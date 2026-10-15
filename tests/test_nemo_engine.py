"""Tests for the pinned NeMo-Speech.cpp engine archive (ticket 57).

Hermetic by construction: the real 16 MB release is **never** fetched. Every
acquisition path goes through an injected fake ``Fetcher`` that writes a
**real gzip tar** built by :func:`_make_archive`, so the extraction seam is
exercised for real — ``tarfile`` opens the archive, ``filter="data"`` runs, the
SONAME symlink and the executable bit are asserted on the extracted files — while
no byte leaves the sandbox.

The pins themselves are asserted as data (literal strings), so a changed pin is a
failing test rather than a silent behaviour change.
"""

from __future__ import annotations

import ast
import hashlib
import io
import os
import stat
import tarfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest

from ges2t import nemo_engine
from ges2t.model_store import ArtifactPin, ArtifactVerificationError
from ges2t.nemo_engine import (
    ARCHIVE_ROOT_NAME,
    ENGINE_DIRECTORY_NAME,
    ENTRY_LIBRARY_RELATIVE_PATH,
    EXECUTABLE_RELATIVE_PATH,
    MANIFEST_NAME,
    NEMO_SPEECH_ARCHIVE_NAME,
    NEMO_SPEECH_PIN,
    NEMO_SPEECH_SHA256,
    NEMO_SPEECH_SIZE,
    NEMO_SPEECH_TAG,
    NEMO_SPEECH_URL,
    NEMO_SPEECH_VERSION,
    EngineArchiveError,
    EngineManifestError,
    NemoDevice,
    NemoEngine,
    NemoEngineError,
    archive_cache_path,
    engine_dir,
    ensure_engine,
    inspect_device,
    read_engine,
    verify_engine,
)
from ges2t.whisper_engine import VULKAN_LIBRARY_NAME

EXECUTABLE_PAYLOAD: bytes = b"\x7fELF fake nemo-speech cli"
ENTRY_LIBRARY_PAYLOAD: bytes = b"\x7fELF fake libnemo_speech_asr_c"
GGML_PAYLOAD: bytes = b"\x7fELF fake libggml-vulkan"


# --- fixtures ------------------------------------------------------------------


class RecordingFetcher:
    """A :data:`~ges2t.model_store.Fetcher` that records its calls and writes bytes."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, Path]] = []

    def __call__(self, url: str, destination: Path) -> None:
        self.calls.append((url, destination))
        destination.write_bytes(self.payload)


class FailingFetcher:
    """A fetcher that writes nothing and fails, like a dropped download."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, url: str, destination: Path) -> None:
        del url, destination
        self.calls += 1
        raise ConnectionError("simulated network drop")


class FakeLibraryProbe:
    """A :class:`~ges2t.whisper_engine.SystemProbe` with only ``library`` meaningful."""

    def __init__(self, libraries: Mapping[str, Path]) -> None:
        self.libraries = dict(libraries)

    def executable(self, name: str) -> Path | None:
        """No executable is configured in these tests."""
        del name
        return None

    def header(self, relative_path: str) -> Path | None:
        """No header is configured in these tests."""
        del relative_path
        return None

    def library(self, name: str) -> Path | None:
        """Return the configured path for ``name``, else ``None``."""
        return self.libraries.get(name)


