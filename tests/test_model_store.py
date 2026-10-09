"""Tests for the pinned model store (ADR 0017).

The suite never touches the network: every acquisition path goes through an
injected fake ``fetcher``. Real pins are checked against the ADR values as data,
while the download/verify/atomic-install machinery is exercised with small
synthetic pins.
"""

from __future__ import annotations

import hashlib
import io
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from ges2t import model_store
from ges2t.model_store import (
    ENGINE_MEMBER,
    ENGINE_SHA256,
    ENGINE_SIZE,
    ENGINE_WHEEL_URL,
    MODEL_SHA256,
    MODEL_SIZE,
    MODEL_URL,
    PLATFORM_KEY,
    ArtifactPin,
    ArtifactVerificationError,
    ModelStoreError,
    UnsupportedPlatformError,
    default_data_dir,
    engine_path,
    ensure_engine,
    ensure_model,
    env_overrides,
    model_path,
    verify,
)

MODEL_BYTES: bytes = b"whistle-weights-payload" * 3
ENGINE_BYTES: bytes = b"\x7fELF" + b"needle-engine-payload"
WRONG_BYTES: bytes = b"not the pinned payload at all"


def _make_pin(name: str, payload: bytes, *, member: str | None = None) -> ArtifactPin:
    """Build a synthetic pin whose hash and size describe ``payload``."""
    return ArtifactPin(
        name=name,
        url=f"https://example.invalid/{name}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
        member=member,
    )


MODEL_TEST_PIN: ArtifactPin = _make_pin("whistle.cact", MODEL_BYTES)
ENGINE_TEST_PIN: ArtifactPin = _make_pin("libneedle.so", ENGINE_BYTES, member=ENGINE_MEMBER)


def _wheel_bytes(member: str, content: bytes) -> bytes:
    """Return an in-memory wheel (zip) containing ``member`` with ``content``."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        bundle.writestr(member, content)
    return buffer.getvalue()


class RecordingFetcher:
    """A ``Fetcher`` that records its calls and writes a fixed payload."""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, Path]] = []

    def __call__(self, url: str, destination: Path) -> None:
        self.calls.append((url, destination))
        destination.write_bytes(self.payload)


class ObservingFetcher:
    """A ``Fetcher`` that records whether the real destination existed mid-fetch."""

    def __init__(self, payload: bytes, watched: Path) -> None:
        self.payload = payload
        self.watched = watched
        self.destination_existed_during_fetch: bool | None = None
        self.calls = 0

    def __call__(self, url: str, destination: Path) -> None:
        self.calls += 1
        self.destination_existed_during_fetch = self.watched.exists()
        destination.write_bytes(self.payload)


class InterruptedFetcher:
    """A ``Fetcher`` that writes a partial file and then fails, like a dropped link."""

    def __init__(self, partial: bytes) -> None:
        self.partial = partial
        self.calls = 0

    def __call__(self, url: str, destination: Path) -> None:
        self.calls += 1
        destination.write_bytes(self.partial)
        raise ConnectionError("simulated network drop mid-download")


def _part_files(directory: Path) -> list[Path]:
    """Return any leftover temporary part files under ``directory``."""
    if not directory.exists():
        return []
    return [entry for entry in directory.rglob("*.part")]


@pytest.fixture()
def data_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point ``$XDG_DATA_HOME`` at a tmp dir so nothing leaves the test sandbox."""
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
    yield tmp_path


