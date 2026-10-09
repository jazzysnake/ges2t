"""Tests for layout detection and injection-block configuration (ADR 0020, ADR 0009).

Every test injects a runner (or calls the pure parsers directly), so no test
starts ``xkbcli``, opens a D-Bus connection, or touches the developer's live
session. The exception is the captured real ``busctl`` strings, which are used
verbatim as parser fixtures.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

from ges2t.config import Config, InjectionConfig, load_config
from ges2t.injectors.layout import (
    AUTO_LAYOUT,
    CLIPBOARD_BLOCK_NAME,
    DEFAULT_KEY_DELAY_MS,
    DEFAULT_PASTE_KEYS,
    LAYOUT_COMMAND_TIMEOUT_SECONDS,
    YDOTOOL_BLOCK_NAME,
    ClipboardConfig,
    ClipboardConfigResult,
    InjectionConfigError,
    LayoutEntry,
    LayoutInfo,
    LayoutParseError,
    LayoutResolutionError,
    LayoutSource,
    ResolvedLayout,
    YdotoolConfig,
    YdotoolConfigResult,
    detect_layout,
    live_keymap_text,
    parse_active_layout_index,
    parse_layouts_list,
    resolve_layout,
)

# The exact strings captured from the live KDE session on this machine (ADR 0020):
#   $ busctl --user call org.kde.keyboard /Layouts org.kde.KeyboardLayouts getLayoutsList
#   a(sss) 1 "hu" "" "Hungarian"
#   $ busctl --user call org.kde.keyboard /Layouts org.kde.KeyboardLayouts getLayout
#   u 0
REAL_LAYOUTS_LIST = 'a(sss) 1 "hu" "" "Hungarian"\n'
REAL_GET_LAYOUT = "u 0\n"

# The same array shape with two layouts and a non-zero active index. ``busctl``
# flattens each ``(sss)`` tuple, so the strings are one run of ``3 * count``.
MULTI_LAYOUTS_LIST = 'a(sss) 2 "us" "" "English (US)" "hu" "" "Hungarian"\n'

REAL_KEYMAP = 'xkb_keymap {\n    xkb_keycodes "evdev_aliases(qwertz)" {\n        minimum = 8;\n'


def _completed(
    stdout: str = "", *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[bytes]:
    """Build a ``CompletedProcess`` with encoded output."""
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout=stdout.encode("utf-8"),
        stderr=stderr.encode("utf-8"),
    )


class RecordingRunner:
    """An injected runner that dispatches on the last argv element and records calls.

    ``outputs`` maps the subcommand (``"dump-keymap-wayland"``, ``"getLayoutsList"``,
    ``"getLayout"``) to either a ``CompletedProcess`` to return or an exception to
    raise. Every call is recorded so a test can assert the exact argv and timeout.
    """

    def __init__(
        self, outputs: Mapping[str, subprocess.CompletedProcess[bytes] | BaseException]
    ) -> None:
        self.outputs = dict(outputs)
        self.calls: list[tuple[str, ...]] = []
        self.timeouts: list[float] = []

    def __call__(
        self, argv: Sequence[str], timeout: float
    ) -> subprocess.CompletedProcess[bytes]:
        self.calls.append(tuple(argv))
        self.timeouts.append(timeout)
        result = self.outputs[argv[-1]]
        if isinstance(result, BaseException):
            raise result
        return result


def _runner(**outputs: subprocess.CompletedProcess[bytes] | BaseException) -> RecordingRunner:
    """Build a :class:`RecordingRunner` from keyword arguments keyed by subcommand."""
    return RecordingRunner(outputs)


# --- parsing real ``busctl`` output -------------------------------------------


def test_parse_real_get_layouts_list_output() -> None:
    entries = parse_layouts_list(REAL_LAYOUTS_LIST)
    assert entries == (LayoutEntry(layout="hu", variant="", display_name="Hungarian"),)


def test_parse_multi_layout_output_with_a_non_zero_active_index_keeps_every_entry() -> None:
    entries = parse_layouts_list(MULTI_LAYOUTS_LIST)
    assert entries == (
        LayoutEntry(layout="us", variant="", display_name="English (US)"),
        LayoutEntry(layout="hu", variant="", display_name="Hungarian"),
    )


def test_parse_layouts_list_preserves_spaces_and_escapes_in_display_names() -> None:
    entries = parse_layouts_list('a(sss) 2 "de" "" "German (with \\"quotes\\")" "us" "intl" "US"\n')
    assert entries[0].display_name == 'German (with "quotes")'
    assert entries[1] == LayoutEntry(layout="us", variant="intl", display_name="US")


def test_parse_layouts_list_accepts_an_empty_array() -> None:
    assert parse_layouts_list("a(sss) 0\n") == ()


@pytest.mark.parametrize(
    "output",
    [
        "",
        "a(sss) 1\n",
        'as 1 "hu"\n',
        'a(sss) one "hu" "" "Hungarian"\n',
        'a(sss) 2 "hu" "" "Hungarian"\n',
        'a(sss) 1 "hu" "" "unterminated\n',
    ],
)
def test_parse_layouts_list_rejects_garbage(output: str) -> None:
    with pytest.raises(LayoutParseError):
        parse_layouts_list(output)


def test_parse_real_get_layout_output() -> None:
    assert parse_active_layout_index(REAL_GET_LAYOUT) == 0


def test_parse_active_layout_index_accepts_a_non_zero_index() -> None:
    assert parse_active_layout_index("u 1\n") == 1


@pytest.mark.parametrize(
    "output",
    ["", "u\n", "s 0\n", "u one\n", "u -1\n", "u 0 extra\n"],
)
def test_parse_active_layout_index_rejects_garbage(output: str) -> None:
    with pytest.raises(LayoutParseError):
        parse_active_layout_index(output)


# --- detect_layout ------------------------------------------------------------


def test_detect_layout_on_the_real_single_layout_session() -> None:
    runner = _runner(getLayoutsList=_completed(REAL_LAYOUTS_LIST), getLayout=_completed(REAL_GET_LAYOUT))
    info = detect_layout(runner=runner)
    assert info == LayoutInfo(layout="hu", variant="", display_name="Hungarian", index=0)
    assert [call[-1] for call in runner.calls] == ["getLayoutsList", "getLayout"]


def test_detect_layout_selects_the_active_entry_in_a_multi_layout_session() -> None:
    runner = _runner(getLayoutsList=_completed(MULTI_LAYOUTS_LIST), getLayout=_completed("u 1\n"))
    info = detect_layout(runner=runner)
    assert info == LayoutInfo(layout="hu", variant="", display_name="Hungarian", index=1)


def test_detect_layout_returns_none_when_the_service_is_absent() -> None:
    runner = _runner(getLayoutsList=FileNotFoundError("busctl is not installed"))
    assert detect_layout(runner=runner) is None


def test_detect_layout_returns_none_on_a_non_zero_exit() -> None:
    runner = _runner(
        getLayoutsList=_completed("", returncode=1, stderr="Call failed: no such service")
    )
    assert detect_layout(runner=runner) is None


def test_detect_layout_returns_none_on_garbage_output() -> None:
    runner = _runner(getLayoutsList=_completed("this is not busctl output"), getLayout=_completed("u 0\n"))
    assert detect_layout(runner=runner) is None


def test_detect_layout_returns_none_on_a_timeout() -> None:
    runner = _runner(getLayoutsList=subprocess.TimeoutExpired(cmd="busctl", timeout=2.0))
    assert detect_layout(runner=runner) is None


def test_detect_layout_returns_none_when_the_active_layout_call_fails() -> None:
    runner = _runner(
        getLayoutsList=_completed(REAL_LAYOUTS_LIST),
        getLayout=_completed("", returncode=1),
    )
    assert detect_layout(runner=runner) is None


def test_detect_layout_returns_none_for_an_out_of_range_index() -> None:
    runner = _runner(getLayoutsList=_completed(REAL_LAYOUTS_LIST), getLayout=_completed("u 3\n"))
    assert detect_layout(runner=runner) is None


def test_detect_layout_returns_none_for_an_empty_layout_list() -> None:
    runner = _runner(getLayoutsList=_completed("a(sss) 0\n"), getLayout=_completed("u 0\n"))
    assert detect_layout(runner=runner) is None


def test_detect_layout_passes_a_bounded_timeout_to_every_call() -> None:
    runner = _runner(getLayoutsList=_completed(REAL_LAYOUTS_LIST), getLayout=_completed(REAL_GET_LAYOUT))
    detect_layout(runner=runner)
    assert runner.timeouts == [LAYOUT_COMMAND_TIMEOUT_SECONDS, LAYOUT_COMMAND_TIMEOUT_SECONDS]
    assert 0 < LAYOUT_COMMAND_TIMEOUT_SECONDS < 60


# --- live_keymap_text ---------------------------------------------------------


def test_live_keymap_text_returns_the_keymap_verbatim() -> None:
    runner = _runner(**{"dump-keymap-wayland": _completed(REAL_KEYMAP)})
    assert live_keymap_text(runner=runner) == REAL_KEYMAP
    assert runner.calls == [("xkbcli", "dump-keymap-wayland")]


def test_live_keymap_text_returns_none_when_xkbcli_is_absent() -> None:
    runner = _runner(**{"dump-keymap-wayland": FileNotFoundError("no xkbcli")})
    assert live_keymap_text(runner=runner) is None


def test_live_keymap_text_returns_none_on_a_non_zero_exit() -> None:
    runner = _runner(**{"dump-keymap-wayland": _completed("", returncode=1)})
    assert live_keymap_text(runner=runner) is None


def test_live_keymap_text_returns_none_on_empty_output() -> None:
    runner = _runner(**{"dump-keymap-wayland": _completed("   \n")})
    assert live_keymap_text(runner=runner) is None


def test_live_keymap_text_returns_none_on_a_timeout() -> None:
    runner = _runner(**{"dump-keymap-wayland": subprocess.TimeoutExpired(cmd="xkbcli", timeout=2.0)})
    assert live_keymap_text(runner=runner) is None


def test_live_keymap_text_uses_a_bounded_timeout() -> None:
    runner = _runner(**{"dump-keymap-wayland": _completed(REAL_KEYMAP)})
    live_keymap_text(runner=runner)
    assert runner.timeouts == [LAYOUT_COMMAND_TIMEOUT_SECONDS]


# --- resolution order ---------------------------------------------------------


def test_explicit_configured_layout_wins_and_probes_nothing() -> None:
    runner = _runner()  # any call would raise KeyError
    resolved = resolve_layout(configured_layout="hu", configured_variant="", runner=runner)
    assert resolved == ResolvedLayout(
        source=LayoutSource.CONFIGURED,
        layout="hu",
        variant="",
        display_name=None,
        keymap_text=None,
    )
    assert runner.calls == []


def test_explicit_configured_layout_overrides_a_different_detected_layout() -> None:
    runner = _runner(
        **{"dump-keymap-wayland": _completed(REAL_KEYMAP)},
        getLayoutsList=_completed(REAL_LAYOUTS_LIST),
        getLayout=_completed(REAL_GET_LAYOUT),
    )
    resolved = resolve_layout(configured_layout="us", configured_variant="intl", runner=runner)
    assert resolved.source is LayoutSource.CONFIGURED
    assert (resolved.layout, resolved.variant) == ("us", "intl")
    assert runner.calls == [], "an explicit layout must not probe the compositor"


def test_auto_uses_the_live_keymap_before_detection() -> None:
    runner = _runner(
        **{"dump-keymap-wayland": _completed(REAL_KEYMAP)},
        getLayoutsList=_completed(REAL_LAYOUTS_LIST),
        getLayout=_completed(REAL_GET_LAYOUT),
    )
    resolved = resolve_layout(runner=runner)
    assert resolved.source is LayoutSource.LIVE_KEYMAP
    assert resolved.keymap_text == REAL_KEYMAP
    assert resolved.layout is None
    assert [call[-1] for call in runner.calls] == ["dump-keymap-wayland"]


def test_falls_back_to_detected_names_when_the_keymap_is_unavailable() -> None:
    runner = _runner(
        **{"dump-keymap-wayland": _completed("", returncode=1)},
        getLayoutsList=_completed(REAL_LAYOUTS_LIST),
        getLayout=_completed(REAL_GET_LAYOUT),
    )
    resolved = resolve_layout(runner=runner)
    assert resolved.source is LayoutSource.DETECTED
    assert (resolved.layout, resolved.variant, resolved.display_name) == ("hu", "", "Hungarian")
    assert resolved.keymap_text is None


def test_detected_multi_layout_uses_the_active_index() -> None:
    runner = _runner(
        **{"dump-keymap-wayland": FileNotFoundError("no wayland session")},
        getLayoutsList=_completed(MULTI_LAYOUTS_LIST),
        getLayout=_completed("u 1\n"),
    )
    resolved = resolve_layout(runner=runner)
    assert resolved.source is LayoutSource.DETECTED
    assert resolved.layout == "hu"
    assert resolved.display_name == "Hungarian"


@pytest.mark.parametrize("configured", ["", "   ", AUTO_LAYOUT])
def test_blank_or_auto_configuration_means_detect_not_guess_us(configured: str) -> None:
    runner = _runner(
        **{"dump-keymap-wayland": _completed(REAL_KEYMAP)},
    )
    resolved = resolve_layout(configured_layout=configured, runner=runner)
    assert resolved.source is LayoutSource.LIVE_KEYMAP
    assert "us" not in (resolved.layout or "")


def test_raises_when_no_source_can_determine_the_layout() -> None:
    runner = _runner(
        **{"dump-keymap-wayland": FileNotFoundError("no xkbcli")},
        getLayoutsList=FileNotFoundError("not KDE"),
    )
    with pytest.raises(LayoutResolutionError) as excinfo:
        resolve_layout(runner=runner)
    message = str(excinfo.value)
    assert "could not determine the keyboard layout" in message
    assert "xkbcli dump-keymap-wayland" in message
    assert "org.kde.keyboard" in message
    assert "layout" in message


def test_layout_info_and_resolved_layout_are_frozen() -> None:
    info = LayoutInfo(layout="hu", variant="", display_name="Hungarian", index=0)
    with pytest.raises(FrozenInstanceError):
        info.index = 1  # type: ignore[misc]
    resolved = ResolvedLayout(
        source=LayoutSource.CONFIGURED,
        layout="hu",
        variant="",
        display_name=None,
        keymap_text=None,
    )
    with pytest.raises(FrozenInstanceError):
        resolved.source = LayoutSource.DETECTED  # type: ignore[misc]


# --- generic config: injection blocks cross the boundary verbatim -------------


def _load(tmp_path: Path, text: str) -> Config:
    """Write ``text`` to a config file and load it."""
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return load_config(path).config


def test_injection_backend_selector_and_blocks_are_preserved(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        """
[injection]
backend = "ydotool"