def _make_archive(
    *,
    root: str = ARCHIVE_ROOT_NAME,
    files: Mapping[str, bytes] | None = None,
    symlinks: Mapping[str, str] | None = None,
    extra_members: tuple[str, ...] = (),
) -> bytes:
    """Return a real gzip tar holding the pinned archive's layout.

    The default layout (both arguments ``None``) is the release's:
    ``bin/nemo-speech`` (executable), ``lib/libnemo_speech_asr_c.so`` as a
    **symlink** to ``…so.1``, and the versioned real file. Passing ``files`` or
    ``symlinks`` replaces that layout *exactly* — an empty mapping means "none of
    them", which is how the missing-entry-library and missing-executable cases
    are built. ``extra_members`` are written verbatim, which is how the
    path-traversal member is built.
    """
    payloads: dict[str, bytes] = (
        {
            EXECUTABLE_RELATIVE_PATH: EXECUTABLE_PAYLOAD,
            "lib/libnemo_speech_asr_c.so.1": ENTRY_LIBRARY_PAYLOAD,
            "lib/libggml-vulkan.so.0": GGML_PAYLOAD,
        }
        if files is None
        else dict(files)
    )
    links: dict[str, str] = (
        {ENTRY_LIBRARY_RELATIVE_PATH: "libnemo_speech_asr_c.so.1"}
        if symlinks is None
        else dict(symlinks)
    )
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as bundle:
        for name, payload in payloads.items():
            info = tarfile.TarInfo(f"{root}/{name}")
            info.size = len(payload)
            info.mode = 0o755 if name == EXECUTABLE_RELATIVE_PATH else 0o644
            bundle.addfile(info, io.BytesIO(payload))
        for name, target in links.items():
            info = tarfile.TarInfo(f"{root}/{name}")
            info.type = tarfile.SYMTYPE
            info.mode = 0o777
            info.linkname = target
            bundle.addfile(info)
        for member in extra_members:
            info = tarfile.TarInfo(member)
            info.size = len(b"x")
            bundle.addfile(info, io.BytesIO(b"x"))
    return buffer.getvalue()


def _pin_for(payload: bytes, *, name: str = NEMO_SPEECH_ARCHIVE_NAME) -> ArtifactPin:
    """Return a synthetic pin describing ``payload`` exactly."""
    return ArtifactPin(
        name=name,
        url=f"https://example.invalid/{name}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


def engine_pin_of(engine: NemoEngine) -> ArtifactPin:
    """Return a pin matching an installed engine's recorded hash and size."""
    return ArtifactPin(
        name=engine.archive,
        url=f"https://example.invalid/{engine.archive}",
        sha256=engine.sha256,
        size=engine.size,
    )


@pytest.fixture()
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point both XDG homes at tmp dirs so nothing leaves the test sandbox."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    return tmp_path


def _temporary_leftovers(root: Path) -> list[Path]:
    """Return any staged tree or ``.part`` file left anywhere under ``root``."""
    return sorted(
        path
        for path in root.rglob("*")
        if path.name.endswith(".part") or path.name.startswith(".")
    )


def _install(sandbox: Path, payload: bytes | None = None) -> NemoEngine:
    """Extract one synthetic archive into ``sandbox/engine`` and return the engine."""
    data = payload if payload is not None else _make_archive()
    return ensure_engine(
        directory=sandbox / "engine",
        pin=_pin_for(data),
        fetcher=RecordingFetcher(data),
    )


# --- pins ----------------------------------------------------------------------


def test_engine_pin_matches_ticket_55() -> None:
    # Literal strings on purpose (rule 8): the values ticket 55 verified against
    # the live GitHub release, not a re-assertion of the module's constants.
    assert NEMO_SPEECH_ARCHIVE_NAME == "nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz"
    assert NEMO_SPEECH_TAG == "v0.2.0"
    assert NEMO_SPEECH_VERSION == "0.2.0"
    assert NEMO_SPEECH_SHA256 == (
        "c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6"
    )
    assert NEMO_SPEECH_SIZE == 16_618_142
    assert NEMO_SPEECH_URL == (
        "https://github.com/NVIDIA/NeMo-Speech.cpp/releases/download/v0.2.0/"
        "nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz"
    )
    assert NEMO_SPEECH_PIN.sha256 == NEMO_SPEECH_SHA256
    assert NEMO_SPEECH_PIN.size == NEMO_SPEECH_SIZE
    assert NEMO_SPEECH_PIN.name == NEMO_SPEECH_ARCHIVE_NAME
    assert NEMO_SPEECH_PIN.member is None


def test_engine_pin_url_names_the_tag_not_a_moving_latest() -> None:
    # ADR 0017: a ``latest`` redirect must never be what is pinned.
    assert NEMO_SPEECH_TAG in NEMO_SPEECH_URL
    assert "/latest/" not in NEMO_SPEECH_URL


# --- locations -----------------------------------------------------------------


def test_engine_dir_is_under_the_data_dir(sandbox: Path) -> None:
    assert engine_dir() == (
        sandbox
        / "data"
        / "ges2t"
        / "engine"
        / ENGINE_DIRECTORY_NAME
        / NEMO_SPEECH_VERSION
    )


def test_engine_dir_honours_an_explicit_version(sandbox: Path) -> None:
    assert engine_dir("9.9.9").name == "9.9.9"


def test_archive_cache_path_is_a_cache_not_the_data_dir(sandbox: Path) -> None:
    assert archive_cache_path() == (
        sandbox / "cache" / "ges2t" / ENGINE_DIRECTORY_NAME / NEMO_SPEECH_VERSION
        / NEMO_SPEECH_ARCHIVE_NAME
    )


def test_archive_cache_falls_back_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert nemo_engine.archive_cache_dir() == (
        tmp_path / ".cache" / "ges2t" / ENGINE_DIRECTORY_NAME / NEMO_SPEECH_VERSION
    )


# --- success path ---------------------------------------------------------------


def test_ensure_engine_extracts_the_pinned_archive(sandbox: Path) -> None:
    directory = sandbox / "engine"
    payload = _make_archive()
    fetcher = RecordingFetcher(payload)

    engine = ensure_engine(
        directory=directory, pin=_pin_for(payload), fetcher=fetcher
    )

    assert engine.directory == directory
    assert engine.version == NEMO_SPEECH_VERSION
    assert engine.tag == NEMO_SPEECH_TAG
    assert engine.entry_library == directory / ENTRY_LIBRARY_RELATIVE_PATH
    assert engine.executable == directory / EXECUTABLE_RELATIVE_PATH
    assert engine.entry_library.read_bytes() == ENTRY_LIBRARY_PAYLOAD
    assert engine.executable.read_bytes() == EXECUTABLE_PAYLOAD
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][0] == _pin_for(payload).url
    # The verified archive is kept in the cache, so a re-extract needs no network.
    assert archive_cache_path(version=engine.version, pin=_pin_for(payload)).read_bytes() == payload
    assert (directory / MANIFEST_NAME).is_file()
    assert _temporary_leftovers(sandbox) == []


