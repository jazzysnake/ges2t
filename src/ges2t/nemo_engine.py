"""NeMo-Speech.cpp engine acquisition: fetch, verify and extract the prebuilt archive.

The in-process shape of the NeMo streaming backend (ADR 0022) needs the prebuilt
**release archive** of `NVIDIA/NeMo-Speech.cpp <https://github.com/NVIDIA/NeMo-Speech.cpp>`_
— ``bin/nemo-speech`` and the shared library set, including
``lib/libnemo_speech_asr_c.so``, which the ``ctypes`` adapter of ticket 58 loads.
This module is the one place that produces that tree, and it mirrors
:mod:`ges2t.whisper_engine`'s layout and discipline while doing **strictly less**
than it:

* **Nothing is built and nothing is installed.** The archive is a prebuilt,
  pinned binary distribution: there is no compiler step, no ``cmake``, no
  ``ninja``, no ``sentencepiece``, and no package manager. ``ges2t setup``
  *requires*, never installs (ADR 0017, ADR 0022 §8); this module never runs
  ``sudo`` and never invokes a build tool. The single prerequisite is the
  pinned archive itself.
* **The archive is pinned by sha256 *and* byte size** (ADR 0017). A moving
  ``releases/latest`` URL is never used: :data:`NEMO_SPEECH_URL` names the tag and
  the sha256 and size below describe exactly the bytes it serves, verified by
  ticket 55 against the live host.
* **Network access is not implemented here.** The archive is fetched through
  :func:`ges2t.model_store.ensure_artifact`, so the one download implementation
  (and its redirect handling, progress reporting and sha256+size verification)
  stays in the module ADR 0018 designates as the only networked code. This module
  imports no HTTP client at all, which
  ``tests/test_model_store.py::test_network_clients_are_confined_to_the_model_store``
  checks statically.
* **The install is atomic and self-describing.** The archive is extracted into a
  staging directory *beside* the install directory, hashed-verified before a
  single byte is extracted, and swapped into place only once complete — so a
  wrong-hash archive or a failed extraction leaves the previous engine exactly as
  it was and never a half-written tree where :func:`verify_engine` looks. An
  ``engine.json`` manifest names the archive, its sha256 and size, and every
  extracted file, so the installed tree can be re-verified without the archive.
* **Device detection is a host property, not an artifact property.** Unlike the
  whisper.cpp build, this archive is one artifact that runs on Vulkan **or** CPU
  (the engine's own ``doctor`` lists both devices), so the effective device is
  decided by the *host*: :func:`inspect_device` reports the Vulkan loader's
  presence and the fallback, and the record of an installed engine deliberately
  carries **no** device — a detected-at-install-time device would go stale the
  moment a driver changed.

The module imports only the standard library plus
:mod:`ges2t.model_store` (the pins, the shared acquisition path and the data
directory) and the generic host probe from :mod:`ges2t.whisper_engine`, so the
Vulkan loader's name and the "does this machine have Vulkan" question have
exactly one definition in the project. It never imports the config layer, the
CLI, or the backend adapter.
"""

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ges2t.model_store import (
    ArtifactPin,
    Fetcher,
    ProgressCallback,
    default_data_dir,
    ensure_artifact,
)
from ges2t.whisper_engine import (
    HostSystemProbe,
    SystemProbe,
    VULKAN_LIBRARY_NAME,
)

__all__ = [
    "ARCHIVE_ROOT_NAME",
    "ENGINE_DIRECTORY_NAME",
    "ENTRY_LIBRARY_NAME",
    "ENTRY_LIBRARY_RELATIVE_PATH",
    "EXECUTABLE_NAME",
    "EXECUTABLE_RELATIVE_PATH",
    "MANIFEST_NAME",
    "NEMO_SPEECH_ARCHIVE_NAME",
    "NEMO_SPEECH_PIN",
    "NEMO_SPEECH_REPOSITORY",
    "NEMO_SPEECH_SHA256",
    "NEMO_SPEECH_SIZE",
    "NEMO_SPEECH_TAG",
    "NEMO_SPEECH_URL",
    "NEMO_SPEECH_VERSION",
    "EngineArchiveError",
    "EngineManifestError",
    "NemoDevice",
    "NemoEngine",
    "NemoEngineError",
    "archive_cache_dir",
    "archive_cache_path",
    "engine_dir",
    "ensure_engine",
    "inspect_device",
    "manifest_path",
    "read_engine",
    "verify_engine",
]

