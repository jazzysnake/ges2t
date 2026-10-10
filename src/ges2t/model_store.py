"""Model store: pinned download, verification, and atomic install.

This module owns the artifacts ges2t runs against — the Whistle weights
(``whistle.cact``) and the Needle 3 engine (``libneedle.so``, extracted from
upstream's engine wheel) for the default backend, plus the whisper.cpp ggml model
**catalogue** (``ggml-<size>-<quant>.bin`` from ``ggerganov/whisper.cpp``) — and
is, by design, **the only code in ges2t allowed to perform network I/O**
(ADR 0018). It is called from ``ges2t setup`` and from ``ges2t models``; never
from the service's startup path.

Facts that shape this module:

* Artifacts are pinned by sha256 **and** byte size (ADR 0017), not trusted by
  URL. The pins below are the single source of truth and a reviewer diffs them
  against ADR 0017.
* ges2t owns the artifacts under ``$XDG_DATA_HOME/ges2t`` rather than using the
  ``cactus-needle`` package's hard-coded ``~/.cache/cactus-needle`` cache, so
  that package's auto-download can never be reached at runtime (ADR 0007,
  ADR 0017). Nothing here reads or writes ``~/.cache/cactus-needle``.
* Downloads are **atomic**: fetched to a temporary file in the destination
  directory, hashed, then ``os.replace``-d into place, so an interrupted run can
  never leave a half-written artifact that later passes an existence check.
* The engine pin is architecture-specific. On a platform with no pin we fail
  loudly *before* downloading anything.

The module deliberately does **not** import ``ges2t.interfaces`` (it is usable
with no backend) and does **not** import ``cactus_needle`` (``ges2t models`` and
``doctor`` must stay importable without the backend installed).
"""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

__all__ = [
    "DEFAULT_DATA_DIRECTORY_NAME",
    "ENGINE_MEMBER",
    "ENGINE_PIN",
    "ENGINE_SHA256",
    "ENGINE_SIZE",
    "ENGINE_VERSION",
    "ENGINE_WHEEL_URL",
    "MODEL_PIN",
    "MODEL_SHA256",
    "MODEL_SIZE",
    "MODEL_URL",
    "MODEL_VERSION",
    "PINNED_CACTUS_NEEDLE_VERSION",
    "PLATFORM_KEY",
    "SUPPORTED_ENGINE_PLATFORMS",
    "WHISPER_CPP_CATALOGUE",
    "WHISPER_CPP_MODELS",
    "WHISPER_CPP_MODELS_URL",
    "WHISPER_CPP_QUANTS",
    "WHISPER_CPP_SIZES",
    "ArtifactPin",
    "ArtifactVerificationError",
    "Fetcher",
    "ModelStoreError",
    "ProgressCallback",
    "UnsupportedPlatformError",
    "WhisperModel",
    "WhisperModelName",
    "WhisperModelNameError",
    "default_data_dir",
    "engine_path",
    "engine_pin",
    "ensure_engine",
    "ensure_model",
    "ensure_whisper_model",
    "env_overrides",
    "model_path",
    "parse_whisper_model_name",
    "verify",
    "whisper_model",
    "whisper_model_path",
]

# --- Pins (ADR 0017) ----------------------------------------------------------

#: Version directory for the Whistle weights pin.
MODEL_VERSION: str = "2.0.0"

#: Version directory for the Needle 3 engine pin.
ENGINE_VERSION: str = "3.2.0"

#: The ``cactus-needle`` *distribution* version ges2t is built and tested
#: against. It is the same pin ``pyproject.toml`` declares as
#: ``cactus-needle==<version>``, kept here so ``ges2t doctor`` can compare the
#: *installed* distribution against it without reading the source tree. The
#: streaming decoder is coupled to this package's exact generator shape (one
#: dict per ``next()``, a terminal tail dict, and the engine's own ``stop()``),
#: so a silent upgrade can break the real seam while the fake-based suite stays
#: green (ticket 14's review, risk R1). Doctor WARNs on the skew; the adapter
#: deliberately does not assert it, because a cosmetic mismatch must not fail
#: dictation at runtime.
PINNED_CACTUS_NEEDLE_VERSION: str = "3.1.3"

