"""Tests for the pinned model store (ADR 0017).

The suite never touches the network: every acquisition path goes through an
injected fake ``fetcher``. Real pins are checked against the ADR values as data,
while the download/verify/atomic-install machinery is exercised with small
synthetic pins.
"""

from __future__ import annotations

import ast
import hashlib
import io
import sys
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Literal

import pytest

from ges2t import model_store
from ges2t.model_store import (
    DEFAULT_NEEDLE_MODEL,
    ENGINE_MEMBER,
    ENGINE_SHA256,
    ENGINE_SIZE,
    ENGINE_WHEEL_URL,
    MODEL_SHA256,
    MODEL_SIZE,
    MODEL_URL,
    NEEDLE_MODELS,
    NEMO_MODEL,
    NEMO_MODEL_FILE_NAME,
    NEMO_MODEL_NAME,
    NEMO_MODEL_PIN,
    NEMO_MODEL_REPOSITORY,
    NEMO_MODEL_REVISION,
    NEMO_MODEL_SHA256,
    NEMO_MODEL_SIZE,
    NEMO_MODEL_URL,
    PLATFORM_KEY,
    WHISPER_CPP_CATALOGUE,
    WHISPER_CPP_MODELS,
    WHISPER_CPP_MODELS_URL,
    WHISPER_CPP_QUANTS,
    WHISPER_CPP_SIZES,
    ArtifactPin,
    ArtifactVerificationError,
    ModelStoreError,
    NeedleModelNameError,
    NemoModel,
    UnsupportedPlatformError,
    WhisperModel,
    WhisperModelName,
    WhisperModelNameError,
    default_data_dir,
    engine_path,
    ensure_engine,
    ensure_model,
    ensure_nemo_model,
    ensure_whisper_model,
    env_overrides,
    model_path,
    needle_model,
    nemo_model_path,
    parse_whisper_model_name,
    verify,
    whisper_model,
    whisper_model_path,
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
    """Replace the real pins with small synthetic ones (no network, fast).

    The acquisition reads the needle catalogue, so the synthetic pin replaces the
    default entry's ``NeedleModel`` (not just the ``MODEL_PIN`` constant).
    """
    monkeypatch.setattr(model_store, "MODEL_PIN", MODEL_TEST_PIN)
    monkeypatch.setattr(model_store, "ENGINE_PIN", ENGINE_TEST_PIN)
    synthetic = model_store.NeedleModel(
        name=model_store.DEFAULT_NEEDLE_MODEL,
        version=model_store.MODEL_VERSION,
        pin=MODEL_TEST_PIN,
    )
    monkeypatch.setattr(
        model_store,
        "NEEDLE_CATALOGUE",
        MappingProxyType({synthetic.name: synthetic}),
    )


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


def test_progress_callback_receives_fractions(data_home: Path, fake_pins: None) -> None:
    fractions: list[float] = []
    ensure_model(fetcher=RecordingFetcher(MODEL_BYTES), progress=fractions.append)
    assert fractions == [0.0, 1.0]


def test_progress_callback_is_optional(data_home: Path, fake_pins: None) -> None:
    # ``progress=None`` means "do not report", and must stay the default.
    assert ensure_model(fetcher=RecordingFetcher(MODEL_BYTES)) == model_path()


def test_present_artifact_reports_complete(data_home: Path, fake_pins: None) -> None:
    destination = model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"\x00" * MODEL_TEST_PIN.size)
    fractions: list[float] = []

    assert ensure_model(fetcher=RecordingFetcher(b""), progress=fractions.append) == destination

    assert fractions == [1.0]


def test_progress_callback_exceptions_do_not_abort_acquisition(
    data_home: Path, fake_pins: None
) -> None:
    seen: list[float] = []

    def broken(fraction: float) -> None:
        seen.append(fraction)
        raise RuntimeError("progress sink exploded")

    result = ensure_model(fetcher=RecordingFetcher(MODEL_BYTES), progress=broken)

    assert result == model_path()
    assert result.read_bytes() == MODEL_BYTES
    assert seen == [0.0, 1.0]