[injection.ydotool]
layout = "hu"
variant = ""

[injection.clipboard]
paste_keys = ["ctrl+shift+v"]
""",
    )
    assert config.injection == InjectionConfig(backend="ydotool")
    assert config.injection_blocks == {
        "ydotool": {"layout": "hu", "variant": ""},
        "clipboard": {"paste_keys": ["ctrl+shift+v"]},
    }


def test_unselected_injection_block_is_preserved_and_does_not_warn(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        """
[injection]
backend = "ydotool"

[injection.clipboard]
paste_keys = ["ctrl+v"]
totally_unknown = 42
""",
        encoding="utf-8",
    )
    result = load_config(path)
    assert result.config.transcriber.engine == "whistle"
    assert result.config.injection_blocks["clipboard"]["totally_unknown"] == 42
    assert result.warnings == [], "the generic layer does not inspect block contents"


def test_defaults_have_no_injection_blocks() -> None:
    assert Config().injection_blocks == {}


def test_unknown_scalar_key_directly_in_injection_warns(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text('[injection]\nbackend = "ydotool"\nbakend = "x"\n', encoding="utf-8")
    result = load_config(path)
    assert result.config.injection.backend == "ydotool"
    assert len(result.warnings) == 1
    assert "'injection.bakend'" in result.warnings[0]


def test_yodotool_block_from_a_real_toml_file(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        """
[injection]
backend = "ydotool"