def test_extraction_preserves_the_soname_symlink(sandbox: Path) -> None:
    engine = _install(sandbox)

    link = engine.directory / ENTRY_LIBRARY_RELATIVE_PATH
    assert link.is_symlink()
    assert os.readlink(link) == "libnemo_speech_asr_c.so.1"
    assert link.read_bytes() == ENTRY_LIBRARY_PAYLOAD


def test_extraction_preserves_the_executable_bit(sandbox: Path) -> None:
    engine = _install(sandbox)

    mode = engine.executable.stat().st_mode
    assert mode & stat.S_IXUSR, oct(mode)


def test_the_manifest_names_every_extracted_file(sandbox: Path) -> None:
    engine = _install(sandbox)

    manifest = read_engine(engine.directory)
    assert manifest is not None
    assert manifest.sha256 == engine.sha256
    assert manifest.size == engine.size
    assert manifest.archive == engine.archive
    relative = {path.relative_to(engine.directory).as_posix() for path in engine.files}
    assert ENTRY_LIBRARY_RELATIVE_PATH in relative
    assert EXECUTABLE_RELATIVE_PATH in relative
    assert "lib/libnemo_speech_asr_c.so.1" in relative
    # The manifest itself is not claimed as extracted content.
    assert MANIFEST_NAME not in relative


def test_a_complete_install_is_not_refetched(sandbox: Path) -> None:
    directory = sandbox / "engine"
    payload = _make_archive()
    pin = _pin_for(payload)
    ensure_engine(directory=directory, pin=pin, fetcher=RecordingFetcher(payload))
    second = RecordingFetcher(b"")

    engine = ensure_engine(directory=directory, pin=pin, fetcher=second)

    assert engine.directory == directory
    assert second.calls == []


def test_force_reinstalls_over_a_complete_install(sandbox: Path) -> None:
    directory = sandbox / "engine"
    payload = _make_archive()
    pin = _pin_for(payload)
    ensure_engine(directory=directory, pin=pin, fetcher=RecordingFetcher(payload))
    # Remove a file by hand: the tree is no longer complete, so a plain re-run
    # already re-extracts from the cached archive.
    (directory / EXECUTABLE_RELATIVE_PATH).unlink()

    engine = ensure_engine(directory=directory, pin=pin, fetcher=RecordingFetcher(b""))

    assert engine.executable.is_file()