class FakeResponse:
    """A minimal stand-in for the object ``urllib.request.urlopen`` returns."""

    def __init__(self, payload: bytes, content_length: str | None) -> None:
        self._stream = io.BytesIO(payload)
        self.headers: dict[str, str] = (
            {} if content_length is None else {"Content-Length": content_length}
        )

    def read(self, size: int) -> bytes:
        return self._stream.read(size)

    def __enter__(self) -> FakeResponse:
        return self

    # ``Literal[False]`` rather than ``bool`` or ``None``: the double must never
    # swallow an exception, and ``Literal[False]`` states that invariant exactly,
    # whereas ``bool`` admits a possible ``True`` (mypy's [exit-return] complaint).
    # ``None`` would be equally safe for mypy but would mean rewriting the body,
    # since returning a value from a ``-> None`` method is itself an error; keeping
    # the explicit ``return False`` preserves the double's existing behaviour.
    def __exit__(self, *exc: object) -> Literal[False]:
        return False


def test_download_reports_content_length_fractions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"0123456789"
    monkeypatch.setattr(model_store, "_CHUNK_SIZE", 4)
    monkeypatch.setattr(
        model_store.urllib.request,
        "urlopen",
        lambda request: FakeResponse(payload, "10"),
    )
    fractions: list[float] = []
    destination = tmp_path / "artifact.bin"

    model_store._download(
        "https://example.invalid/x", destination, progress=fractions.append
    )

    # 10/10 == 1.0 is deliberately left to ``ensure_artifact``; the downloader only
    # emits the strictly-intermediate fractions.
    assert fractions == [0.4, 0.8]
    assert destination.read_bytes() == payload


def test_download_without_content_length_reports_no_fractions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"0123456789"
    monkeypatch.setattr(model_store, "_CHUNK_SIZE", 4)
    monkeypatch.setattr(
        model_store.urllib.request,
        "urlopen",
        lambda request: FakeResponse(payload, None),
    )
    fractions: list[float] = []
    destination = tmp_path / "artifact.bin"

    model_store._download(
        "https://example.invalid/x", destination, progress=fractions.append
    )

    assert fractions == []
    assert destination.read_bytes() == payload


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


# --- needle model catalogue ---------------------------------------------------


def test_needle_catalogue_pins_exactly_whistle() -> None:
    assert tuple(entry.name for entry in NEEDLE_MODELS) == ("whistle",)
    assert DEFAULT_NEEDLE_MODEL == "whistle"
    assert set(model_store.NEEDLE_CATALOGUE) == {"whistle"}


def test_needle_model_path_matches_the_adr_layout(data_home: Path) -> None:
    assert needle_model("whistle").path() == model_path()
    assert model_path() == (
        data_home / "ges2t" / "models" / "whistle" / "2.0.0" / "whistle.cact"
    )


def test_needle_model_rejects_an_unknown_name_with_a_typed_error() -> None:
    with pytest.raises(NeedleModelNameError) as excinfo:
        needle_model("dragon")

    error = excinfo.value
    assert error.name == "dragon"
    assert error.valid == ("whistle",)
    assert "whistle" in str(error)


def test_ensure_model_rejects_an_unknown_name_before_fetching(
    data_home: Path, fake_pins: None
) -> None:
    fetcher = RecordingFetcher(MODEL_BYTES)

    with pytest.raises(NeedleModelNameError):
        ensure_model("dragon", fetcher=fetcher)

    assert fetcher.calls == []


def test_ensure_model_accepts_the_named_default(data_home: Path, fake_pins: None) -> None:
    result = ensure_model("whistle", fetcher=RecordingFetcher(MODEL_BYTES))
    assert result == model_path()


# --- whisper.cpp: catalogue ---------------------------------------------------

#: The pins, **recorded as data** so a silent edit to the catalogue is caught.
#: They were verified against the live Hugging Face host (provenance: the tree
#: API listing in the ticket's 001-comment.md), but this dict is a constant: the
#: test below does **not** contact the network. The live check is the manual step
#: recorded in 001-comment.md and re-run by the reviewer.
EXPECTED_WHISPER_PINS: dict[str, tuple[int, str]] = {
    "tiny-q8_0": (
        43_537_433,
        "c2085835d3f50733e2ff6e4b41ae8a2b8d8110461e18821b09a15c40c42d1cca",
    ),
    "base-q8_0": (
        81_768_585,
        "c577b9a86e7e048a0b7eada054f4dd79a56bbfa911fbdacf900ac5b567cbb7d9",
    ),
    "small-q8_0": (
        264_464_607,
        "49c8fb02b65e6049d5fa6c04f81f53b867b5ec9540406812c643f177317f779f",
    ),
    "medium-q5_0": (
        539_212_467,
        "19fea4b380c3a618ec4723c3eef2eb785ffba0d0538cf43f8f235e7b3b34220f",
    ),
    "medium-q8_0": (
        823_369_779,
        "42a1ffcbe4167d224232443396968db4d02d4e8e87e213d3ee2e03095dea6502",
    ),
    "large-v3-turbo-q5_0": (
        574_041_195,
        "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2",
    ),
    "large-v3-turbo-q8_0": (
        874_188_075,
        "317eb69c11673c9de1e1f0d459b253999804ec71ac4c23c17ecf5fbe24e259a1",
    ),
}