# --- The pinned engine archive (ADR 0017, ADR 0022 §8) -------------------------

#: Upstream repository. Never fetched from directly: the archive below is.
NEMO_SPEECH_REPOSITORY: str = "https://github.com/NVIDIA/NeMo-Speech.cpp"

#: The release tag the pinned archive belongs to.
NEMO_SPEECH_TAG: str = "v0.2.0"

#: Version directory under ``engine/nemo-speech/``; the release version, so the
#: installation path names exactly what is installed.
NEMO_SPEECH_VERSION: str = "0.2.0"

#: The prebuilt Linux x86-64 archive **with the Vulkan backend compiled in** (it
#: falls back to CPU at runtime). The tag is in the URL: no ``latest`` redirect is
#: ever followed into a pin (ADR 0017).
NEMO_SPEECH_ARCHIVE_NAME: str = "nemo-speech-0.2.0-linux-x86_64-vulkan.tar.gz"

#: The archive URL, assembled from the tag and the file name so the tag cannot be
#: updated in one place and not the other.
NEMO_SPEECH_URL: str = (
    f"{NEMO_SPEECH_REPOSITORY}/releases/download/{NEMO_SPEECH_TAG}/"
    f"{NEMO_SPEECH_ARCHIVE_NAME}"
)

#: sha256 of the pinned archive, and its exact byte size — the pair ADR 0017
#: requires, measured against the live release by ticket 55.
NEMO_SPEECH_SHA256: str = (
    "c8d0ed5b9ac5fb6d5c460015ba38ea217415c44edc1e6d49120646781009aee6"
)
NEMO_SPEECH_SIZE: int = 16_618_142

#: The engine pin, as the shared :class:`~ges2t.model_store.ArtifactPin` every
#: acquisition in ges2t uses.
NEMO_SPEECH_PIN: ArtifactPin = ArtifactPin(
    name=NEMO_SPEECH_ARCHIVE_NAME,
    url=NEMO_SPEECH_URL,
    sha256=NEMO_SPEECH_SHA256,
    size=NEMO_SPEECH_SIZE,
)

#: Directory name under the ges2t data dir's ``engine/`` directory.
ENGINE_DIRECTORY_NAME: str = "nemo-speech"

#: The single top-level directory inside the archive (verified by ticket 55's own
#: ``tar xzf … -C engine``, which produced exactly this directory). The installed
#: version directory **is** this directory, so paths match upstream's layout.
ARCHIVE_ROOT_NAME: str = "nemo-speech-0.2.0-linux-x86_64-vulkan"

#: The installed manifest naming the archive, its hash and every extracted file.
MANIFEST_NAME: str = "engine.json"

#: The library the adapter loads, relative to the engine directory. The
#: unversioned SONAME symlink: the loader resolves ``.so.1`` through it.
ENTRY_LIBRARY_NAME: str = "libnemo_speech_asr_c.so"
ENTRY_LIBRARY_RELATIVE_PATH: str = f"lib/{ENTRY_LIBRARY_NAME}"

#: The engine's own CLI, relative to the engine directory. ``doctor`` runs
#: ``nemo-speech doctor`` and the release notes' ``model_pull`` feature uses it;
#: acquisition needs it only as a completeness check.
EXECUTABLE_NAME: str = "nemo-speech"
EXECUTABLE_RELATIVE_PATH: str = f"bin/{EXECUTABLE_NAME}"


# --- Errors --------------------------------------------------------------------


class NemoEngineError(Exception):
    """Base class for every failure raised by this module."""


class EngineArchiveError(NemoEngineError):
    """The verified archive could not be read, or does not hold the engine tree.

    Raised *after* the archive has been hash-verified, for the two cases the pin
    cannot catch: a container that is not a readable gzip tar, and one whose
    top-level directory is not :data:`ARCHIVE_ROOT_NAME`.
    """

    def __init__(self, archive: Path, reason: str) -> None:
        self.archive = archive
        self.reason = reason
        super().__init__(f"{archive}: {reason}")


class EngineManifestError(NemoEngineError):
    """The installed engine manifest exists but cannot be read back."""