def test_a_tampered_but_complete_install_is_left_alone_without_force(
    sandbox: Path,
) -> None:
    directory = sandbox / "engine"
    payload = _make_archive()
    pin = _pin_for(payload)
    ensure_engine(directory=directory, pin=pin, fetcher=RecordingFetcher(payload))
    stray = directory / "stray.txt"
    stray.write_text("an extra file the manifest does not claim", encoding="utf-8")
    fetcher = RecordingFetcher(b"")

    ensure_engine(directory=directory, pin=pin, fetcher=fetcher)

    assert stray.exists()  # a complete install is trusted, not re-extracted
    assert fetcher.calls == []


def test_force_re_extracts_even_when_complete(sandbox: Path) -> None:
    directory = sandbox / "engine"
    payload = _make_archive()
    pin = _pin_for(payload)
    ensure_engine(directory=directory, pin=pin, fetcher=RecordingFetcher(payload))
    stray = directory / "stray.txt"
    stray.write_text("an extra file the manifest does not claim", encoding="utf-8")
    fetcher = RecordingFetcher(b"")

    engine = ensure_engine(
        directory=directory, pin=pin, force=True, fetcher=fetcher
    )

    assert engine.directory == directory
    assert not stray.exists()  # the tree was replaced wholesale
    assert engine.executable.is_file()
    # ``ensure_artifact`` trusts a cache hit by pinned byte size alone, so the
    # forced re-extract reuses the archive downloaded above, fully offline.
    assert fetcher.calls == []
    assert _temporary_leftovers(sandbox) == []


def test_progress_reports_start_and_completion(sandbox: Path) -> None:
    fractions: list[float] = []
    payload = _make_archive()

    ensure_engine(
        directory=sandbox / "engine",
        pin=_pin_for(payload),
        fetcher=RecordingFetcher(payload),
        progress=fractions.append,
    )

    assert fractions[0] == 0.0
    assert fractions[-1] == 1.0
    assert all(0.0 <= value <= 1.0 for value in fractions)


# --- failure paths --------------------------------------------------------------


def test_wrong_hash_archive_fails_and_leaves_nothing(sandbox: Path) -> None:
    directory = sandbox / "engine"

    with pytest.raises(ArtifactVerificationError) as excinfo:
        ensure_engine(
            directory=directory,
            pin=NEMO_SPEECH_PIN,  # the real pin, so a real hash mismatch is tested
            fetcher=RecordingFetcher(b"not the pinned archive"),
        )

    assert NEMO_SPEECH_SHA256 in str(excinfo.value)
    assert not directory.exists()
    assert _temporary_leftovers(sandbox) == []


def test_wrong_size_archive_fails_and_leaves_nothing(sandbox: Path) -> None:
    directory = sandbox / "engine"
    payload = _make_archive()

    with pytest.raises(ArtifactVerificationError):
        ensure_engine(
            directory=directory,
            pin=_pin_for(payload[:-1]),  # a size (and hash) the payload cannot match
            fetcher=RecordingFetcher(payload),
        )

    assert not directory.exists()
    assert _temporary_leftovers(sandbox) == []


def test_ensure_engine_checks_the_archive_size_alone(sandbox: Path) -> None:
    # A pin whose sha256 is the archive's but whose *size* is wrong: only the
    # size check can reject this, so the pair really is checked, not just the hash.
    payload = _make_archive()
    pin = ArtifactPin(
        name=NEMO_SPEECH_ARCHIVE_NAME,
        url="https://example.invalid/archive.tar.gz",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload) + 1,
    )

    with pytest.raises(ArtifactVerificationError):
        ensure_engine(
            directory=sandbox / "engine",
            pin=pin,
            fetcher=RecordingFetcher(payload),
        )

    assert not (sandbox / "engine").exists()
    assert _temporary_leftovers(sandbox) == []