def test_whisper_catalogue_covers_exactly_the_live_verified_names() -> None:
    assert tuple(entry.name for entry in WHISPER_CPP_MODELS) == tuple(EXPECTED_WHISPER_PINS)
    assert set(WHISPER_CPP_CATALOGUE) == set(EXPECTED_WHISPER_PINS)


def test_whisper_catalogue_matches_the_recorded_pins() -> None:
    """Compare the catalogue against the recorded pin constants.

    This is an offline change-detector, **not** a live-host check: it only
    compares two constants and never contacts Hugging Face. The live
    re-verification is the manual step recorded in this ticket's 001-comment.md.
    """
    for name, (size_bytes, sha256) in EXPECTED_WHISPER_PINS.items():
        entry = whisper_model(name)
        assert entry.size_bytes == size_bytes
        assert entry.sha256 == sha256


def test_whisper_catalogue_entries_are_self_consistent() -> None:
    for entry in WHISPER_CPP_MODELS:
        assert entry.name == f"{entry.model.size}-{entry.model.quant}"
        assert entry.file_name == f"ggml-{entry.name}.bin"
        assert entry.url == f"{WHISPER_CPP_MODELS_URL}/{entry.file_name}"
        assert len(entry.sha256) == 64
        int(entry.sha256, 16)  # a hex digest, or ValueError
        assert entry.size_bytes > 0
        assert entry.pin() == ArtifactPin(
            name=entry.file_name,
            url=entry.url,
            sha256=entry.sha256,
            size=entry.size_bytes,
        )


def test_whisper_catalogue_only_uses_supported_quants_and_never_f16() -> None:
    quants = {entry.model.quant for entry in WHISPER_CPP_MODELS}
    assert quants <= set(WHISPER_CPP_QUANTS)
    assert "f16" not in quants


def test_whisper_catalogue_publishes_q8_for_every_size_and_q5_only_where_upstream_does() -> None:
    # q8_0 for every size; q5_0 only for medium/large-v3-turbo (the ticket's rule).
    for size in WHISPER_CPP_SIZES:
        assert whisper_model(f"{size}-q8_0").model.quant == "q8_0"
    assert {entry.name for entry in WHISPER_CPP_MODELS if entry.model.quant == "q5_0"} == {
        "medium-q5_0",
        "large-v3-turbo-q5_0",
    }


# --- whisper.cpp: name parser -------------------------------------------------


def test_parse_whisper_model_name_splits_size_and_quant() -> None:
    assert parse_whisper_model_name("medium-q5_0") == WhisperModelName(
        size="medium", quant="q5_0"
    )
    assert parse_whisper_model_name("large-v3-turbo-q8_0") == WhisperModelName(
        size="large-v3-turbo", quant="q8_0"
    )


def test_parse_whisper_model_name_accepts_every_catalogue_entry() -> None:
    for entry in WHISPER_CPP_MODELS:
        parsed = parse_whisper_model_name(entry.name)
        assert parsed.name == entry.name
        assert parsed == entry.model


@pytest.mark.parametrize(
    "name",
    [
        "",
        "medium",
        "medium-",
        "-q5_0",
        "MEDIUM-q8_0",
        "tiny-q5_0",  # size/quant valid, but not published upstream
        "small-q5_1",  # q5_1 is published but not supported
        "medium-f16",  # never f16
        "large-v1-q8_0",  # size not in the shipped range
        "medium-q5_0-extra",
        "medium-q9_9",
    ],
)
def test_parse_whisper_model_name_rejects_with_a_typed_error_naming_the_valid_set(
    name: str,
) -> None:
    with pytest.raises(WhisperModelNameError) as excinfo:
        parse_whisper_model_name(name)

    message = str(excinfo.value)
    assert "valid models:" in message
    for valid in WHISPER_CPP_CATALOGUE:
        assert valid in message