#: Upstream URL for the Whistle weights. ``/resolve/`` redirects to a CDN; the
#: stdlib opener follows that redirect (verified against the real endpoint).
MODEL_URL: str = (
    "https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact"
)
MODEL_SHA256: str = "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb"
MODEL_SIZE: int = 16_919_407

#: Upstream engine wheel (a manylinux2014 x86-64 wheel, i.e. a zip archive).
ENGINE_WHEEL_URL: str = (
    "https://huggingface.co/Cactus-Compute/needle3/resolve/main/python/"
    "cactus_needle-3.2.0-py3-none-manylinux2014_x86_64.whl"
)
#: Member inside the wheel. The file's SONAME is ``libneedle.so``; we install it
#: under that name so it matches the ``NEEDLE3_LIB_PATH`` contract (ADR 0017).
ENGINE_MEMBER: str = "needle/libneedle3.so"
ENGINE_SHA256: str = "0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030"
ENGINE_SIZE: int = 1_581_448

# --- whisper.cpp model catalogue ---------------------------------------------- #

#: Canonical Hugging Face repo for the whisper.cpp ggml models. The individual
#: files are ``<repo>/ggml-<size>-<quant>.bin``; ``/resolve/`` redirects to a CDN
#: and the stdlib opener follows that redirect (ADR 0018, verified against the
#: real endpoint). The catalogue below pins the exact file, size and sha256.
WHISPER_CPP_MODELS_URL: str = (
    "https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
)

#: The whisper.cpp sizes ges2t ships. This is the provider research's tested
#: range and the ADR 0021 default-model range (tiny … large-v3-turbo). Upstream
#: also publishes ``large-v1``/``large-v2``/``large-v3`` variants; they are
#: deliberately **not** pinned here (untested by the research, and not needed by
#: the GPU/CPU default rule).
WHISPER_CPP_SIZES: tuple[str, ...] = (
    "tiny",
    "base",
    "small",
    "medium",
    "large-v3-turbo",
)

#: The quantisations ges2t supports. **Never ``f16``** (larger, slower, never
#: more accurate — ADR 0021) and no quantiser is shipped: only files upstream
#: actually publishes are pinned. ``q8_0`` exists for every size; ``q5_0`` only
#: for ``medium``/``large-v3-turbo``.
WHISPER_CPP_QUANTS: tuple[str, ...] = ("q5_0", "q8_0")

#: Directory name under the XDG data home.
DEFAULT_DATA_DIRECTORY_NAME: str = "ges2t"

#: Chunk size for both the download loop and the hashing loop (1 MiB).
_CHUNK_SIZE: int = 1024 * 1024

#: Sent with every request; some CDNs reject the default ``Python-urllib`` agent.
_USER_AGENT: str = "ges2t"

_MACHINE_ALIASES: dict[str, str] = {
    "amd64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
}


@dataclass(frozen=True)
class ArtifactPin:
    """An immutable, checksummed artifact ges2t is willing to download.

    ``member`` is set when the real artifact lives inside a container (the engine
    is a member of a wheel, which is a zip); it is ``None`` for a file fetched
    directly. The ``sha256`` and ``size`` always describe the artifact that is
    installed at the destination, not the container.
    """

    name: str
    url: str
    sha256: str
    size: int
    member: str | None = None


@dataclass(frozen=True)
class WhisperModelName:
    """A parsed whisper.cpp model name: a size and a quantisation.

    ``name`` renders back to the ``<size>-<quant>`` form used by config,
    ``doctor`` and the destination directory. A class rather than a tuple so a
    caller cannot confuse the two strings (ADR 0019).
    """

    size: str
    quant: str

    @property
    def name(self) -> str:
        """Return the canonical ``<size>-<quant>`` name, e.g. ``medium-q5_0``."""
        return f"{self.size}-{self.quant}"