@pytest.fixture()
def fake_pins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real pins with small synthetic ones (no network, fast)."""
    monkeypatch.setattr(model_store, "MODEL_PIN", MODEL_TEST_PIN)
    monkeypatch.setattr(model_store, "ENGINE_PIN", ENGINE_TEST_PIN)


# --- pins and platform --------------------------------------------------------


def test_model_pin_matches_adr_0017() -> None:
    assert MODEL_URL == (
        "https://huggingface.co/Cactus-Compute/whistle/resolve/main/whistle.cact"
    )
    assert MODEL_SHA256 == "b6e02f048568ac5d01a2042556c658061e699acbc0aa2a1439f52f3d461dffeb"
    assert MODEL_SIZE == 16_919_407


def test_engine_pin_matches_adr_0017() -> None:
    assert ENGINE_WHEEL_URL == (
        "https://huggingface.co/Cactus-Compute/needle3/resolve/main/python/"
        "cactus_needle-3.2.0-py3-none-manylinux2014_x86_64.whl"
    )
    assert ENGINE_MEMBER == "needle/libneedle3.so"
    assert ENGINE_SHA256 == "0772130a416cc7a07fe444bfe400239a595195324f595b2139391839874fd030"
    assert ENGINE_SIZE == 1_581_448


def test_platform_key_is_derived_from_platform() -> None:
    machine = model_store._MACHINE_ALIASES.get(
        model_store.platform.machine().lower(), model_store.platform.machine().lower()
    )
    assert PLATFORM_KEY == f"{sys.platform}-{machine}"
    assert "linux-x86_64" in model_store.SUPPORTED_ENGINE_PLATFORMS


# --- locations ----------------------------------------------------------------


def test_default_data_dir_uses_xdg_data_home(data_home: Path) -> None:
    assert default_data_dir() == data_home / "ges2t"


def test_default_data_dir_falls_back_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / ".local" / "share" / "ges2t"


def test_empty_xdg_data_home_falls_back_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_DATA_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert default_data_dir() == tmp_path / ".local" / "share" / "ges2t"


def test_model_and_engine_paths_use_the_adr_layout(data_home: Path) -> None:
    assert model_path() == data_home / "ges2t" / "models" / "whistle" / "2.0.0" / "whistle.cact"
    assert engine_path() == data_home / "ges2t" / "engine" / "3.2.0" / "libneedle.so"


def test_paths_honour_an_explicit_version(data_home: Path) -> None:
    assert model_path("9.9.9").parent.name == "9.9.9"
    assert engine_path("9.9.9").parent.name == "9.9.9"


# --- env overrides ------------------------------------------------------------


def test_env_overrides_contains_all_five_variables(data_home: Path) -> None:
    overrides = env_overrides()
    assert set(overrides) == {
        "NEEDLE_WHISTLE_WEIGHTS",
        "NEEDLE3_LIB_PATH",
        "NEEDLE_TELEMETRY",
        "DO_NOT_TRACK",
        "HF_HUB_OFFLINE",
    }
    assert overrides["NEEDLE_WHISTLE_WEIGHTS"] == str(model_path())
    assert overrides["NEEDLE3_LIB_PATH"] == str(engine_path())
    assert overrides["NEEDLE_TELEMETRY"] == "0"
    assert overrides["DO_NOT_TRACK"] == "1"
    assert overrides["HF_HUB_OFFLINE"] == "1"


# --- ensure_model: present, verified, atomic ----------------------------------


def test_present_file_of_pinned_size_is_returned_without_fetching(
    data_home: Path, fake_pins: None
) -> None:
    destination = model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"\x00" * MODEL_TEST_PIN.size)
    fetcher = RecordingFetcher(MODEL_BYTES)

    assert ensure_model(fetcher=fetcher) == destination
    assert fetcher.calls == []


def test_successful_download_installs_the_verified_payload(
    data_home: Path, fake_pins: None
) -> None:
    fetcher = RecordingFetcher(MODEL_BYTES)

    result = ensure_model(fetcher=fetcher)

    assert result == model_path()
    assert result.read_bytes() == MODEL_BYTES
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][0] == MODEL_TEST_PIN.url
    assert verify(result, MODEL_TEST_PIN) is True


def test_replace_happens_only_after_the_fetch_completes(
    data_home: Path, fake_pins: None
) -> None:
    destination = model_path()
    fetcher = ObservingFetcher(MODEL_BYTES, destination)

    ensure_model(fetcher=fetcher)

    assert fetcher.calls == 1
    assert fetcher.destination_existed_during_fetch is False
    assert destination.read_bytes() == MODEL_BYTES


def test_checksum_mismatch_raises_and_leaves_no_file(
    data_home: Path, fake_pins: None
) -> None:
    fetcher = RecordingFetcher(WRONG_BYTES)

    with pytest.raises(ArtifactVerificationError) as excinfo:
        ensure_model(fetcher=fetcher)

    message = str(excinfo.value)
    assert MODEL_TEST_PIN.name in message
    assert MODEL_TEST_PIN.sha256 in message
    assert hashlib.sha256(WRONG_BYTES).hexdigest() in message
    assert not model_path().exists()
    assert _part_files(data_home) == []


def test_interrupted_fetch_leaves_no_partial_file(
    data_home: Path, fake_pins: None
) -> None:
    fetcher = InterruptedFetcher(MODEL_BYTES[:5])

    with pytest.raises(ConnectionError):
        ensure_model(fetcher=fetcher)

    assert fetcher.calls == 1
    assert not model_path().exists()
    assert _part_files(data_home) == []


def test_size_mismatch_is_reported_when_only_the_size_is_wrong(
    data_home: Path, fake_pins: None
) -> None:
    # Hash of a truncated payload differs too, but the point is the pair is checked.
    fetcher = RecordingFetcher(MODEL_BYTES[:-1])

    with pytest.raises(ArtifactVerificationError):
        ensure_model(fetcher=fetcher)

    assert not model_path().exists()


def test_progress_callback_receives_lines(data_home: Path, fake_pins: None) -> None:
    messages: list[str] = []
    ensure_model(fetcher=RecordingFetcher(MODEL_BYTES), progress=messages.append)
    assert any("downloading" in line for line in messages)
    assert any("installed" in line for line in messages)


# --- ensure_engine: extraction ------------------------------------------------


def test_engine_is_extracted_from_the_wheel_and_verified(
    data_home: Path, fake_pins: None
) -> None:
    wheel = _wheel_bytes(ENGINE_MEMBER, ENGINE_BYTES)
    fetcher = RecordingFetcher(wheel)

    result = ensure_engine(fetcher=fetcher)

    assert result == engine_path()
    assert result.name == "libneedle.so"
    assert result.read_bytes() == ENGINE_BYTES
    assert verify(result, ENGINE_TEST_PIN) is True


def test_engine_wheel_with_wrong_member_content_is_rejected(
    data_home: Path, fake_pins: None
) -> None:
    wheel = _wheel_bytes(ENGINE_MEMBER, WRONG_BYTES)

    with pytest.raises(ArtifactVerificationError):
        ensure_engine(fetcher=RecordingFetcher(wheel))

    assert not engine_path().exists()
    assert _part_files(data_home) == []


def test_engine_wheel_without_the_member_is_a_clear_error(
    data_home: Path, fake_pins: None
) -> None:
    wheel = _wheel_bytes("needle/somewhere_else.so", ENGINE_BYTES)

    with pytest.raises(ModelStoreError) as excinfo:
        ensure_engine(fetcher=RecordingFetcher(wheel))

    assert ENGINE_MEMBER in str(excinfo.value)
    assert not engine_path().exists()


def test_engine_present_file_is_not_refetched(data_home: Path, fake_pins: None) -> None:
    destination = engine_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"\x00" * ENGINE_TEST_PIN.size)
    fetcher = RecordingFetcher(b"")

    assert ensure_engine(fetcher=fetcher) == destination
    assert fetcher.calls == []


# --- unsupported platform -----------------------------------------------------


def test_unsupported_platform_raises_before_downloading(
    data_home: Path, fake_pins: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(model_store, "PLATFORM_KEY", "plan9-vax")
    fetcher = RecordingFetcher(MODEL_BYTES)

    with pytest.raises(UnsupportedPlatformError) as excinfo:
        ensure_engine(fetcher=fetcher)

    assert "no engine artifact pinned for this platform" in str(excinfo.value)
    with pytest.raises(UnsupportedPlatformError):
        ensure_model(fetcher=fetcher)
    assert fetcher.calls == []
    assert not engine_path().exists()
    assert not model_path().exists()


# --- verify -------------------------------------------------------------------


def test_verify_accepts_the_pinned_file(data_home: Path, fake_pins: None) -> None:
    path = model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(MODEL_BYTES)
    assert verify(path, MODEL_TEST_PIN) is True


def test_verify_rejects_tampered_content(data_home: Path, fake_pins: None) -> None:
    path = model_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(WRONG_BYTES)
    assert verify(path, MODEL_TEST_PIN) is False


def test_verify_rejects_a_missing_file(data_home: Path, fake_pins: None) -> None:
    assert verify(model_path(), MODEL_TEST_PIN) is False