def test_a_dropped_connection_leaves_nothing(sandbox: Path) -> None:
    directory = sandbox / "engine"
    fetcher = FailingFetcher()

    with pytest.raises(ConnectionError):
        ensure_engine(
            directory=directory, pin=NEMO_SPEECH_PIN, fetcher=fetcher
        )

    assert fetcher.calls == 1
    assert not directory.exists()
    assert _temporary_leftovers(sandbox) == []


def test_an_archive_without_the_engine_root_is_a_typed_error(sandbox: Path) -> None:
    directory = sandbox / "engine"
    payload = _make_archive(root="some-other-directory")

    with pytest.raises(EngineArchiveError) as excinfo:
        ensure_engine(
            directory=directory, pin=_pin_for(payload), fetcher=RecordingFetcher(payload)
        )

    assert "some-other-directory" in str(excinfo.value)
    assert not directory.exists()
    assert _temporary_leftovers(sandbox) == []


def test_an_archive_without_the_entry_library_is_a_typed_error(sandbox: Path) -> None:
    payload = _make_archive(
        files={EXECUTABLE_RELATIVE_PATH: EXECUTABLE_PAYLOAD},
        symlinks={},
    )

    with pytest.raises(EngineArchiveError) as excinfo:
        ensure_engine(
            directory=sandbox / "engine",
            pin=_pin_for(payload),
            fetcher=RecordingFetcher(payload),
        )

    assert ENTRY_LIBRARY_RELATIVE_PATH in str(excinfo.value)
    assert not (sandbox / "engine").exists()


def test_an_archive_without_the_executable_is_a_typed_error(sandbox: Path) -> None:
    payload = _make_archive(
        files={"lib/libnemo_speech_asr_c.so.1": ENTRY_LIBRARY_PAYLOAD},
        symlinks={ENTRY_LIBRARY_RELATIVE_PATH: "libnemo_speech_asr_c.so.1"},
    )

    with pytest.raises(EngineArchiveError) as excinfo:
        ensure_engine(
            directory=sandbox / "engine",
            pin=_pin_for(payload),
            fetcher=RecordingFetcher(payload),
        )

    assert EXECUTABLE_RELATIVE_PATH in str(excinfo.value)
    assert not (sandbox / "engine").exists()


def test_a_path_traversal_member_is_refused(sandbox: Path) -> None:
    directory = sandbox / "engine"
    payload = _make_archive(extra_members=("../escaped.txt",))

    with pytest.raises(EngineArchiveError):
        ensure_engine(
            directory=directory, pin=_pin_for(payload), fetcher=RecordingFetcher(payload)
        )

    assert not (sandbox / "escaped.txt").exists()
    assert not directory.exists()
    assert _temporary_leftovers(sandbox) == []


def test_a_not_a_tarball_payload_is_a_typed_error(sandbox: Path) -> None:
    # Hash-verified but not a gzip tar: the pin cannot catch this, the extractor
    # must, and it must not leave a staging tree behind.
    payload = b"definitely not a tar archive"

    with pytest.raises(EngineArchiveError):
        ensure_engine(
            directory=sandbox / "engine",
            pin=_pin_for(payload),
            fetcher=RecordingFetcher(payload),
        )

    assert not (sandbox / "engine").exists()
    assert _temporary_leftovers(sandbox) == []


def test_a_failed_reinstall_keeps_the_previous_install(sandbox: Path) -> None:
    directory = sandbox / "engine"
    good = _make_archive()
    ensure_engine(directory=directory, pin=_pin_for(good), fetcher=RecordingFetcher(good))
    original = (directory / EXECUTABLE_RELATIVE_PATH).read_bytes()

    with pytest.raises(ArtifactVerificationError):
        # The installed tree is of a *different* pin, so this re-extracts; the
        # fetch fails its hash check first and the tree must survive untouched.
        ensure_engine(
            directory=directory,
            pin=NEMO_SPEECH_PIN,
            fetcher=RecordingFetcher(b"a different archive"),
        )

    assert (directory / EXECUTABLE_RELATIVE_PATH).read_bytes() == original
    assert verify_engine(directory, pin=_pin_for(good)) is not None
    assert _temporary_leftovers(sandbox) == []


# --- verify_engine / read_engine ------------------------------------------------