@dataclass(frozen=True)
class WhisperModel:
    """A downloadable whisper.cpp ggml model: its name plus a pinned file.

    ``size_bytes``/``sha256`` always describe the ``.bin`` that is installed,
    never the repo. ``pin()`` adapts the entry to the shared :class:`ArtifactPin`
    so acquisition reuses the Whistle store's download/verify/install path
    rather than duplicating it.
    """

    model: WhisperModelName
    url: str
    sha256: str
    size_bytes: int

    @property
    def name(self) -> str:
        """Return the canonical ``<size>-<quant>`` name."""
        return self.model.name

    @property
    def file_name(self) -> str:
        """Return the upstream file name, ``ggml-<name>.bin``."""
        return f"ggml-{self.model.name}.bin"

    def pin(self) -> ArtifactPin:
        """Return this model as an :class:`ArtifactPin` for the shared installer."""
        return ArtifactPin(
            name=self.file_name,
            url=self.url,
            sha256=self.sha256,
            size=self.size_bytes,
        )


def _whisper_model(
    size: str,
    quant: str,
    size_bytes: int,
    sha256: str,
) -> WhisperModel:
    """Build a catalogue entry from the live-verified pin data."""
    name = WhisperModelName(size=size, quant=quant)
    return WhisperModel(
        model=name,
        url=f"{WHISPER_CPP_MODELS_URL}/ggml-{name.name}.bin",
        sha256=sha256,
        size_bytes=size_bytes,
    )


#: The Whistle weights pin.
MODEL_PIN: ArtifactPin = ArtifactPin(
    name="whistle.cact",
    url=MODEL_URL,
    sha256=MODEL_SHA256,
    size=MODEL_SIZE,
)

#: The Needle 3 engine pin (x86-64 manylinux).
ENGINE_PIN: ArtifactPin = ArtifactPin(
    name="libneedle.so",
    url=ENGINE_WHEEL_URL,
    sha256=ENGINE_SHA256,
    size=ENGINE_SIZE,
    member=ENGINE_MEMBER,
)

