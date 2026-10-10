"""Tests for the generic configuration loader (ADR 0009).

The file is written with ``Path.write_text`` because ``tomllib`` is read-only and
no TOML-writing dependency is allowed (ADR 0009).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ges2t.config import (
    DEFAULT_ENGINE,
    ENGINE_ALIASES,
    INJECTION_DEFAULT_BACKEND,
    KNOWN_ENGINES,
    KNOWN_HOTKEY_MODES,
    WHISPER_CPP_ENGINE,
    AudioConfig,
    Config,
    ConfigError,
    ConfigLoadResult,
    HotkeyConfig,
    InjectionConfig,
    StreamingConfig,
    TranscriberConfig,
    UIConfig,
    default_config,
    default_config_path,
    load_config,
    normalize_engine,
    resolve_config_path,
    suggest_engine,
)

MISSING_FILE = "definitely-not-there.toml"

FULL_CONFIG = """
[transcriber]
engine = "whistle"
model_path = "/models/whistle.cact"

[hotkey]
mode = "toggle"
key = "Meta+D"

[audio]
device = "alsa_input.usb-mic"

[injection]
backend = "ydotool"

[ui]
tray = false
notifications = false
trailing_space = false

[streaming]
enabled = false

[engine.whistle]
language = "en"
window_seconds = 5
chunk_seconds = 1
"""


def _write_config(directory: Path, text: str, name: str = "config.toml") -> Path:
    """Write ``text`` to ``directory/name`` and return the path."""
    path = directory / name
    path.write_text(text, encoding="utf-8")
    return path


def _load_error(directory: Path, text: str) -> str:
    """Load a written config, assert it raises, and return the error message."""
    path = _write_config(directory, text)
    with pytest.raises(ConfigError) as excinfo:
        load_config(path)
    message = str(excinfo.value)
    assert str(path) in message, f"error does not name the file: {message}"
    return message


# --- defaults -----------------------------------------------------------------


def test_missing_file_yields_all_defaults(tmp_path: Path) -> None:
    result = load_config(tmp_path / MISSING_FILE)
    assert result.warnings == []
    assert result.config == default_config()
    assert result.config.engine_blocks == {}


def test_empty_file_yields_all_defaults(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, ""))
    assert result.warnings == []
    assert result.config == default_config()


def test_missing_default_path_uses_the_xdg_location(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    result = load_config()
    assert result.warnings == []
    assert result.config == default_config()


def test_default_values_match_the_adr_schema() -> None:
    config = default_config()
    assert config.transcriber == TranscriberConfig(engine="whistle", model_path="")
    assert config.hotkey == HotkeyConfig(mode="ptt", key="")
    assert config.audio == AudioConfig(device="")
    assert config.injection == InjectionConfig(backend="ydotool")
    assert config.ui == UIConfig(tray=True, notifications=True, trailing_space=True)
    assert config.streaming == StreamingConfig(enabled=True)
    assert config.engine_blocks == {}


def test_engine_constants_are_consistent() -> None:
    assert isinstance(KNOWN_ENGINES, tuple)
    assert DEFAULT_ENGINE in KNOWN_ENGINES
    assert KNOWN_HOTKEY_MODES == ("ptt", "toggle")
    assert INJECTION_DEFAULT_BACKEND == "ydotool"


# --- parsing every generic key ------------------------------------------------


def test_every_generic_key_is_parsed(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, FULL_CONFIG))
    assert result.warnings == []
    config = result.config
    assert config.transcriber.engine == "whistle"
    assert config.transcriber.model_path == "/models/whistle.cact"
    assert config.hotkey.mode == "toggle"
    assert config.hotkey.key == "Meta+D"
    assert config.audio.device == "alsa_input.usb-mic"
    assert config.injection.backend == "ydotool"
    assert config.ui.tray is False
    assert config.ui.notifications is False
    assert config.ui.trailing_space is False
    assert config.streaming.enabled is False


def test_load_result_is_a_typed_frozen_dataclass(tmp_path: Path) -> None:
    result = load_config(tmp_path / MISSING_FILE)
    assert isinstance(result, ConfigLoadResult)
    assert isinstance(result.config, Config)
    assert isinstance(result.warnings, list)
    with pytest.raises(FrozenInstanceError):
        result.warnings = []  # type: ignore[misc]


def test_config_dataclasses_are_frozen(tmp_path: Path) -> None:
    config = load_config(tmp_path / MISSING_FILE).config
    with pytest.raises(FrozenInstanceError):
        config.transcriber = TranscriberConfig(engine="other", model_path="")  # type: ignore[misc]


# --- hard errors --------------------------------------------------------------


def test_invalid_toml_error_names_file_and_parse_problem(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "[ui\ntray = true\n")
    assert "invalid TOML" in message
    assert "line 1" in message


def test_wrong_typed_bool_error_names_file_key_and_both_types(tmp_path: Path) -> None:
    message = _load_error(tmp_path, '[ui]\ntray = "yes"\n')
    assert "'ui.tray'" in message
    assert "expected bool" in message
    assert "str" in message
    assert repr("yes") in message


def test_wrong_typed_int_for_bool_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "[ui]\ntray = 1\n")
    assert "expected bool" in message
    assert "int" in message


def test_wrong_typed_str_error_names_file_key_and_both_types(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "[transcriber]\nmodel_path = 5\n")
    assert "'transcriber.model_path'" in message
    assert "expected str" in message
    assert "int" in message


def test_wrong_typed_key_in_every_section_is_rejected(tmp_path: Path) -> None:
    cases = {
        "engine": '[transcriber]\nengine = 7\n',
        "hotkey mode": '[hotkey]\nmode = true\n',
        "hotkey key": "[hotkey]\nkey = 12\n",
        "audio device": "[audio]\ndevice = 3.5\n",
        "injection backend": "[injection]\nbackend = false\n",
        "notifications": "[ui]\nnotifications = 1\n",
        "trailing space": "[ui]\ntrailing_space = 0\n",
        "streaming enabled": "[streaming]\nenabled = 1\n",
    }
    for label, text in cases.items():
        with pytest.raises(ConfigError) as excinfo:
            load_config(_write_config(tmp_path, text, name=f"{label.replace(' ', '_')}.toml"))
        assert "expected" in str(excinfo.value), label


def test_section_that_is_not_a_table_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, "ui = true\n")
    assert "'ui'" in message
    assert "expected table" in message
    assert "bool" in message


def test_engine_selector_that_is_not_a_table_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, 'engine = "whistle"\n')
    assert "'engine'" in message
    assert "expected table" in message


def test_engine_block_that_is_not_a_table_is_rejected(tmp_path: Path) -> None:
    message = _load_error(tmp_path, 'engine = { whistle = "nope" }\n')
    assert "'engine.whistle'" in message
    assert "expected table" in message


def test_unknown_engine_name_lists_the_known_names(tmp_path: Path) -> None:
    message = _load_error(tmp_path, '[transcriber]\nengine = "sphinx"\n')
    assert "sphinx" in message
    assert "known engines" in message
    for name in KNOWN_ENGINES:
        assert name in message
    # 'sphinx' resembles nothing in the valid set, so no spelling advice is added.
    assert "did you mean" not in message


# --- one canonical engine value, one accepted alias (ticket 47) ---------------


def test_engine_alias_map_points_only_at_canonical_values() -> None:
    assert ENGINE_ALIASES == {"whisper.cpp": WHISPER_CPP_ENGINE}
    assert WHISPER_CPP_ENGINE in KNOWN_ENGINES


def test_normalize_engine_folds_the_cli_token_to_the_canonical_value() -> None:
    assert normalize_engine("whisper.cpp") == WHISPER_CPP_ENGINE
    assert normalize_engine("whisper_cpp") == WHISPER_CPP_ENGINE
    assert normalize_engine("whistle") == "whistle"
    # An unknown spelling is returned unchanged, so the caller can report it.
    assert normalize_engine("sphinx") == "sphinx"


@pytest.mark.parametrize(
    ("spelling", "expected"),
    [
        ("whisper", "whisper_cpp"),
        ("whisper-cpp", "whisper_cpp"),
        ("Whisper.CPP", "whisper_cpp"),
        ("WHISPER_CPP", "whisper_cpp"),
        ("whistl", "whistle"),
        ("sphinx", None),
        ("", None),
    ],
)
def test_suggest_engine_points_at_the_canonical_value(
    spelling: str, expected: str | None
) -> None:
    assert suggest_engine(spelling) == expected


def test_config_accepts_the_canonical_whisper_value(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, '[transcriber]\nengine = "whisper_cpp"\n'))
    assert result.warnings == []
    assert result.config.transcriber.engine == "whisper_cpp"


def test_config_accepts_whisper_dot_cpp_as_an_alias(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, '[transcriber]\nengine = "whisper.cpp"\n'))
    assert result.warnings == []
    assert result.config.transcriber.engine == "whisper_cpp"


def test_omitted_streaming_enabled_follows_the_engine(tmp_path: Path) -> None:
    # Ticket 50: the default follows the engine. A batch-only engine (whisper.cpp)
    # resolves an omitted switch to false, so the minimal config starts; whistle
    # keeps streaming as the default (ADR 0011).
    whistle = load_config(_write_config(tmp_path, '[transcriber]\nengine = "whistle"\n'))
    assert whistle.config.streaming.enabled is True

    whisper = load_config(
        _write_config(tmp_path, '[transcriber]\nengine = "whisper_cpp"\n')
    )
    assert whisper.config.streaming.enabled is False


def test_an_empty_streaming_table_still_follows_the_engine(tmp_path: Path) -> None:
    # A present-but-empty [streaming] table is the same as an omitted one: it
    # carries no explicit request, so the engine decides (ticket 50).
    result = load_config(
        _write_config(tmp_path, '[transcriber]\nengine = "whisper_cpp"\n\n[streaming]\n')
    )
    assert result.config.streaming.enabled is False


@pytest.mark.parametrize("engine", ["whistle", "whisper_cpp"])
def test_explicit_streaming_enabled_beats_the_engine_default(
    tmp_path: Path, engine: str
) -> None:
    # An explicit value is used verbatim for either engine, including the
    # contradictory explicit true on the batch-only engine (refused later, at
    # startup, rather than rewritten), and the explicit false that stays valid.
    for spelling in ("true", "false"):
        result = load_config(
            _write_config(
                tmp_path,
                f'[transcriber]\nengine = "{engine}"\n\n[streaming]\nenabled = {spelling}\n',
            )
        )
        assert result.config.streaming.enabled is (spelling == "true")


def test_unknown_engine_that_looks_like_the_cli_token_points_at_the_right_one(
    tmp_path: Path,
) -> None:
    message = _load_error(tmp_path, '[transcriber]\nengine = "whisper"\n')
    assert "known engines" in message
    assert WHISPER_CPP_ENGINE in message
    assert "did you mean 'whisper_cpp'?" in message
    # The config value and the CLI token are named together, which is the whole
    # point of the ticket: one thing, two spellings, explained.
    assert "whisper.cpp" in message
    assert "ges2t setup" in message


def test_unknown_engine_separator_variant_points_at_the_right_one(tmp_path: Path) -> None:
    message = _load_error(tmp_path, '[transcriber]\nengine = "Whisper.CPP"\n')
    assert "did you mean 'whisper_cpp'?" in message
    assert "whisper.cpp" in message


def test_hotkey_mode_rejects_anything_but_ptt_and_toggle(tmp_path: Path) -> None:
    message = _load_error(tmp_path, '[hotkey]\nmode = "hold"\n')
    assert "hotkey.mode" in message
    assert "hold" in message
    assert "ptt" in message
    assert "toggle" in message


@pytest.mark.parametrize("mode", ["ptt", "toggle"])
def test_hotkey_mode_accepts_ptt_and_toggle(tmp_path: Path, mode: str) -> None:
    result = load_config(_write_config(tmp_path, f'[hotkey]\nmode = "{mode}"\n'))
    assert result.config.hotkey.mode == mode
    assert result.warnings == []


# --- warnings, not errors -----------------------------------------------------


def test_unknown_key_in_a_generic_section_is_a_warning(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "[ui]\ntray = false\ntray_icon = true\n")
    result = load_config(path)
    assert result.config.ui.tray is False
    assert len(result.warnings) == 1
    assert "'ui.tray_icon'" in result.warnings[0]
    assert str(path) in result.warnings[0]


def test_unknown_top_level_key_is_a_warning(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, "nonsense = 1\n"))
    assert result.config == default_config()
    assert len(result.warnings) == 1
    assert "'nonsense'" in result.warnings[0]


def test_unknown_section_is_a_warning(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, "[ui_extra]\nfoo = 1\n"))
    assert result.config == default_config()
    assert len(result.warnings) == 1
    assert "'ui_extra'" in result.warnings[0]


def test_unknown_keys_are_reported_in_file_order(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "[hotkey]\nzzz = 1\nmode = \"ptt\"\naaa = 2\n")
    result = load_config(path)
    assert len(result.warnings) == 2
    assert "'hotkey.zzz'" in result.warnings[0]
    assert "'hotkey.aaa'" in result.warnings[1]


# --- engine blocks cross the boundary verbatim --------------------------------


def test_engine_blocks_survive_verbatim_including_unselected_engines(tmp_path: Path) -> None:
    path = _write_config(
        tmp_path,
        """