def test_verify_engine_returns_none_for_an_absent_directory(sandbox: Path) -> None:
    assert verify_engine(sandbox / "nothing-here") is None


def test_verify_engine_returns_none_without_a_manifest(sandbox: Path) -> None:
    directory = sandbox / "engine"
    directory.mkdir(parents=True)
    assert verify_engine(directory) is None


def test_verify_engine_returns_none_when_a_file_was_deleted(sandbox: Path) -> None:
    engine = _install(sandbox)
    (engine.directory / EXECUTABLE_RELATIVE_PATH).unlink()

    assert verify_engine(engine.directory, pin=engine_pin_of(engine)) is None


def test_verify_engine_returns_none_for_a_different_pin(sandbox: Path) -> None:
    engine = _install(sandbox)

    # Same byte size, different sha256: only the hash comparison can reject this,
    # so the test isolates the sha check rather than the size check.
    other = ArtifactPin(
        name=engine.archive,
        url="https://example.invalid/other",
        sha256="0" * 64,
        size=engine.size,
    )

    assert verify_engine(engine.directory, pin=other) is None
    assert verify_engine(engine.directory, pin=engine_pin_of(engine)) is not None


def test_verify_engine_returns_none_for_a_corrupt_manifest(sandbox: Path) -> None:
    engine = _install(sandbox)
    (engine.directory / MANIFEST_NAME).write_text("{not json", encoding="utf-8")

    assert verify_engine(engine.directory, pin=engine_pin_of(engine)) is None


def test_read_engine_reports_a_corrupt_manifest_loudly(sandbox: Path) -> None:
    engine = _install(sandbox)
    (engine.directory / MANIFEST_NAME).write_text("[]", encoding="utf-8")

    with pytest.raises(EngineManifestError):
        read_engine(engine.directory)


def test_read_engine_returns_none_when_there_is_no_manifest(sandbox: Path) -> None:
    directory = sandbox / "engine"
    directory.mkdir(parents=True)
    assert read_engine(directory) is None


# --- device detection -----------------------------------------------------------


def test_inspect_device_reports_vulkan_when_the_loader_is_present() -> None:
    probe = FakeLibraryProbe({VULKAN_LIBRARY_NAME: Path("/usr/lib/libvulkan.so.1")})

    device = inspect_device(probe)

    assert device.vulkan_available is True
    assert device.backend == "Vulkan"
    assert "Vulkan" in device.summary


def test_inspect_device_reports_cpu_when_the_loader_is_absent() -> None:
    device = inspect_device(FakeLibraryProbe({}))

    assert device.vulkan_available is False
    assert device.backend == "CPU"
    assert "CPU-only" in device.summary


def test_nemo_device_is_a_record_not_a_tuple() -> None:
    device = NemoDevice(vulkan_library=None)
    assert device == NemoDevice(vulkan_library=None)


# --- the "no build, no sudo" guarantee ------------------------------------------

#: Programs this module must **never** invoke or name as a command. The archive is
#: prebuilt (ticket 57's DoD): a build tool or an escalation tool appearing here
#: would mean the ticket's "no build, no sudo" promise had been broken.
_FORBIDDEN_PROGRAMS: frozenset[str] = frozenset(
    {"sudo", "pacman", "cmake", "ninja", "make", "sentencepiece", "pip", "meson"}
)

#: Modules that would let this file run a process or reach the network.
_FORBIDDEN_MODULES: frozenset[str] = frozenset(
    {"subprocess", "urllib", "http", "socket", "requests", "httpx"}
)

#: Call/attribute names that spawn a process **without** importing
#: :mod:`subprocess`: ``os.system``, the ``os.exec*`` family, ``os.popen`` and the
#: ``os.spawn*`` family. The module already imports ``os`` for ``os.rename`` and
#: ``os.replace``, so forbidding the import is not an option — the *call* has to
#: be named. This closes the hole where ``os.system("ninja -C build")`` carries
#: the build tool inside one string constant that the exact-name scan below would
#: not match.
_FORBIDDEN_PROCESS_CALLS: frozenset[str] = frozenset(
    {
        "system",
        "popen",
        "execv",
        "execve",
        "execvp",
        "execvpe",
        "execl",
        "execlp",
        "execle",
        "spawnl",
        "spawnle",
        "spawnlp",
        "spawnv",
        "spawnve",
        "spawnvp",
        "spawnvpe",
    }
)