[injection.ydotool]
layout = "auto"
variant = ""
key_delay_ms = 12
""",
    )
    result = YdotoolConfig.from_mapping(config.injection_blocks["ydotool"])
    assert result.config == YdotoolConfig(layout="auto", variant="", key_delay_ms=12)
    assert result.warnings == []


# --- [injection.ydotool] block ------------------------------------------------


def test_ydotool_config_defaults_match_the_adr_shape() -> None:
    result = YdotoolConfig.from_mapping({})
    assert isinstance(result, YdotoolConfigResult)
    assert result.config.layout == AUTO_LAYOUT == "auto"
    assert result.config.variant == ""
    assert result.config.key_delay_ms == DEFAULT_KEY_DELAY_MS == 12
    assert result.warnings == []


def test_ydotool_config_parses_every_known_key() -> None:
    result = YdotoolConfig.from_mapping(
        {"layout": "us", "variant": "intl", "key_delay_ms": 5}
    )
    assert result.config == YdotoolConfig(layout="us", variant="intl", key_delay_ms=5)
    assert result.warnings == []


def test_ydotool_config_unknown_key_warns_and_is_ignored() -> None:
    result = YdotoolConfig.from_mapping({"layout": "hu", "speed": 10})
    assert result.config == YdotoolConfig(layout="hu")
    assert len(result.warnings) == 1
    assert YDOTOOL_BLOCK_NAME in result.warnings[0]
    assert "'speed'" in result.warnings[0]


def test_ydotool_config_result_is_frozen() -> None:
    result = YdotoolConfig.from_mapping({})
    with pytest.raises(FrozenInstanceError):
        result.warnings = []  # type: ignore[misc]


@pytest.mark.parametrize("key", ["layout", "variant"])
@pytest.mark.parametrize("value", [5, True, ["hu"], {"a": 1}])
def test_ydotool_config_wrong_typed_strings_are_rejected(key: str, value: object) -> None:
    with pytest.raises(InjectionConfigError) as excinfo:
        YdotoolConfig.from_mapping({key: value})
    message = str(excinfo.value)
    assert f"{YDOTOOL_BLOCK_NAME}.{key}" in message
    assert "expected a string" in message


@pytest.mark.parametrize("value", ["fast", True, 12.5, -1])
def test_ydotool_config_invalid_key_delay_is_rejected(value: object) -> None:
    with pytest.raises(InjectionConfigError) as excinfo:
        YdotoolConfig.from_mapping({"key_delay_ms": value})
    assert "key_delay_ms" in str(excinfo.value)


def test_ydotool_config_integral_float_key_delay_is_accepted() -> None:
    # TOML `key_delay_ms = 12.0` parses as float but is a whole millisecond count.
    result = YdotoolConfig.from_mapping({"key_delay_ms": 12.0})
    assert result.config.key_delay_ms == 12
    assert isinstance(result.config.key_delay_ms, int)


# --- [injection.clipboard] block ----------------------------------------------


def test_clipboard_config_defaults_to_ctrl_v() -> None:
    result = ClipboardConfig.from_mapping({})
    assert isinstance(result, ClipboardConfigResult)
    assert result.config.paste_keys == DEFAULT_PASTE_KEYS == ("ctrl+v",)
    assert result.warnings == []


def test_clipboard_config_parses_paste_keys_into_a_tuple() -> None:
    result = ClipboardConfig.from_mapping({"paste_keys": ["ctrl+shift+v", "ctrl+insert"]})
    assert result.config.paste_keys == ("ctrl+shift+v", "ctrl+insert")
    assert result.warnings == []


def test_clipboard_config_unknown_key_warns() -> None:
    result = ClipboardConfig.from_mapping({"paste_keys": ["ctrl+v"], "typo": 1})
    assert len(result.warnings) == 1
    assert CLIPBOARD_BLOCK_NAME in result.warnings[0]
    assert "'typo'" in result.warnings[0]


@pytest.mark.parametrize(
    "value",
    ["ctrl+v", 5, ["ctrl+v", 7], [], ["  "]],
)
def test_clipboard_config_invalid_paste_keys_are_rejected(value: object) -> None:
    with pytest.raises(InjectionConfigError):
        ClipboardConfig.from_mapping({"paste_keys": value})


# --- a full end-to-end config document ----------------------------------------


FULL_INJECTION_CONFIG = """
[injection]
backend = "ydotool"