[engine.whistle]
language = "en"
window_seconds = 5
chunk_seconds = 1
nested = { a = 1, b = [1, 2, 3] }

[engine.something_else]
whatever = "kept"
flag = true
""",
    )
    result = load_config(path)
    assert result.warnings == []
    assert result.config.transcriber.engine == "whistle"
    assert result.config.engine_blocks == {
        "whistle": {
            "language": "en",
            "window_seconds": 5,
            "chunk_seconds": 1,
            "nested": {"a": 1, "b": [1, 2, 3]},
        },
        "something_else": {"whatever": "kept", "flag": True},
    }


def test_unselected_engine_block_alone_is_not_an_error(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, '[engine.whisper_cpp]\nmodel = "ggml.bin"\n'))
    assert result.warnings == []
    assert result.config.engine_blocks == {"whisper_cpp": {"model": "ggml.bin"}}


def test_alias_selector_reads_the_canonical_engine_block(tmp_path: Path) -> None:
    result = load_config(
        _write_config(
            tmp_path,
            '[transcriber]\nengine = "whisper.cpp"\n\n[engine.whisper_cpp]\nmodel = "medium-q5_0"\n',
        )
    )
    assert result.config.transcriber.engine == "whisper_cpp"
    assert result.config.engine_blocks == {"whisper_cpp": {"model": "medium-q5_0"}}


def test_quoted_alias_block_name_reads_as_the_canonical_block(tmp_path: Path) -> None:
    result = load_config(
        _write_config(tmp_path, '[engine."whisper.cpp"]\nmodel = "medium-q5_0"\n')
    )
    assert result.config.engine_blocks == {"whisper_cpp": {"model": "medium-q5_0"}}


def test_dotted_alias_block_name_reads_as_the_canonical_block(tmp_path: Path) -> None:
    # NOTE: "[engine.whisper.cpp]" is TOML's dotted form and parses as the nested
    # table {"whisper": {"cpp": {...}}}. It is folded through the same normaliser,
    # so the block is read rather than silently ignored (ticket 47).
    result = load_config(
        _write_config(tmp_path, '[engine.whisper.cpp]\nmodel = "medium-q5_0"\n')
    )
    assert result.warnings == []
    assert result.config.engine_blocks == {"whisper_cpp": {"model": "medium-q5_0"}}


def test_two_spellings_of_one_engine_block_are_rejected(tmp_path: Path) -> None:
    message = _load_error(
        tmp_path,
        '[engine.whisper_cpp]\nmodel = "a"\n\n[engine.whisper.cpp]\nmodel = "b"\n',
    )
    assert "both spellings" in message
    assert "whisper_cpp" in message
    assert "whisper.cpp" in message


# --- engine block contents are uninterpreted ----------------------------------
def test_engine_block_contents_are_not_interpreted_or_warned_about(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, "[engine.whistle]\ntotally_unknown = 42\n"))
    assert result.warnings == []
    assert result.config.engine_blocks["whistle"] == {"totally_unknown": 42}


def test_engine_blocks_are_empty_when_the_section_is_absent(tmp_path: Path) -> None:
    result = load_config(_write_config(tmp_path, "[ui]\ntray = true\n"))
    assert result.config.engine_blocks == {}


# --- --config resolution ------------------------------------------------------


def test_resolve_config_path_honours_the_flag_over_xdg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "ignored"))
    custom = tmp_path / "custom.toml"
    assert resolve_config_path(str(custom)) == custom
    assert resolve_config_path(custom) == custom


def test_config_flag_override_is_honoured_by_load_config(tmp_path: Path) -> None:
    custom = _write_config(tmp_path, "[ui]\ntray = false\n", name="custom.toml")
    result = load_config(resolve_config_path(str(custom)))
    assert result.config.ui.tray is False


def test_resolve_config_path_uses_xdg_config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    expected = tmp_path / "ges2t" / "config.toml"
    assert resolve_config_path(None) == expected
    assert default_config_path() == expected


def test_resolve_config_path_falls_back_to_home_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_config_path(None) == tmp_path / ".config" / "ges2t" / "config.toml"


def test_empty_xdg_config_home_falls_back_to_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "")
    monkeypatch.setenv("HOME", str(tmp_path))
    assert resolve_config_path(None) == tmp_path / ".config" / "ges2t" / "config.toml"
