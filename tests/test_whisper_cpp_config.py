"""Tests for the ``[engine.whisper_cpp]`` configuration block (ADR 0009, ADR 0021).

The block crosses the generic/engine boundary as a raw mapping, so these tests
call ``WhisperCppConfig.from_mapping`` directly with plain dicts. One test parses
a real TOML snippet with ``tomllib`` to prove the int-vs-bool trap is handled.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from ges2t.transcribers.whisper_cpp_config import (
    BLOCK_NAME,
    DEFAULT_GPU,
    DEFAULT_LANGUAGE,
    DEFAULT_MODEL,
    DEFAULT_THREADS,
    KNOWN_KEYS,
    GpuPreference,
    WhisperCppConfig,
    WhisperCppConfigError,
    WhisperCppConfigResult,
    default_threads,
)


def _error_message(raw: Mapping[str, Any]) -> str:
    """Validate ``raw``, assert it raises, and return the error message."""
    with pytest.raises(WhisperCppConfigError) as excinfo:
        WhisperCppConfig.from_mapping(raw)
    return str(excinfo.value)


# --- defaults -----------------------------------------------------------------


def test_empty_mapping_yields_all_defaults() -> None:
    result = WhisperCppConfig.from_mapping({})
    assert isinstance(result, WhisperCppConfigResult)
    assert result.config == WhisperCppConfig()
    assert result.config.model is None  # omitted: resolved at build time (ticket 45)
    assert result.config.language == ""
    assert result.config.threads is None
    assert result.config.gpu is GpuPreference.AUTO
    assert result.warnings == []


def test_default_values_match_the_documented_schema() -> None:
    config = WhisperCppConfig()
    assert config.model is DEFAULT_MODEL is None
    assert config.language == DEFAULT_LANGUAGE == ""
    assert config.threads is DEFAULT_THREADS is None
    assert config.gpu is DEFAULT_GPU is GpuPreference.AUTO
    assert BLOCK_NAME == "engine.whisper_cpp"
    assert KNOWN_KEYS == frozenset({"model", "language", "threads", "gpu"})


def test_an_omitted_model_is_left_unresolved_for_the_build_time_rule() -> None:
    # The device-aware default (ADR 0021 §4) is applied by the composition root
    # through ges2t.whisper_engine.default_whisper_model, never baked in here: a
    # static default would contradict the rule on a Vulkan machine (ticket 45).
    assert WhisperCppConfig.from_mapping({}).config.model is None


def test_config_is_frozen() -> None:
    config = WhisperCppConfig()
    with pytest.raises(FrozenInstanceError):
        config.model = "medium-q5_0"  # type: ignore[misc]


# --- model --------------------------------------------------------------------


def test_model_is_validated_against_the_catalogue() -> None:
    result = WhisperCppConfig.from_mapping({"model": "medium-q5_0"})
    assert result.config.model == "medium-q5_0"


def test_an_unpublished_model_is_rejected_naming_the_valid_set() -> None:
    message = _error_message({"model": "small-q5_0"})
    assert "engine.whisper_cpp.model" in message
    assert "small-q5_0" in message
    assert "tiny-q8_0" in message  # the valid set is printed


def test_a_non_string_model_is_rejected() -> None:
    message = _error_message({"model": 5})
    assert "expected a string" in message
    assert "int" in message


# --- language -----------------------------------------------------------------


def test_language_is_passed_through_verbatim() -> None:
    assert WhisperCppConfig.from_mapping({"language": "de"}).config.language == "de"


def test_a_non_string_language_is_rejected() -> None:
    message = _error_message({"language": ["en"]})
    assert "expected a string" in message


# --- threads ------------------------------------------------------------------


def test_threads_accepts_a_positive_integer() -> None:
    assert WhisperCppConfig.from_mapping({"threads": 8}).config.threads == 8


def test_threads_rejects_a_bool() -> None:
    message = _error_message({"threads": True})
    assert "expected an integer" in message
    assert "bool" in message


def test_threads_rejects_zero_and_negative() -> None:
    assert "at least 1" in _error_message({"threads": 0})
    assert "at least 1" in _error_message({"threads": -2})


def test_threads_rejects_a_float() -> None:
    assert "expected an integer" in _error_message({"threads": 4.0})


def test_threads_toml_int_is_accepted() -> None:
    raw = tomllib.loads("[engine.whisper_cpp]\nthreads = 6\n")
    block = raw["engine"]["whisper_cpp"]
    assert WhisperCppConfig.from_mapping(block).config.threads == 6


# --- gpu ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "value,expected",
    [
        ("auto", GpuPreference.AUTO),
        ("cpu", GpuPreference.CPU),
        ("vulkan", GpuPreference.VULKAN),
    ],
)
def test_gpu_accepts_the_three_preferences(
    value: str, expected: GpuPreference
) -> None:
    assert WhisperCppConfig.from_mapping({"gpu": value}).config.gpu is expected


def test_an_unknown_gpu_is_rejected_naming_the_valid_values() -> None:
    message = _error_message({"gpu": "cuda"})
    assert "engine.whisper_cpp.gpu" in message
    assert "auto" in message and "cpu" in message and "vulkan" in message


def test_a_non_string_gpu_is_rejected() -> None:
    assert "expected a string" in _error_message({"gpu": 1})


# --- unknown keys -------------------------------------------------------------


def test_unknown_keys_are_warnings_not_errors() -> None:
    result = WhisperCppConfig.from_mapping({"model": "medium-q5_0", "beam": 5})
    assert result.config.model == "medium-q5_0"
    assert result.warnings == ["engine.whisper_cpp: unknown key 'beam'"]


# --- helpers ------------------------------------------------------------------


def test_default_threads_is_at_least_one() -> None:
    assert default_threads() >= 1


def test_whisper_cpp_config_is_constructible_with_explicit_values() -> None:
    config = WhisperCppConfig(
        model="medium-q5_0",
        language="en",
        threads=4,
        gpu=GpuPreference.VULKAN,
    )
    assert config.model == "medium-q5_0"
    assert config.language == "en"
    assert config.threads == 4
    assert config.gpu is GpuPreference.VULKAN