# --- Records -------------------------------------------------------------------


@dataclass(frozen=True)
class NemoDevice:
    """Which device the prebuilt engine will use on this host (ADR 0022 §7).

    The archive ships one binary with both backends, so the device is a property
    of the **machine**, not of the installed artifact: Vulkan when a Vulkan
    loader is present, CPU otherwise. This is deliberately a *detection* record
    and never baked into the installed engine's manifest — a device detected at
    setup time would be stale the moment a driver or an ICD package changed, and
    ``doctor`` (ticket 61) must report what is true **now**.

    The engine enumerates and names the actual GPUs itself (``nemo-speech
    doctor``); probing for the loader is the host-side question ges2t can answer
    without running the engine, and it is the same probe the whisper.cpp engine
    uses to choose its variant, so "is Vulkan available" has one answer in the
    project.
    """

    vulkan_library: Path | None

    @property
    def vulkan_available(self) -> bool:
        """Whether a Vulkan loader was found, i.e. the engine can use the GPU."""
        return self.vulkan_library is not None

    @property
    def backend(self) -> str:
        """``"Vulkan"`` or ``"CPU"``: the backend the engine will select."""
        return "Vulkan" if self.vulkan_available else "CPU"

    @property
    def summary(self) -> str:
        """One line fit for ``doctor`` and setup output."""
        if self.vulkan_available:
            return f"Vulkan (loader: {self.vulkan_library})"
        return "CPU-only (no Vulkan loader found)"


@dataclass(frozen=True)
class NemoEngine:
    """One extracted engine: what archive produced it, and where it lives."""

    version: str
    tag: str
    archive: str
    sha256: str
    size: int
    directory: Path
    entry_library: Path
    executable: Path
    files: tuple[Path, ...]
    extracted_at: str

    @property
    def summary(self) -> str:
        """One line fit for ``doctor`` and setup output."""
        return f"NeMo-Speech.cpp {self.tag} at {self.directory}"

    def missing(self) -> tuple[Path, ...]:
        """The extracted files that are not present on disk."""
        return tuple(path for path in self.files if not path.exists())

    def complete(self) -> bool:
        """Whether every extracted file named by the manifest is present."""
        return not self.missing()


# --- Locations -----------------------------------------------------------------


def engine_dir(version: str = NEMO_SPEECH_VERSION) -> Path:
    """Return ``<data_dir>/engine/nemo-speech/<version>/``.

    A product directory (like ``engine/whisper-cpp/``) rather than Needle's
    ``engine/<version>/``, because the engine is a whole distribution and not one
    ``.so``. The version directory **is** the archive's top-level directory, so
    the layout inside matches upstream's release exactly.
    """
    return default_data_dir() / "engine" / ENGINE_DIRECTORY_NAME / version


def manifest_path(directory: Path) -> Path:
    """Return the manifest path inside an extracted engine directory."""
    return directory / MANIFEST_NAME


def archive_cache_dir(version: str = NEMO_SPEECH_VERSION) -> Path:
    """Return the scratch directory the pinned archive is kept under.

    ``$XDG_CACHE_HOME/ges2t/nemo-speech/<version>`` (default
    ``~/.cache/ges2t/...``). A **cache**, not the data directory: the archive is
    an intermediate, and a failed install must not be mistaken for an installed
    engine. It is deliberately kept after a successful extraction (16 MB), so a
    re-run that finds a broken engine tree can be completed **offline** from the
    already-verified archive.
    """
    raw_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(raw_cache) if raw_cache else Path.home() / ".cache"
    return base / "ges2t" / ENGINE_DIRECTORY_NAME / version


def archive_cache_path(
    version: str = NEMO_SPEECH_VERSION,
    pin: ArtifactPin = NEMO_SPEECH_PIN,
) -> Path:
    """Return where the pinned archive is cached: ``<cache dir>/<archive name>``."""
    return archive_cache_dir(version) / pin.name


# --- Device detection ----------------------------------------------------------