def test_whisper_model_name_error_carries_its_fields() -> None:
    with pytest.raises(WhisperModelNameError) as excinfo:
        parse_whisper_model_name("medium-f16")

    error = excinfo.value
    assert error.name == "medium-f16"
    assert "f16" in error.reason
    assert error.valid == tuple(WHISPER_CPP_CATALOGUE)


# --- whisper.cpp: paths -------------------------------------------------------


def test_whisper_model_path_uses_the_epic_layout(data_home: Path) -> None:
    assert whisper_model_path("medium-q5_0") == (
        data_home / "ges2t" / "models" / "whisper-cpp" / "medium-q5_0" / "ggml-medium-q5_0.bin"
    )
    assert whisper_model_path("large-v3-turbo-q5_0") == (
        data_home
        / "ges2t"
        / "models"
        / "whisper-cpp"
        / "large-v3-turbo-q5_0"
        / "ggml-large-v3-turbo-q5_0.bin"
    )


def test_whisper_model_path_rejects_an_unknown_name(data_home: Path) -> None:
    with pytest.raises(WhisperModelNameError):
        whisper_model_path("medium-f16")


# --- whisper.cpp: acquisition -------------------------------------------------

WHISPER_TEST_BYTES: bytes = b"ggml-whisper-payload" * 3
WHISPER_TEST_ENTRY: WhisperModel = WhisperModel(
    model=WhisperModelName(size="tiny", quant="q8_0"),
    url=f"{WHISPER_CPP_MODELS_URL}/ggml-tiny-q8_0.bin",
    sha256=hashlib.sha256(WHISPER_TEST_BYTES).hexdigest(),
    size_bytes=len(WHISPER_TEST_BYTES),
)


def _flip_one_byte(payload: bytes) -> bytes:
    """Return ``payload`` with exactly one bit flipped in its first byte."""
    flipped = bytearray(payload)
    flipped[0] ^= 0x01
    return bytes(flipped)


