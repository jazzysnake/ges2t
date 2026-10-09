"""Tests for the ``[engine.whistle]`` configuration block (ADR 0009, ADR 0011).

The block crosses the generic/engine boundary as a raw mapping, so these tests
call ``WhistleConfig.from_mapping`` directly with plain dicts. One test parses a
real TOML snippet with ``tomllib`` to prove the int-vs-float trap is handled.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from typing import Any

import pytest

from ges2t.transcribers.whistle_config import (
    DEFAULT_CHUNK_SECONDS,
    DEFAULT_LANGUAGE,
    DEFAULT_WINDOW_SECONDS,
    KNOWN_KEYS,
    LATENCY_KNEE_SECONDS,
    MAX_WINDOW_SECONDS,
    MIN_CHUNK_SECONDS,
    WhistleConfig,
    WhistleConfigError,
    WhistleConfigResult,
)


def _error_message(raw: Mapping[str, Any]) -> str:
    """Validate ``raw``, assert it raises, and return the error message."""
    with pytest.raises(WhistleConfigError) as excinfo:
        WhistleConfig.from_mapping(raw)
    return str(excinfo.value)


# --- defaults -----------------------------------------------------------------


def test_empty_mapping_yields_all_defaults() -> None:
    result = WhistleConfig.from_mapping({})
    assert isinstance(result, WhistleConfigResult)
    assert result.config == WhistleConfig()
    assert result.config.language == ""
    assert result.config.window_seconds == 5.0
    assert result.config.chunk_seconds == 1.0
    assert result.warnings == []


def test_default_values_match_the_adr_schema() -> None:
    config = WhistleConfig()
    assert config.language == DEFAULT_LANGUAGE == ""
    assert config.window_seconds == DEFAULT_WINDOW_SECONDS == 5.0
    assert config.chunk_seconds == DEFAULT_CHUNK_SECONDS == 1.0
    assert MIN_CHUNK_SECONDS == 0.25
    assert MAX_WINDOW_SECONDS == 30.0
    assert LATENCY_KNEE_SECONDS == 6.0
    assert KNOWN_KEYS == frozenset({"language", "window_seconds", "chunk_seconds"})


def test_result_is_a_typed_frozen_dataclass() -> None:
    result = WhistleConfig.from_mapping({})
    assert isinstance(result, WhistleConfigResult)
    assert isinstance(result.config, WhistleConfig)
    assert isinstance(result.warnings, list)
    with pytest.raises(FrozenInstanceError):
        result.warnings = []  # type: ignore[misc]


def test_config_is_frozen() -> None:
    config = WhistleConfig()
    with pytest.raises(FrozenInstanceError):
        config.language = "de"  # type: ignore[misc]


def test_from_mapping_does_not_mutate_the_input() -> None:
    raw: dict[str, object] = {"window_seconds": 5, "chunk_seconds": 1, "typo": 2}
    snapshot = dict(raw)
    WhistleConfig.from_mapping(raw)
    assert raw == snapshot


# --- every known key ----------------------------------------------------------


def test_every_known_key_is_parsed() -> None:
    result = WhistleConfig.from_mapping(
        {"language": "de", "window_seconds": 4.5, "chunk_seconds": 0.5}
    )
    assert result.warnings == []
    assert result.config == WhistleConfig(
        language="de", window_seconds=4.5, chunk_seconds=0.5
    )


@pytest.mark.parametrize("language", ["", "en", "de", "es", "nl", "zz-invented"])
def test_language_accepts_any_string(language: str) -> None:
    result = WhistleConfig.from_mapping({"language": language})
    assert result.config.language == language
    assert result.warnings == []


# --- unknown keys are warnings, never errors ----------------------------------


def test_unknown_key_is_a_warning_and_is_ignored() -> None:
    result = WhistleConfig.from_mapping({"tray": True})
    assert result.config == WhistleConfig()
    assert len(result.warnings) == 1
    assert "engine.whistle" in result.warnings[0]
    assert "'tray'" in result.warnings[0]


def test_model_path_in_the_engine_block_is_an_unknown_key_warning() -> None:
    # model_path is generic (transcriber.model_path, ADR 0009); inside the engine
    # block it is a typo and must be warned about, not honoured.
    result = WhistleConfig.from_mapping({"model_path": "/models/whistle.cact"})
    assert result.config == WhistleConfig()
    assert len(result.warnings) == 1
    assert "'model_path'" in result.warnings[0]


def test_unknown_keys_are_reported_in_insertion_order() -> None:
    result = WhistleConfig.from_mapping({"zzz": 1, "language": "en", "aaa": 2})
    assert result.config.language == "en"
    assert len(result.warnings) == 2
    assert "'zzz'" in result.warnings[0]
    assert "'aaa'" in result.warnings[1]


# --- the int-vs-float TOML trap -----------------------------------------------


def test_toml_integer_is_accepted_as_a_float_with_no_warnings() -> None:
    # `window_seconds = 5`, `chunk_seconds = 1` as Python ints: the ADR's own example.
    result = WhistleConfig.from_mapping({"window_seconds": 5, "chunk_seconds": 1})
    assert result.warnings == []
    assert result.config.window_seconds == 5.0
    assert result.config.chunk_seconds == 1.0
    assert isinstance(result.config.window_seconds, float)
    assert isinstance(result.config.chunk_seconds, float)


def test_integer_and_float_spellings_produce_the_same_config() -> None:
    from_int = WhistleConfig.from_mapping({"window_seconds": 5, "chunk_seconds": 1})
    from_float = WhistleConfig.from_mapping({"window_seconds": 5.0, "chunk_seconds": 1.0})
    assert from_int.config == from_float.config
    assert from_int.warnings == []
    assert from_float.warnings == []


def test_real_toml_parses_the_window_as_int_and_is_still_accepted() -> None:
    # This is the trap itself: tomllib yields `int`, not `float`, for a bare `5`.
    parsed = tomllib.loads(
        "[engine.whistle]\nwindow_seconds = 5\nchunk_seconds = 1\n"
    )
    block = parsed["engine"]["whistle"]
    assert isinstance(block["window_seconds"], int)
    result = WhistleConfig.from_mapping(block)
    assert result.config.window_seconds == 5.0
    assert result.config.chunk_seconds == 1.0
    assert result.warnings == []


# --- wrong types --------------------------------------------------------------


@pytest.mark.parametrize("value", [5, 1.5, True, False, ["en"], {"a": 1}, None])
def test_language_wrong_type_is_rejected(value: object) -> None:
    message = _error_message({"language": value})
    assert "engine.whistle.language" in message
    assert "expected a string" in message
    assert type(value).__name__ in message


@pytest.mark.parametrize("key", ["window_seconds", "chunk_seconds"])
@pytest.mark.parametrize("value", ["five", None, [5], {"a": 1}])
def test_wrong_typed_numbers_are_rejected(key: str, value: object) -> None:
    message = _error_message({key: value})
    assert f"engine.whistle.{key}" in message
    assert "expected a number" in message
    assert type(value).__name__ in message


@pytest.mark.parametrize("key", ["window_seconds", "chunk_seconds"])
@pytest.mark.parametrize("value", [True, False])
def test_booleans_are_rejected_as_numbers(key: str, value: bool) -> None:
    # bool is an int subclass in Python; it must not silently become 1.0 / 0.0.
    message = _error_message({key: value})
    assert f"engine.whistle.{key}" in message
    assert "not bool" in message
    assert "bool" in message
    assert repr(value) in message


@pytest.mark.parametrize("value", [float("inf"), float("-inf"), float("nan")])
def test_non_finite_numbers_are_rejected(value: float) -> None:
    message = _error_message({"window_seconds": value})
    assert "engine.whistle.window_seconds" in message
    assert "finite" in message


# --- range validation: errors -------------------------------------------------


@pytest.mark.parametrize("chunk", [0.0, 0.1, 0.24])
def test_chunk_below_the_minimum_is_rejected(chunk: float) -> None:
    message = _error_message({"chunk_seconds": chunk})
    assert "engine.whistle.chunk_seconds" in message
    assert repr(chunk) in message
    assert "0.25" in message


@pytest.mark.parametrize(
    ("window", "chunk"),
    [(5.0, 5.0), (5.0, 6.0), (2.0, 3.0), (30.0, 30.0)],
)
def test_chunk_at_or_above_window_is_rejected(window: float, chunk: float) -> None:
    message = _error_message({"window_seconds": window, "chunk_seconds": chunk})
    assert "engine.whistle.chunk_seconds" in message
    assert "window_seconds" in message
    assert repr(window) in message
    assert repr(chunk) in message


@pytest.mark.parametrize("window", [30.0001, 31, 45.0, 1e9])
def test_window_above_the_engine_limit_is_rejected(window: float) -> None:
    message = _error_message({"window_seconds": window, "chunk_seconds": 0.25})
    assert "engine.whistle.window_seconds" in message
    assert repr(float(window)) in message
    assert "at most 30" in message


def test_range_errors_name_the_key_the_value_and_the_allowed_range() -> None:
    cases = [
        ({"chunk_seconds": 0.1}, "engine.whistle.chunk_seconds", "0.1", "0.25"),
        (
            {"window_seconds": 5, "chunk_seconds": 5},
            "engine.whistle.chunk_seconds",
            "5.0",
            "less than window_seconds = 5.0",
        ),
        ({"window_seconds": 31}, "engine.whistle.window_seconds", "31.0", "at most 30"),
    ]
    for raw, key, value, allowed in cases:
        message = _error_message(raw)
        assert key in message, message
        assert value in message, message
        assert allowed in message, message


# --- range validation: boundaries and the latency warning ---------------------


def test_chunk_exactly_at_the_minimum_is_accepted() -> None:
    result = WhistleConfig.from_mapping({"window_seconds": 5, "chunk_seconds": 0.25})
    assert result.warnings == []
    assert result.config.chunk_seconds == 0.25


def test_window_exactly_at_the_engine_limit_is_accepted_with_a_warning() -> None:
    result = WhistleConfig.from_mapping({"window_seconds": 30, "chunk_seconds": 1})
    assert result.config.window_seconds == 30.0
    assert len(result.warnings) == 1
    assert "latency" in result.warnings[0]


@pytest.mark.parametrize("window", [5, 5.0, 6, 6.0])
def test_window_at_or_below_the_latency_knee_does_not_warn(window: float) -> None:
    result = WhistleConfig.from_mapping({"window_seconds": window, "chunk_seconds": 1})
    assert result.warnings == []


@pytest.mark.parametrize("window", [6.0001, 7, 8.0, 29.9, 30.0])
def test_window_above_the_latency_knee_warns_but_does_not_raise(window: float) -> None:
    result = WhistleConfig.from_mapping({"window_seconds": window, "chunk_seconds": 1})
    assert result.config.window_seconds == float(window)
    assert len(result.warnings) == 1
    warning = result.warnings[0]
    assert "engine.whistle.window_seconds" in warning
    assert "latency" in warning
    assert "6" in warning
