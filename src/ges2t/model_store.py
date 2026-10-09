"""Model store: pinned download, verification, and atomic install.

This module owns the two artifacts ges2t runs against — the Whistle weights
(``whistle.cact``) and the Needle 3 engine (``libneedle.so``, extracted from
upstream's engine wheel) — and is, by design, **the only code in ges2t allowed
to perform network I/O** (ADR 0018). It is called from ``ges2t setup`` and from
``ges2t models``; never from the service's startup path.

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
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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
    "PLATFORM_KEY",
    "SUPPORTED_ENGINE_PLATFORMS",
    "ArtifactPin",
    "ArtifactVerificationError",
    "Fetcher",
    "ModelStoreError",
    "ProgressCallback",
    "UnsupportedPlatformError",
    "default_data_dir",
    "engine_path",
    "engine_pin",
    "ensure_engine",
    "ensure_model",
    "env_overrides",
    "model_path",
    "verify",
]

# --- Pins (ADR 0017) ----------------------------------------------------------

#: Version directory for the Whistle weights pin.
MODEL_VERSION: str = "2.0.0"

#: Version directory for the Needle 3 engine pin.
ENGINE_VERSION: str = "3.2.0"

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

#: Receives short, human-readable progress lines, one call per milestone.
ProgressCallback = Callable[[str], None]


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
    installs. ``fetcher`` is the test seam; ``progress`` receives status lines.
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
        _report(progress, f"{pin.name}: already present at {destination} ({pin.size} bytes)")
        return destination

    destination.parent.mkdir(parents=True, exist_ok=True)
    fetch = fetcher if fetcher is not None else _download
    _report(progress, f"{pin.name}: downloading {pin.url}")

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

    _report(
        progress,
        f"{pin.name}: installed {destination} ({pin.size} bytes, sha256 verified)",
    )
    return destination


def _present_with_pinned_size(path: Path, expected_size: int) -> bool:
    """Return whether ``path`` is a file of exactly ``expected_size`` bytes.

    Deliberately **not** a hash check: normal load verifies existence and size
    only, and the full sha256 is deferred to :func:`verify` and to the
    acquisition path (ADR 0017). Do not "optimise" this into a hash — hashing
    17 MB must not sit on the service's startup path.
    """
    return path.is_file() and path.stat().st_size == expected_size


def _download(url: str, destination: Path) -> None:
    """Fetch ``url`` into ``destination`` with the stdlib, following redirects.

    Hugging Face's ``/resolve/`` URLs redirect to a CDN; the default
    :mod:`urllib.request` opener follows 3xx redirects, which is why this works
    with no HTTP dependency (ADR 0018).
    """
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request) as response, destination.open("wb") as handle:
        while True:
            chunk = response.read(_CHUNK_SIZE)
            if not chunk:
                break
            handle.write(chunk)


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


def _report(progress: ProgressCallback | None, message: str) -> None:
    """Forward a progress line when a callback was supplied."""
    if progress is not None:
        progress(message)