def inspect_device(system: SystemProbe | None = None) -> NemoDevice:
    """Detect which backend the prebuilt engine will use on this host.

    ``system`` is the test seam; the default is the real host probe. The probe
    object and the Vulkan loader's name come from :mod:`ges2t.whisper_engine`
    (:class:`~ges2t.whisper_engine.SystemProbe`,
    :data:`~ges2t.whisper_engine.VULKAN_LIBRARY_NAME`), so "where is
    ``libvulkan.so``" has exactly one definition in the project and the two
    engines cannot disagree about whether this machine has Vulkan.
    """
    probe = system if system is not None else HostSystemProbe()
    return NemoDevice(vulkan_library=probe.library(VULKAN_LIBRARY_NAME))


# --- Manifest ------------------------------------------------------------------


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string (second resolution)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _relative_names(root: Path) -> tuple[str, ...]:
    """Return every regular file and symlink under ``root``, as sorted posix names.

    Directories are not listed: they are implied by the file paths, and a
    directory that vanished would take a file with it. ``os.walk`` does not
    follow symlinks, so a symlinked directory is neither descended nor recorded
    — the pinned archive has none, and recording one would make completeness
    depend on where it points.
    """
    names: list[str] = []
    for directory, _, file_names in os.walk(root):
        base = Path(directory)
        for name in file_names:
            names.append((base / name).relative_to(root).as_posix())
    return tuple(sorted(names))


def _engine_mapping(engine: NemoEngine) -> dict[str, object]:
    """Convert ``engine`` to the JSON-ready manifest mapping (relative names)."""
    return {
        "version": engine.version,
        "tag": engine.tag,
        "archive": engine.archive,
        "sha256": engine.sha256,
        "size": engine.size,
        "extracted_at": engine.extracted_at,
        "entry_library": engine.entry_library.relative_to(engine.directory).as_posix(),
        "executable": engine.executable.relative_to(engine.directory).as_posix(),
        "files": [
            path.relative_to(engine.directory).as_posix() for path in engine.files
        ],
    }


def _required(source: Mapping[str, object], key: str) -> object:
    """Return ``source[key]`` or raise :class:`EngineManifestError`."""
    if key not in source:
        raise EngineManifestError(f"engine manifest: {key!r} is missing")
    return source[key]


def _required_text(source: Mapping[str, object], key: str) -> str:
    """Return ``source[key]`` as a ``str``, or raise :class:`EngineManifestError`."""
    value = _required(source, key)
    if not isinstance(value, str):
        raise EngineManifestError(f"engine manifest: {key!r} must be a string")
    return value