[injection.ydotool]
layout = "hu"
variant = ""
key_delay_ms = 8

[injection.clipboard]
paste_keys = ["ctrl+shift+v"]
"""


def test_full_injection_config_round_trips_through_the_parsers(tmp_path: Path) -> None:
    config = _load(tmp_path, FULL_INJECTION_CONFIG)
    ydotool = YdotoolConfig.from_mapping(
        config.injection_blocks.get(config.injection.backend, {})
    )
    clipboard = ClipboardConfig.from_mapping(config.injection_blocks.get("clipboard", {}))
    assert ydotool.config == YdotoolConfig(layout="hu", variant="", key_delay_ms=8)
    assert ydotool.warnings == []
    assert clipboard.config == ClipboardConfig(paste_keys=("ctrl+shift+v",))
    assert clipboard.warnings == []


def test_selected_block_unknown_key_warns_end_to_end(tmp_path: Path) -> None:
    config = _load(
        tmp_path,
        """
[injection]
backend = "ydotool"

[injection.ydotool]
layout = "hu"
key_delya_ms = 8
""",
    )
    block: Mapping[str, Any] = config.injection_blocks.get(config.injection.backend, {})
    result = YdotoolConfig.from_mapping(block)
    assert result.config.layout == "hu"
    assert len(result.warnings) == 1
    assert "'key_delya_ms'" in result.warnings[0]