@dataclass(frozen=True)
class _CommandScan:
    """What a static scan of one module found that could run or name a command."""

    imported_modules: frozenset[str]
    forbidden_programs: frozenset[str]
    process_calls: frozenset[str]


def _scan_for_commands(source: str) -> _CommandScan:
    """Scan ``source`` for process-spawning imports, calls and build-tool names.

    A pure function of the source text, so a test can feed it a **mutated**
    module and prove the guard is not vacuous.

    ``process_calls`` is drawn from attribute names (so ``os.system`` is seen
    wherever it is referenced) and from the callee of a call (so a bare
    ``system(...)`` after ``from os import system`` is seen too). A bare name
    that is only a parameter — the module's ``inspect_device(system=…)`` — is
    deliberately not a finding.
    """
    tree = ast.parse(source)
    imported: set[str] = set()
    constants: set[str] = set()
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            constants.add(node.value)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                referenced.add(func.id)
    return _CommandScan(
        imported_modules=frozenset(imported),
        forbidden_programs=frozenset(constants & _FORBIDDEN_PROGRAMS),
        process_calls=frozenset(referenced & _FORBIDDEN_PROCESS_CALLS),
    )


def _engine_source() -> str:
    """Return the real ``nemo_engine`` source text."""
    return Path(nemo_engine.__file__).read_text(encoding="utf-8")


def test_the_engine_module_runs_no_external_command_and_installs_nothing() -> None:
    """A static guard: no process, no network, no build tool named anywhere.

    Scans the **AST**, not the prose: the module's docstring legitimately says the
    word "sudo", but no string *constant* may be a program name, no import may
    open a process or a socket, and no call may be ``os.system``/``os.exec*``/
    ``os.popen``/``os.spawn*``.

    Scope, stated honestly: this is a **static** scan of names, not a runtime
    guard. It catches a process-spawning *import* (``subprocess``) and a
    process-spawning *call* by name, including the ``os.system("ninja -C build")``
    shape whose command line hides inside one string. The residual hole is an
    **indirect** invocation a name scan cannot follow — ``getattr(os, "system")``,
    a process reached through a helper or an alias, or a dynamic import whose
    module name is not a literal. The realistic failures (``import subprocess``,
    a named ``os.system``/``os.exec*``) are caught.
    """
    scan = _scan_for_commands(_engine_source())

    assert scan.imported_modules & _FORBIDDEN_MODULES == set(), (
        scan.imported_modules & _FORBIDDEN_MODULES
    )
    assert scan.forbidden_programs == frozenset(), scan.forbidden_programs
    assert scan.process_calls == frozenset(), scan.process_calls


@pytest.mark.parametrize(
    "spawn",
    [
        "os.system('ninja -C build')",
        "os.execv('/bin/sh', ['sh'])",
        "os.popen('cmake .')",
        "os.spawnvp(os.P_WAIT, 'make', ['make'])",
    ],
)
def test_the_static_guard_fails_on_a_process_spawning_call(spawn: str) -> None:
    """Falsify the guard: a named ``os.system``/``os.exec*``/… call is reported."""
    scan = _scan_for_commands(_engine_source() + f"\nimport os\n{spawn}\n")

    assert scan.process_calls != frozenset(), scan.process_calls


def test_the_static_guard_fails_on_a_subprocess_import() -> None:
    """Falsify the guard: ``import subprocess`` is reported."""
    scan = _scan_for_commands(_engine_source() + "\nimport subprocess\n")

    assert scan.imported_modules & _FORBIDDEN_MODULES == {"subprocess"}


def test_the_static_guard_does_not_flag_a_parameter_named_system() -> None:
    """A bare name used as a parameter or keyword argument is not a process call.

    The real module has ``inspect_device(system: SystemProbe | None = None)``;
    only attributed access (``os.system``) or a bare *callee* is a finding.
    """
    scan = _scan_for_commands(
        "def inspect_device(system=None):\n    return probe(system=system)\n"
    )

    assert scan.process_calls == frozenset(), scan.process_calls