#: The whisper.cpp ggml model catalogue.
#:
#: Every pin was **re-verified against the live Hugging Face host** on
#: 2026-10-10 (see the ticket's ``001-comment.md`` for the provenance): the tree
#: API reports each Git-LFS object's size and ``lfs.oid``, which *is* the file's
#: sha256. The three entries the provider research already published
#: (``small-q8_0``, ``medium-q5_0``, ``large-v3-turbo-q5_0``) match the live host
#: byte-for-byte; the others are newly measured against it. ``tiny``/``base`` /
#: ``small`` ``q5_0`` are **not** upstream-published — the research produced those
#: with a local quantiser — so they are not here.
WHISPER_CPP_MODELS: tuple[WhisperModel, ...] = (
    _whisper_model(
        "tiny",
        "q8_0",
        43_537_433,
        "c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca",
    ),
    _whisper_model(
        "base",
        "q8_0",
        81_768_585,
        "c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9",
    ),
    _whisper_model(
        "small",
        "q8_0",
        264_464_607,
        "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f",
    ),
    _whisper_model(
        "medium",
        "q5_0",
        539_212_467,
        "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
    ),
    _whisper_model(
        "medium",
        "q8_0",
        823_369_779,
        "42a1ffcbe4167d224232443396968db4d02d4e8e87e213d3ee2e03095dea6502",
    ),
    _whisper_model(
        "large-v3-turbo",
        "q5_0",
        574_041_195,
        "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    ),
    _whisper_model(
        "large-v3-turbo",
        "q8_0",
        874_188_075,
        "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1",
    ),
)

#: The catalogue keyed by ``<size>-<quant>`` name, for O(1) lookup and printing.
WHISPER_CPP_CATALOGUE: Mapping[str, WhisperModel] = MappingProxyType(
    {model.name: model for model in WHISPER_CPP_MODELS}
)

#: Normalised CPU architecture, e.g. ``x86_64`` from ``platform.machine()``.
_MACHINE: str = _MACHINE_ALIASES.get(platform.machine().lower(), platform.machine().lower())

#: A short, stable key describing the running platform, e.g. ``linux-x86_64``.
PLATFORM_KEY: str = f"{sys.platform}-{_MACHINE}"

#: The platforms for which an engine artifact is pinned. Only x86-64 Linux is
#: pinned today (ADR 0017); everything else fails before downloading anything.
SUPPORTED_ENGINE_PLATFORMS: frozenset[str] = frozenset({"linux-x86_64"})


# --- Callable shapes (ADR 0019) ----------------------------------------------

#: Downloads ``url`` into the file at ``dest`` (truncating it). This is the seam
#: tests replace so the suite performs no network I/O.
Fetcher = Callable[[str, Path], None]

#: Receives the completed fraction in ``[0.0, 1.0]`` as an artifact is
#: downloaded and installed. This is structurally identical to
#: ``ges2t.interfaces.ProgressCallback``; the alias is kept local on purpose so
#: this module never imports the interfaces module (it must stay importable with
#: no backend and no dependency on the pipeline vocabulary).
ProgressCallback = Callable[[float], None]


# --- Errors -------------------------------------------------------------------


class ModelStoreError(Exception):
    """Base class for every failure raised by this module."""


class UnsupportedPlatformError(ModelStoreError):
    """No engine artifact is pinned for the running platform."""


class ArtifactVerificationError(ModelStoreError):
    """A downloaded artifact did not match its pinned sha256 or byte size.

    The message names the artifact, the expected hash and size, and the actual
    hash and size, so ``ges2t setup`` can print it verbatim (ADR 0017).
    """
    def __init__(
        self,
        pin: ArtifactPin,
        actual_sha256: str,
        actual_size: int,
        destination: Path,
    ) -> None:
        self.pin = pin
        self.actual_sha256 = actual_sha256
        self.actual_size = actual_size
        self.destination = destination
        super().__init__(
            f"{pin.name}: pinned sha256 {pin.sha256} ({pin.size} bytes), but downloaded "
            f"sha256 {actual_sha256} ({actual_size} bytes); refusing to install into "
            f"{destination}"
        )


class WhisperModelNameError(ModelStoreError):
    """A whisper.cpp model name is malformed or not upstream-published.

    Carries the offending ``name`` and the parse ``reason``, and always names the
    valid set of catalogue names so ``ges2t setup``/config can print an
    actionable error (the ticket's "unknown size or quant → a typed error naming
    the valid set").
    """

    def __init__(
        self,
        name: str,
        reason: str,
        valid: Sequence[str],
    ) -> None:
        self.name = name
        self.reason = reason
        self.valid = tuple(valid)
        super().__init__(
            f"unknown whisper.cpp model {name!r}: {reason}; "
            f"valid models: {', '.join(self.valid)}"
        )


# --- Locations ----------------------------------------------------------------


def default_data_dir() -> Path:
    """Return ``$XDG_DATA_HOME/ges2t``, defaulting to ``~/.local/share/ges2t``.

    An empty ``XDG_DATA_HOME`` is treated as unset, mirroring the config layer
    (ADR 0009).
    """
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return base / DEFAULT_DATA_DIRECTORY_NAME


def model_path(version: str = MODEL_VERSION) -> Path:
    """Return ``<data_dir>/models/whistle/<version>/whistle.cact``."""
    return default_data_dir() / "models" / "whistle" / version / "whistle.cact"


def engine_path(version: str = ENGINE_VERSION) -> Path:
    """Return ``<data_dir>/engine/<version>/libneedle.so``.

    The installed name is ``libneedle.so`` (the library's SONAME) even though the
    wheel member is ``needle/libneedle3.so``; ``NEEDLE3_LIB_PATH`` expects the
    former (ADR 0017).
    """
    return default_data_dir() / "engine" / version / "libneedle.so"


def whisper_model_path(name: str) -> Path:
    """Return ``<data_dir>/models/whisper-cpp/<name>/ggml-<name>.bin``.

    Args:
        name: a canonical ``<size>-<quant>`` name, e.g. ``medium-q5_0``. It is
            parsed and validated first, so an unknown name raises a typed error
            rather than producing a plausible-looking path.
    """
    parsed = parse_whisper_model_name(name)
    return (
        default_data_dir()
        / "models"
        / "whisper-cpp"
        / parsed.name
        / f"ggml-{parsed.name}.bin"
    )


def parse_whisper_model_name(name: str) -> WhisperModelName:
    """Parse and validate a ``<size>-<quant>`` whisper.cpp model name.

    The size may contain hyphens (``large-v3-turbo-q5_0``), so the quant is the
    segment after the *last* hyphen. A name is accepted only when it is a
    catalogue entry, i.e. an upstream-published size/quant combination; anything
    else raises :class:`WhisperModelNameError` whose message names the valid set.
    """
    entry = WHISPER_CPP_CATALOGUE.get(name)
    if entry is not None:
        return entry.model

    size, separator, quant = name.rpartition("-")
    if not separator or not size or not quant:
        reason = "expected '<size>-<quant>'"
    elif size not in WHISPER_CPP_SIZES:
        reason = f"unknown size {size!r}; valid sizes: {', '.join(WHISPER_CPP_SIZES)}"
    elif quant not in WHISPER_CPP_QUANTS:
        reason = f"unknown quant {quant!r}; valid quants: {', '.join(WHISPER_CPP_QUANTS)}"
    else:
        reason = f"{quant!r} is not published upstream for size {size!r}"
    raise WhisperModelNameError(name, reason, tuple(WHISPER_CPP_CATALOGUE))


def whisper_model(name: str) -> WhisperModel:
    """Return the catalogue entry for ``name``, or raise :class:`WhisperModelNameError`."""
    parsed = parse_whisper_model_name(name)
    return WHISPER_CPP_CATALOGUE[parsed.name]


def engine_pin() -> ArtifactPin:
    """Return the engine pin for this platform, or raise :class:`UnsupportedPlatformError`."""
    if PLATFORM_KEY not in SUPPORTED_ENGINE_PLATFORMS:
        supported = ", ".join(sorted(SUPPORTED_ENGINE_PLATFORMS))
        raise UnsupportedPlatformError(
            f"no engine artifact pinned for this platform ({PLATFORM_KEY}); "
            f"supported platforms: {supported}"
        )
    return ENGINE_PIN


def env_overrides() -> dict[str, str]:
    """Return the environment exports the backend needs (ADR 0017).

    Pointing ``NEEDLE_WHISTLE_WEIGHTS``/``NEEDLE3_LIB_PATH`` at the managed paths
    makes the pins real rather than nominal, and ``HF_HUB_OFFLINE=1`` turns a
    wrong path into a loud local error instead of a silent download (ADR 0007).
    """
    return {
        "NEEDLE_WHISTLE_WEIGHTS": str(model_path(MODEL_VERSION)),
        "NEEDLE3_LIB_PATH": str(engine_path(ENGINE_VERSION)),
        "NEEDLE_TELEMETRY": "0",
        "DO_NOT_TRACK": "1",
        "HF_HUB_OFFLINE": "1",
    }


# --- Verification -------------------------------------------------------------


def verify(path: Path, expected: ArtifactPin) -> bool:
    """Return whether ``path`` matches ``expected`` by full sha256 and byte size.

    This is the explicit, expensive check behind ``ges2t models --verify``.
    Normal load must **not** call it: existence and byte size are enough on the
    startup path (ADR 0017), see :func:`_present_with_pinned_size`.
    """
    if not path.is_file():
        return False
    return path.stat().st_size == expected.size and _sha256_file(path) == expected.sha256


# --- Acquisition --------------------------------------------------------------


def ensure_model(
    *,
    fetcher: Fetcher | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    """Ensure the pinned Whistle weights exist locally and return their path.

    Returns immediately when a file of the pinned byte size already exists (no
    download, no hash — ADR 0017). Otherwise downloads atomically, verifies, and
    installs. ``fetcher`` is the test seam; ``progress`` receives the completed
    fraction in ``[0.0, 1.0]`` (see :data:`ProgressCallback`).
    """
    _require_supported_platform()
    return _ensure(
        MODEL_PIN,
        model_path(MODEL_VERSION),
        fetcher=fetcher,
        progress=progress,
    )


def ensure_engine(
    *,
    fetcher: Fetcher | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    """Ensure the pinned Needle 3 engine exists locally and return its path.

    Same contract as :func:`ensure_model`, except the artifact is extracted from
    the pinned wheel with :mod:`zipfile` and the *extracted* ``.so`` is verified.
    No ``pip install``, and nothing is written to ``~/.cache/cactus-needle``.
    """
    pin = engine_pin()
    return _ensure(
        pin,
        engine_path(ENGINE_VERSION),
        fetcher=fetcher,
        progress=progress,
    )


def ensure_whisper_model(
    name: str,
    *,
    fetcher: Fetcher | None = None,
    progress: ProgressCallback | None = None,
) -> Path:
    """Ensure the named whisper.cpp ggml model exists locally and return its path.

    Same contract as :func:`ensure_model`: an existing file of the pinned byte
    size is returned untouched (idempotent — no download, no hash), otherwise the
    file is downloaded atomically, verified against the pinned sha256 **and** byte
    size, and installed at
    ``<data_dir>/models/whisper-cpp/<name>/ggml-<name>.bin``. ``name`` is parsed
    and validated first, so an unpublished model fails before any network I/O.

    Whisper models are platform-independent (unlike the architecture-pinned
    Needle engine), so no platform gate applies here.
    """
    entry = whisper_model(name)
    return _ensure(
        entry.pin(),
        whisper_model_path(name),
        fetcher=fetcher,
        progress=progress,
    )


def _require_supported_platform() -> None:
    """Fail before any download when no engine is pinned for this platform (ADR 0017)."""
    engine_pin()


def _ensure(
    pin: ArtifactPin,
    destination: Path,
    *,
    fetcher: Fetcher | None,
    progress: ProgressCallback | None,
) -> Path:
    """Download, verify and atomically install ``pin`` at ``destination``.

    ``destination`` always comes from :func:`model_path`/:func:`engine_path`, so
    every write stays inside :func:`default_data_dir`.
    """
    if _present_with_pinned_size(destination, pin.size):
        _report(progress, 1.0)
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    fetch = fetcher if fetcher is not None else _downloader(progress)
    _report(progress, 0.0)

    fetched = _temporary_path(destination)
    extracted: Path | None = None
    try:
        fetch(pin.url, fetched)
        if pin.member is None:
            source = fetched
        else:
            extracted = _temporary_path(destination)
            _extract_member(fetched, pin.member, extracted)
            source = extracted

        actual_sha256 = _sha256_file(source)
        actual_size = source.stat().st_size
        if actual_sha256 != pin.sha256 or actual_size != pin.size:
            raise ArtifactVerificationError(pin, actual_sha256, actual_size, destination)

        # os.replace within one directory is atomic: the destination only ever
        # appears complete, never half-written.
        os.replace(source, destination)
    finally:
        _remove(fetched)
        if extracted is not None:
            _remove(extracted)

    _report(progress, 1.0)
    return destination


def _present_with_pinned_size(path: Path, expected_size: int) -> bool:
    """Return whether ``path`` is a file of exactly ``expected_size`` bytes.

    Deliberately **not** a hash check: normal load verifies existence and size
    only, and the full sha256 is deferred to :func:`verify` and to the
    acquisition path (ADR 0017). Do not "optimise" this into a hash — hashing
    17 MB must not sit on the service's startup path.
    """
    return path.is_file() and path.stat().st_size == expected_size


def _downloader(progress: ProgressCallback | None) -> Fetcher:
    """Return the real :data:`Fetcher`, wired to stream ``progress`` fractions."""

    def fetch(url: str, destination: Path) -> None:
        _download(url, destination, progress=progress)

    return fetch


def _download(
    url: str,
    destination: Path,
    *,
    progress: ProgressCallback | None = None,
) -> None:
    """Fetch ``url`` into ``destination`` with the stdlib, following redirects.

    Hugging Face's ``/resolve/`` URLs redirect to a CDN; the default
    :mod:`urllib.request` opener follows 3xx redirects, which is why this works
    with no HTTP dependency (ADR 0018).

    When the response carries a ``Content-Length``, each chunk reports
    ``downloaded / total`` clamped to ``[0.0, 1.0]``. The terminal ``1.0`` is
    deliberately left to :func:`_ensure`, which emits it only once the artifact
    has been verified and installed, so what the caller sees is one monotonic
    run ending in a single ``1.0``. When ``Content-Length`` is missing we emit
    **no** intermediate fractions: an invented estimate would be worse than
    silence, and the caller still gets ``0.0`` at the start and ``1.0`` on
    completion.
    """
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response, destination.open("wb") as handle:
        total = _content_length(response)
        downloaded = 0
        while True:
            chunk = response.read(_CHUNK_SIZE)
            if not chunk:
                break
            handle.write(chunk)
            downloaded += len(chunk)
            if total:
                fraction = min(downloaded / total, 1.0)
                if fraction < 1.0:
                    _report(progress, fraction)


def _content_length(response: Any) -> int | None:
    """Return the response's ``Content-Length`` as an int, or ``None`` if absent.

    A missing *or malformed* header is treated as unknown, never as zero.
    """
    raw_total = response.headers.get("Content-Length")
    if raw_total is None:
        return None
    try:
        return int(raw_total)
    except ValueError:
        return None


def _extract_member(archive: Path, member: str, destination: Path) -> None:
    """Copy ``member`` out of the zip ``archive`` into ``destination``."""
    with zipfile.ZipFile(archive) as bundle:
        try:
            info = bundle.getinfo(member)
        except KeyError as exc:
            raise ModelStoreError(f"{archive}: archive has no member {member!r}") from exc
        with bundle.open(info) as source, destination.open("wb") as handle:
            shutil.copyfileobj(source, handle)


def _sha256_file(path: Path) -> str:
    """Return the hex sha256 of ``path``, read in chunks so memory stays flat."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _temporary_path(destination: Path) -> Path:
    """Create an empty sibling temp file for ``destination`` and return its path."""
    handle, name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".part",
        dir=destination.parent,
    )
    os.close(handle)
    return Path(name)


def _remove(path: Path) -> None:
    """Delete ``path`` if it exists; used to clean up partial downloads."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def _report(progress: ProgressCallback | None, fraction: float) -> None:
    """Forward the completed fraction when a callback was supplied.

    Exceptions raised by the callback are swallowed: a broken progress sink must
    never abort an otherwise successful acquisition. Only ``Exception`` is
    caught, so ``KeyboardInterrupt`` and ``SystemExit`` still propagate.
    """
    if progress is not None:
        try:
            progress(fraction)
        except Exception:
            pass