@pytest.fixture()
def fake_whisper_catalogue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real (multi-hundred-MB) catalogue with one tiny synthetic entry."""
    monkeypatch.setattr(
        model_store,
        "WHISPER_CPP_CATALOGUE",
        MappingProxyType({WHISPER_TEST_ENTRY.name: WHISPER_TEST_ENTRY}),
    )
    monkeypatch.setattr(model_store, "WHISPER_CPP_MODELS", (WHISPER_TEST_ENTRY,))


def test_ensure_whisper_model_downloads_verifies_and_installs(
    data_home: Path, fake_whisper_catalogue: None
) -> None:
    fetcher = RecordingFetcher(WHISPER_TEST_BYTES)

    result = ensure_whisper_model("tiny-q8_0", fetcher=fetcher)

    assert result == whisper_model_path("tiny-q8_0")
    assert result.read_bytes() == WHISPER_TEST_BYTES
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][0] == WHISPER_TEST_ENTRY.url
    assert verify(result, WHISPER_TEST_ENTRY.pin()) is True


def test_ensure_whisper_model_is_idempotent(
    data_home: Path, fake_whisper_catalogue: None
) -> None:
    ensure_whisper_model("tiny-q8_0", fetcher=RecordingFetcher(WHISPER_TEST_BYTES))
    second = RecordingFetcher(b"")

    result = ensure_whisper_model("tiny-q8_0", fetcher=second)

    assert result == whisper_model_path("tiny-q8_0")
    assert second.calls == []


def test_ensure_whisper_model_flipped_byte_is_a_typed_error(
    data_home: Path, fake_whisper_catalogue: None
) -> None:
    corrupted = _flip_one_byte(WHISPER_TEST_BYTES)
    assert len(corrupted) == WHISPER_TEST_ENTRY.size_bytes  # size alone would pass
    fetcher = RecordingFetcher(corrupted)

    with pytest.raises(ArtifactVerificationError) as excinfo:
        ensure_whisper_model("tiny-q8_0", fetcher=fetcher)

    message = str(excinfo.value)
    assert WHISPER_TEST_ENTRY.file_name in message
    assert WHISPER_TEST_ENTRY.sha256 in message
    assert hashlib.sha256(corrupted).hexdigest() in message
    assert not whisper_model_path("tiny-q8_0").exists()
    assert _part_files(data_home) == []


def test_ensure_whisper_model_truncated_file_is_a_typed_error(
    data_home: Path, fake_whisper_catalogue: None
) -> None:
    fetcher = RecordingFetcher(WHISPER_TEST_BYTES[:-1])

    with pytest.raises(ArtifactVerificationError):
        ensure_whisper_model("tiny-q8_0", fetcher=fetcher)

    assert not whisper_model_path("tiny-q8_0").exists()
    assert _part_files(data_home) == []


def test_ensure_whisper_model_network_failure_propagates_and_leaves_nothing(
    data_home: Path, fake_whisper_catalogue: None
) -> None:
    fetcher = InterruptedFetcher(WHISPER_TEST_BYTES[:5])

    with pytest.raises(ConnectionError):
        ensure_whisper_model("tiny-q8_0", fetcher=fetcher)

    assert fetcher.calls == 1
    assert not whisper_model_path("tiny-q8_0").exists()
    assert _part_files(data_home) == []


def test_ensure_whisper_model_rejects_an_unpublished_name_before_fetching(
    data_home: Path, fake_whisper_catalogue: None
) -> None:
    fetcher = RecordingFetcher(WHISPER_TEST_BYTES)

    with pytest.raises(WhisperModelNameError):
        ensure_whisper_model("medium-q5_0", fetcher=fetcher)

    assert fetcher.calls == []


SECOND_WHISPER_TEST_BYTES: bytes = b"ggml-second-whisper-payload" * 2
SECOND_WHISPER_TEST_ENTRY: WhisperModel = WhisperModel(
    model=WhisperModelName(size="base", quant="q8_0"),
    url=f"{WHISPER_CPP_MODELS_URL}/ggml-base-q8_0.bin",
    sha256=hashlib.sha256(SECOND_WHISPER_TEST_BYTES).hexdigest(),
    size_bytes=len(SECOND_WHISPER_TEST_BYTES),
)


def test_fetching_a_second_whisper_model_leaves_the_first_untouched(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        model_store,
        "WHISPER_CPP_CATALOGUE",
        MappingProxyType(
            {
                WHISPER_TEST_ENTRY.name: WHISPER_TEST_ENTRY,
                SECOND_WHISPER_TEST_ENTRY.name: SECOND_WHISPER_TEST_ENTRY,
            }
        ),
    )
    monkeypatch.setattr(
        model_store,
        "WHISPER_CPP_MODELS",
        (WHISPER_TEST_ENTRY, SECOND_WHISPER_TEST_ENTRY),
    )

    ensure_whisper_model("tiny-q8_0", fetcher=RecordingFetcher(WHISPER_TEST_BYTES))
    first = whisper_model_path("tiny-q8_0")

    second = RecordingFetcher(SECOND_WHISPER_TEST_BYTES)
    ensure_whisper_model("base-q8_0", fetcher=second)

    assert whisper_model_path("base-q8_0").read_bytes() == SECOND_WHISPER_TEST_BYTES
    # The first model's bytes are untouched and it was not re-fetched.
    assert first.read_bytes() == WHISPER_TEST_BYTES
    assert len(second.calls) == 1


# --- NeMo-Speech.cpp streaming model (ADR 0022) -------------------------------


def test_nemo_model_pin_matches_ticket_55() -> None:
    # Literal strings on purpose (rule 8): these are the values ticket 55 verified
    # against the live host, not a re-assertion of the module's own constants.
    assert NEMO_MODEL_REPOSITORY == "nvidia/nemotron-3.5-asr-streaming-0.6b"
    assert NEMO_MODEL_REVISION == "1c8deaecc64b91f034d73e08dd8b64625eb3395d"
    assert NEMO_MODEL_FILE_NAME == "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
    assert NEMO_MODEL_SHA256 == (
        "a5c435f294eea8f88ce68dd27b8c3bfea7f777cb2fbba04fcd30eaa555f429ae"
    )
    assert NEMO_MODEL_SIZE == 741_548_352
    assert NEMO_MODEL_URL == (
        "https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b/resolve/"
        "1c8deaecc64b91f034d73e08dd8b64625eb3395d/"
        "nemotron-3.5-asr-streaming-0.6b.q8_0.gguf"
    )


def test_nemo_model_pin_url_is_revision_qualified_not_main() -> None:
    # ADR 0017/ADR 0022 §8: the revision is part of the pin. ``main`` is a
    # *different* artifact of the same name (742,090,464 B, revision ea30d66d…),
    # so a ``resolve/main`` URL must never be pinned here.
    assert NEMO_MODEL_REVISION in NEMO_MODEL_URL
    assert "/resolve/main" not in NEMO_MODEL_URL
    assert NEMO_MODEL.pin is NEMO_MODEL_PIN
    assert NEMO_MODEL.pin.url == NEMO_MODEL_URL
    assert NEMO_MODEL.pin.size == NEMO_MODEL_SIZE
    assert NEMO_MODEL.pin.sha256 == NEMO_MODEL_SHA256
    assert NEMO_MODEL.pin.member is None


def test_nemo_model_path_uses_the_data_dir_layout(data_home: Path) -> None:
    assert nemo_model_path() == (
        data_home
        / "ges2t"
        / "models"
        / "nemo-speech"
        / NEMO_MODEL_NAME
        / NEMO_MODEL_FILE_NAME
    )


NEMO_TEST_BYTES: bytes = b"gguf-nemotron-payload" * 3


@pytest.fixture()
def fake_nemo_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """Replace the real (741 MB) nemo model with one tiny synthetic entry."""
    monkeypatch.setattr(
        model_store,
        "NEMO_MODEL",
        NemoModel(
            name="nemotron-test",
            revision="0" * 40,
            pin=_make_pin("nemotron-test.q8_0.gguf", NEMO_TEST_BYTES),
        ),
    )


def test_ensure_nemo_model_downloads_verifies_and_installs(
    data_home: Path, fake_nemo_model: None
) -> None:
    fetcher = RecordingFetcher(NEMO_TEST_BYTES)

    result = ensure_nemo_model(fetcher=fetcher)

    assert result == nemo_model_path()
    assert result.read_bytes() == NEMO_TEST_BYTES
    assert len(fetcher.calls) == 1
    assert fetcher.calls[0][0] == model_store.NEMO_MODEL.pin.url
    assert verify(result, model_store.NEMO_MODEL.pin) is True


def test_ensure_nemo_model_is_idempotent(
    data_home: Path, fake_nemo_model: None
) -> None:
    ensure_nemo_model(fetcher=RecordingFetcher(NEMO_TEST_BYTES))
    second = RecordingFetcher(b"")

    result = ensure_nemo_model(fetcher=second)

    assert result == nemo_model_path()
    assert second.calls == []


def test_ensure_nemo_model_wrong_hash_is_a_typed_error(
    data_home: Path, fake_nemo_model: None
) -> None:
    corrupt = _flip_one_byte(NEMO_TEST_BYTES)
    assert len(corrupt) == model_store.NEMO_MODEL.pin.size  # size alone would pass

    with pytest.raises(ArtifactVerificationError) as excinfo:
        ensure_nemo_model(fetcher=RecordingFetcher(corrupt))

    message = str(excinfo.value)
    assert model_store.NEMO_MODEL.pin.sha256 in message
    assert hashlib.sha256(corrupt).hexdigest() in message
    assert not nemo_model_path().exists()
    assert _part_files(data_home) == []


def test_ensure_nemo_model_wrong_size_is_a_typed_error(
    data_home: Path, fake_nemo_model: None
) -> None:
    with pytest.raises(ArtifactVerificationError):
        ensure_nemo_model(fetcher=RecordingFetcher(NEMO_TEST_BYTES[:-1]))

    assert not nemo_model_path().exists()
    assert _part_files(data_home) == []


def test_ensure_nemo_model_checks_the_size_alone(
    data_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A pin whose sha256 is the payload's but whose *size* is wrong: only the
    # size check can reject this, so the pair really is checked, not just the hash.
    payload = NEMO_TEST_BYTES
    monkeypatch.setattr(
        model_store,
        "NEMO_MODEL",
        NemoModel(
            name="nemotron-test",
            revision="0" * 40,
            pin=ArtifactPin(
                name="nemotron-test.q8_0.gguf",
                url="https://example.invalid/nemotron-test.q8_0.gguf",
                sha256=hashlib.sha256(payload).hexdigest(),
                size=len(payload) + 1,
            ),
        ),
    )

    with pytest.raises(ArtifactVerificationError):
        ensure_nemo_model(fetcher=RecordingFetcher(payload))

    assert not nemo_model_path().exists()
    assert _part_files(data_home) == []


def test_ensure_nemo_model_network_failure_leaves_nothing(
    data_home: Path, fake_nemo_model: None
) -> None:
    fetcher = InterruptedFetcher(NEMO_TEST_BYTES[:5])

    with pytest.raises(ConnectionError):
        ensure_nemo_model(fetcher=fetcher)

    assert fetcher.calls == 1
    assert not nemo_model_path().exists()
    assert _part_files(data_home) == []


# --- ADR 0018: acquisition is the only networked path ------------------------

#: Modules that provide an HTTP client to our own code. Any import of one of
#: these outside ``model_store`` is a second networked path (ADR 0018).
_HTTP_CLIENT_MODULES: frozenset[str] = frozenset(
    {"urllib", "http", "httpx", "requests", "aiohttp", "ftplib"}
)

#: The only modules allowed to import ``socket``: both use it for **AF_UNIX**
#: local IPC (the service protocol and the ``ydotoold`` probe). A new module that
#: needs a socket fails this test until a human adds it here deliberately.
_SOCKET_MODULES_ALLOWED: frozenset[str] = frozenset({"doctor.py", "socket_proto.py"})

#: Names that only appear when a **network** socket is built. ``AF_INET`` /
#: ``AF_INET6`` and ``create_connection`` are the raw-socket equivalent of an
#: HTTP client; none may appear anywhere in our source.
_NETWORK_SOCKET_CONSTRUCTS: frozenset[str] = frozenset(
    {"AF_INET", "AF_INET6", "create_connection"}
)


@dataclass(frozen=True)
class ModuleScan:
    """What one source file imports and which bare/attribute names it references."""

    imported_modules: frozenset[str]
    referenced_names: frozenset[str]


def _imported_top_level_names(tree: ast.AST) -> set[str]:
    """Return the top-level module names imported by a parsed module.

    Uses :mod:`ast`, not a regex, so it sees every name in a multi-import
    (``import os, httpx``) and resolves ``importlib.import_module("httpx")`` and
    ``__import__("httpx")`` when the target is a string literal.
    """
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and node.level == 0:
                imported.add(node.module.split(".")[0])
        elif isinstance(node, ast.Call):
            dynamic = _dynamic_import_target(node)
            if dynamic is not None:
                imported.add(dynamic)
    return imported


def _dynamic_import_target(node: ast.Call) -> str | None:
    """Return the top-level module of a string-literal ``import_module``/``__import__`` call."""
    func = node.func
    is_dynamic_import = (
        isinstance(func, ast.Attribute)
        and func.attr in {"import_module", "__import__"}
    ) or (isinstance(func, ast.Name) and func.id == "__import__")
    if not is_dynamic_import or not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value.split(".")[0] or None
    return None


def _scan_module(source: str) -> ModuleScan:
    """Return the imports and referenced names of one source file."""
    tree = ast.parse(source)
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)
    return ModuleScan(
        imported_modules=frozenset(_imported_top_level_names(tree)),
        referenced_names=frozenset(referenced),
    )


def test_network_clients_are_confined_to_the_model_store() -> None:
    """No second networked path: HTTP clients and network sockets live only in acquisition.

    Scope, stated honestly: this is a **static** scan of matching names, not a
    runtime guard. It catches HTTP-client imports (including multi-imports and
    literal ``importlib.import_module``) and the raw-socket constructs
    ``AF_INET``/``AF_INET6``/``create_connection``. The residual hole is a bare
    ``socket.socket()`` (which defaults to ``AF_INET``) followed by a
    ``.connect((host, port))`` hidden inside one of the two allow-listed
    AF_UNIX modules; that is not caught. The realistic HTTP clients are.
    """
    source_root = Path(model_store.__file__).resolve().parent
    http_importers: dict[str, frozenset[str]] = {}
    socket_importers: set[str] = set()
    network_socket_users: dict[str, frozenset[str]] = {}
    for path in sorted(source_root.rglob("*.py")):
        scan = _scan_module(path.read_text(encoding="utf-8"))
        http_hits = scan.imported_modules & _HTTP_CLIENT_MODULES
        if http_hits:
            http_importers[path.name] = http_hits
        if "socket" in scan.imported_modules:
            socket_importers.add(path.name)
        socket_hits = scan.referenced_names & _NETWORK_SOCKET_CONSTRUCTS
        if socket_hits:
            network_socket_users[path.name] = socket_hits

    assert http_importers == {"model_store.py": frozenset({"urllib"})}, http_importers
    assert socket_importers == _SOCKET_MODULES_ALLOWED, socket_importers
    assert network_socket_users == {}, network_socket_users