def _required_size(source: Mapping[str, object], key: str) -> int:
    """Return ``source[key]`` as an ``int``, or raise :class:`EngineManifestError`."""
    value = _required(source, key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise EngineManifestError(f"engine manifest: {key!r} must be an integer")
    return value


def _text_tuple(source: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Return ``source[key]`` as a tuple of strings, or raise."""
    value = _required(source, key)
    if not isinstance(value, list):
        raise EngineManifestError(f"engine manifest: {key!r} must be a list")
    names: list[str] = []
    for entry in value:
        if not isinstance(entry, str):
            raise EngineManifestError(
                f"engine manifest: every entry of {key!r} must be a string"
            )
        names.append(entry)
    return tuple(names)


def _engine_from_mapping(directory: Path, source: Mapping[str, object]) -> NemoEngine:
    """Rebuild a :class:`NemoEngine` from a manifest mapping."""
    files = tuple(directory / name for name in _text_tuple(source, "files"))
    return NemoEngine(
        version=_required_text(source, "version"),
        tag=_required_text(source, "tag"),
        archive=_required_text(source, "archive"),
        sha256=_required_text(source, "sha256"),
        size=_required_size(source, "size"),
        directory=directory,
        entry_library=directory / _required_text(source, "entry_library"),
        executable=directory / _required_text(source, "executable"),
        files=files,
        extracted_at=_required_text(source, "extracted_at"),
    )


def _write_manifest(engine: NemoEngine) -> Path:
    """Write ``engine``'s manifest atomically and return its path.

    Temp file plus ``os.replace`` in the same directory, mirroring
    :mod:`ges2t.model_store`'s install: a reader never sees a half-written
    manifest.
    """
    destination = manifest_path(engine.directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_engine_mapping(engine), indent=2, sort_keys=True) + "\n"
    handle, name = tempfile.mkstemp(
        prefix=f".{MANIFEST_NAME}.",
        suffix=".part",
        dir=destination.parent,
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, destination)
    except BaseException:
        _remove_file(temporary)
        raise
    return destination


def read_engine(directory: Path) -> NemoEngine | None:
    """Read the installed manifest; ``None`` when there is none.

    Raises :class:`EngineManifestError` when the file exists but is not a valid
    manifest, so ``doctor`` can report genuine corruption instead of pretending
    the engine is simply absent.
    """
    path = manifest_path(directory)
    if not path.is_file():
        return None
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineManifestError(
            f"{path}: cannot read the engine manifest: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise EngineManifestError(f"{path}: the engine manifest is not an object")
    return _engine_from_mapping(directory, raw)


def verify_engine(
    directory: Path,
    *,
    pin: ArtifactPin | None = None,
) -> NemoEngine | None:
    """Return the extracted engine when it is complete, else ``None``.

    "Complete" means: a readable manifest, whose recorded sha256 and byte size
    match ``pin`` (the pinned archive, ``NEMO_SPEECH_PIN`` by default), naming the
    entry library and the executable, with every file it lists still on disk.

    The pin comparison is what makes a **re-pin** take effect: if the pinned
    archive changes, an installation of the previous archive no longer verifies
    and :func:`ensure_engine` re-extracts instead of trusting a tree that is not
    the pinned bytes. Anything else — absent directory, absent or corrupt
    manifest, a file deleted by hand — is ``None`` too. ``pin`` is the test seam;
    production callers use the default.
    """
    expected = pin if pin is not None else NEMO_SPEECH_PIN
    try:
        engine = read_engine(directory)
    except EngineManifestError:
        return None
    if engine is None or not engine.files:
        return None
    if engine.sha256 != expected.sha256 or engine.size != expected.size:
        return None
    if engine.entry_library not in engine.files:
        return None
    if engine.executable not in engine.files:
        return None
    if not engine.complete():
        return None
    return engine


# --- Extraction ----------------------------------------------------------------


def _remove_file(path: Path) -> None:
    """Delete ``path`` if it exists."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _remove_tree(path: Path) -> None:
    """Delete the directory tree at ``path``, ignoring an absent one."""
    shutil.rmtree(path, ignore_errors=True)


def _extract(archive: Path, staging: Path) -> Path:
    """Extract ``archive`` into ``staging`` and return the engine root inside it.

    ``filter="data"`` is mandatory here rather than cosmetic: it refuses absolute
    member names, ``..`` components, links that escape the destination and
    special files, so a tampered *container* (the hash of the archive was checked
    before this runs, but defence in depth is free) cannot write outside
    ``staging``. It preserves the executable bit and the SONAME symlinks the
    engine's own loader needs.
    """
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            bundle.extractall(staging, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise EngineArchiveError(archive, f"cannot extract: {exc}") from exc

    root = staging / ARCHIVE_ROOT_NAME
    if not root.is_dir():
        raise EngineArchiveError(
            archive,
            f"the archive has no {ARCHIVE_ROOT_NAME!r} directory "
            f"(found: {', '.join(sorted(entry.name for entry in staging.iterdir()))})",
        )
    return root


def _staging_directory(destination: Path) -> Path:
    """Create and return a fresh staging directory **beside** ``destination``.

    A sibling (same parent, hence the same filesystem) is what makes the final
    ``os.rename`` into place atomic and cheap, and keeps a transient,
    half-extracted tree out of the install path entirely. Same discipline as
    :mod:`ges2t.whisper_engine`'s build staging (ticket 49); the helpers are
    duplicated here rather than imported because that module's are private to its
    build path, and lifting them into a shared module is a separate refactor.
    """
    return Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.staging-",
            dir=destination.parent,
        )
    )


def _replace_directory(source: Path, destination: Path) -> None:
    """Move ``source`` onto ``destination``, restoring the old tree on failure.

    ``os.rename`` cannot replace a non-empty directory, so an existing install is
    moved aside first and removed only once the new one is in place. If the new
    tree cannot be moved in, the previous one is restored: a failed re-extraction
    leaves the engine exactly as it was, never a mixed tree the manifest would
    describe (ticket 49's rule, kept here).
    """
    backup = destination.with_name(f".{destination.name}.old")
    _remove_tree(backup)
    moved_backup = False
    if destination.exists() or destination.is_symlink():
        os.rename(destination, backup)
        moved_backup = True
    try:
        os.rename(source, destination)
    except BaseException:
        if moved_backup:
            _remove_tree(destination)
            os.rename(backup, destination)
        raise
    _remove_tree(backup)


# --- Orchestration -------------------------------------------------------------


def _report(progress: ProgressCallback | None, fraction: float) -> None:
    """Forward the completed fraction when a callback was supplied.

    A broken progress sink must never abort an otherwise successful install, so
    callback exceptions are swallowed (``KeyboardInterrupt``/``SystemExit`` still
    propagate). Same contract as :mod:`ges2t.model_store`.
    """
    if progress is not None:
        try:
            progress(fraction)
        except Exception:
            pass


def ensure_engine(
    *,
    version: str = NEMO_SPEECH_VERSION,
    tag: str = NEMO_SPEECH_TAG,
    pin: ArtifactPin = NEMO_SPEECH_PIN,
    directory: Path | None = None,
    fetcher: Fetcher | None = None,
    progress: ProgressCallback | None = None,
    force: bool = False,
) -> NemoEngine:
    """Ensure the pinned NeMo-Speech.cpp archive is extracted and return the engine.

    This is the module's composition point and the only place a concrete
    collaborator is constructed (ADR 0019): every dependency a caller can vary is
    a parameter — the pin and the install directory for tests, the ``Fetcher`` for
    a fake download.

    Order of operations, each one deliberate:

    1. **Idempotency.** A complete installation of *this* pin is returned
       untouched (no download, no hash of the tree) unless ``force`` is set.
    2. **Fetch and verify.** :func:`ges2t.model_store.ensure_artifact` downloads
       the pinned archive to the cache, verifies its sha256 **and** byte size, and
       installs it atomically. A mismatch raises
       :class:`~ges2t.model_store.ArtifactVerificationError` **before** anything
       is extracted, so a bad download leaves nothing half-written — no staging
       directory, no partial engine.
    3. **Extract.** Into a staging directory beside the install directory.
    4. **Verify, then swap.** The manifest is written and the staged tree is
       re-read through :func:`verify_engine` (so a truncated extraction is caught)
       *before* the atomic replace. ``force`` re-extracts over a complete install
       (the ``--rebuild`` escape hatch).

    Nothing here builds, installs a package, or runs ``sudo``, and the device the
    engine will use is :func:`inspect_device`'s answer, not an input: the install
    is identical on a Vulkan machine and a CPU-only one.
    """
    destination = directory if directory is not None else engine_dir(version)

    if not force:
        installed = verify_engine(destination, pin=pin)
        if installed is not None:
            _report(progress, 1.0)
            return installed

    archive = ensure_artifact(
        pin,
        archive_cache_path(version, pin),
        fetcher=fetcher,
        progress=progress,
    )

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = _staging_directory(destination)
    try:
        root = _extract(archive, staging)
        files = _relative_names(root)
        if ENTRY_LIBRARY_RELATIVE_PATH not in files:
            raise EngineArchiveError(
                archive,
                f"the archive has no {ENTRY_LIBRARY_RELATIVE_PATH!r} "
                f"({len(files)} files extracted)",
            )
        if EXECUTABLE_RELATIVE_PATH not in files:
            raise EngineArchiveError(
                archive,
                f"the archive has no {EXECUTABLE_RELATIVE_PATH!r} "
                f"({len(files)} files extracted)",
            )
        staged = NemoEngine(
            version=version,
            tag=tag,
            archive=pin.name,
            sha256=pin.sha256,
            size=pin.size,
            directory=root,
            entry_library=root / ENTRY_LIBRARY_RELATIVE_PATH,
            executable=root / EXECUTABLE_RELATIVE_PATH,
            files=tuple(root / name for name in files),
            extracted_at=_utc_now(),
        )
        _write_manifest(staged)
        if verify_engine(root, pin=pin) is None:
            raise EngineArchiveError(
                archive, f"the staged engine at {root} is incomplete"
            )
        _replace_directory(root, destination)
    except BaseException:
        _remove_tree(staging)
        raise
    _remove_tree(staging)

    installed = verify_engine(destination, pin=pin)
    if installed is None:
        raise NemoEngineError(f"the installed engine at {destination} is incomplete")
    _report(progress, 1.0)
    return installed
