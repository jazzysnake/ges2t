"""Generic configuration loading for ges2t.

Implements ADR 0009: the configuration lives in TOML at
``$XDG_CONFIG_HOME/ges2t/config.toml`` (default ``~/.config/ges2t/config.toml``)
and is read with the standard library ``tomllib``.

The schema has two layers and the split is load-bearing:

* the **generic layer** (this module) understands every key outside
  ``[engine.*]`` and ``[injection.*]`` and owns the two *selectors*,
  ``transcriber.engine`` and ``injection.backend``;
* every ``[engine.<name>]`` and ``[injection.<name>]`` table is passed through
  **uninterpreted** as a raw ``Mapping[str, Any]`` for the selected backend's
  adapter to validate.

Nothing in this module knows what a key inside an ``[engine.*]`` or
``[injection.*]`` table means. ADR 0009's generic/backend-block pattern applies
to injectors exactly as it does to engines, so the plumbing is the same for
both. ``tomllib`` is used here and nowhere else: this is the only module that
opens the configuration file.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "AUDIO_DEFAULT_DEVICE",
    "BATCH_ONLY_ENGINES",
    "DEFAULT_ENGINE",
    "ENGINE_ALIASES",
    "INJECTION_DEFAULT_BACKEND",
    "KNOWN_ENGINES",
    "WHISPER_CPP_ENGINE",
    "WHISTLE_ENGINE",
    "KNOWN_HOTKEY_MODES",
    "AudioConfig",
    "Config",
    "ConfigError",
    "ConfigLoadResult",
    "HotkeyConfig",
    "InjectionConfig",
    "StreamingConfig",
    "TranscriberConfig",
    "UIConfig",
    "default_config",
    "default_config_path",
    "load_config",
    "normalize_engine",
    "resolve_config_path",
    "suggest_engine",
]

#: The engine value of the shipped default: the Whistle model on the Needle 3
#: engine. Kept last among the two because :data:`KNOWN_ENGINES` orders it first.
WHISTLE_ENGINE: str = "whistle"

#: The **canonical** engine value of the whisper.cpp backend (ADR 0021). A TOML
#: block name cannot contain the dot of the CLI token, so the underscore form is
#: canonical and ``whisper.cpp`` is accepted only as an alias (ticket 47).
WHISPER_CPP_ENGINE: str = "whisper_cpp"

#: Engine names the generic layer accepts as ``transcriber.engine``. This is the
#: single source of truth: the factory (ADR 0010) and the error message below
#: both read it, so they cannot drift apart. Adding an engine means adding its
#: name here and a matching ``[engine.<name>]`` adapter; the schema does not change.
#: The **first** entry is the default (:data:`DEFAULT_ENGINE`); ``whistle`` stays
#: first so the shipped live/incremental typing is unchanged (ADR 0021 §1).
KNOWN_ENGINES: tuple[str, ...] = (WHISTLE_ENGINE, WHISPER_CPP_ENGINE)

#: Non-canonical spellings the config layer forgives, folded to the canonical
#: engine value by :func:`normalize_engine`. The ``whisper.cpp`` entry is the
#: **CLI token** ``ges2t setup`` takes: a user who copies it into
#: ``transcriber.engine`` must not be told the value is unknown (ticket 47).
ENGINE_ALIASES: Mapping[str, str] = {"whisper.cpp": WHISPER_CPP_ENGINE}

#: The default engine selector, kept in sync with :data:`KNOWN_ENGINES`.
DEFAULT_ENGINE: str = KNOWN_ENGINES[0]

#: Engines whose shipped implementation is **batch-only**: each declares
#: ``Transcriber.supports_streaming = False`` (ADR 0010; ADR 0021 §3 for
#: whisper.cpp), so streaming on that engine is not possible at all. An omitted
#: ``streaming.enabled`` resolves to ``False`` for these engines and to ``True``
#: otherwise; an **explicit** value always wins (ticket 50). This is a pure
#: engine-name declaration, not a hardware probe: the device-aware model default
#: stays at the composition root (ADR 0009). A guard test builds every engine in
#: :data:`KNOWN_ENGINES` and asserts this set agrees with
#: ``Transcriber.supports_streaming``, so the two cannot drift.
BATCH_ONLY_ENGINES: frozenset[str] = frozenset({WHISPER_CPP_ENGINE})

#: The only ``hotkey.mode`` values the generic layer accepts (ADR 0009).
KNOWN_HOTKEY_MODES: tuple[str, ...] = ("ptt", "toggle")

#: Default ``injection.backend`` (ADR 0003).
INJECTION_DEFAULT_BACKEND: str = "ydotool"

#: Default ``audio.device``: the empty string means "system default" (ADR 0008).
AUDIO_DEFAULT_DEVICE: str = ""

CONFIG_DIRECTORY_NAME = "ges2t"
CONFIG_FILE_NAME = "config.toml"

_TOP_LEVEL_KEYS = frozenset(
    {
        "transcriber",
        "hotkey",
        "audio",
        "injection",
        "ui",
        "streaming",
        "engine",
    }
)
_TRANSCRIBER_KEYS = frozenset({"engine", "model_path"})
_HOTKEY_KEYS = frozenset({"mode", "key"})
_AUDIO_KEYS = frozenset({"device"})
_INJECTION_KEYS = frozenset({"backend"})
_UI_KEYS = frozenset({"tray", "notifications", "trailing_space"})
_STREAMING_KEYS = frozenset({"enabled"})


class ConfigError(Exception):
    """The configuration file is unreadable, malformed, or has a wrong-typed value.

    Every message names the offending file and key so ``ges2t doctor`` can print
    it verbatim (ADR 0009). Unknown keys are *not* raised as errors; they are
    returned as warnings by :func:`load_config`.
    """


@dataclass(frozen=True)
class TranscriberConfig:
    """Generic transcription settings, including the engine selector."""

    engine: str = DEFAULT_ENGINE
    model_path: str = ""


@dataclass(frozen=True)
class HotkeyConfig:
    """Hotkey settings. ``key`` is documentation only; the binding lives in the compositor."""

    mode: str = KNOWN_HOTKEY_MODES[0]
    key: str = ""


@dataclass(frozen=True)
class AudioConfig:
    """Audio capture settings. An empty ``device`` means the system default."""

    device: str = AUDIO_DEFAULT_DEVICE


@dataclass(frozen=True)
class InjectionConfig:
    """Text injection settings; ``backend`` selects the injector implementation."""

    backend: str = INJECTION_DEFAULT_BACKEND


@dataclass(frozen=True)
class UIConfig:
    """User-interface behaviours the app itself understands."""

    tray: bool = True
    notifications: bool = True
    trailing_space: bool = True


@dataclass(frozen=True)
class StreamingConfig:
    """App-level streaming switch; engines own their own streaming knobs.

    ``enabled`` is the **effective** value after the engine-aware default: for
    an engine in :data:`BATCH_ONLY_ENGINES` an omitted key resolves to ``False``
    (streaming is impossible there anyway, ADR 0010), and it resolves to ``True``
    for an engine that streams (ADR 0011). An explicitly written value is used
    verbatim, so ``enabled = true`` on a batch-only engine is still an explicit
    request and still refused at startup (ticket 45/50). :func:`default_config`,
    which describes the shipped ``whistle`` default, is therefore ``True``.
    """

    enabled: bool = True


@dataclass(frozen=True)
class Config:
    """The fully resolved generic configuration.

    ``engine_blocks`` holds every ``[engine.<name>]`` table with its keys
    normalised to the canonical engine value (``transcriber.engine``), including
    blocks for engines that are not currently selected. The generic layer never
    reads *inside* them; only the *name* is normalised, so
    ``transcriber.engine = "whisper.cpp"`` and ``[engine.whisper.cpp]`` both
    resolve to the ``whisper_cpp`` block (ticket 47). ``injection_blocks`` is the
    same idea for ``[injection.<name>]``: raw, uninterpreted, and complete, so a
    backend that is not currently selected is preserved for when the user
    switches back.
    """

    transcriber: TranscriberConfig = TranscriberConfig()
    hotkey: HotkeyConfig = HotkeyConfig()
    audio: AudioConfig = AudioConfig()
    injection: InjectionConfig = InjectionConfig()
    ui: UIConfig = UIConfig()
    streaming: StreamingConfig = StreamingConfig()
    engine_blocks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    injection_blocks: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class _EngineBlock:
    """One ``[engine.<name>]`` table after its name is normalised (ADR 0019).

    ``canonical`` is the engine the block configures; ``spelling`` is the name it
    was written under (so a duplicate error can name both spellings); ``body`` is
    the raw, uninterpreted mapping that crosses the boundary to the adapter
    (ADR 0009).
    """

    canonical: str
    spelling: str
    body: Mapping[str, Any]


@dataclass(frozen=True)
class ConfigLoadResult:
    """The loaded configuration plus the non-fatal problems found while loading.

    ``warnings`` are unknown generic keys, surfaced so ``ges2t doctor`` can
    print them; they never prevent the configuration from being used.
    """

    config: Config
    warnings: list[str]


def default_config() -> Config:
    """Return the configuration used when no file exists (ADR 0009: all defaults)."""
    return Config()


def default_config_path() -> Path:
    """Return the default configuration path, honouring ``XDG_CONFIG_HOME``."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config_home) if xdg_config_home else Path.home() / ".config"
    return base / CONFIG_DIRECTORY_NAME / CONFIG_FILE_NAME


def resolve_config_path(flag: str | Path | None) -> Path:
    """Resolve the ``--config <path>`` flag against the XDG default.

    An absent flag (``None``) yields :func:`default_config_path`; a supplied
    path is used as given, with no existence check.
    """
    if flag is None:
        return default_config_path()
    return Path(flag)


def normalize_engine(name: str) -> str:
    """Return the canonical engine value for a user-supplied spelling.

    ``whisper_cpp`` is canonical and ``whisper.cpp`` -- the token ``ges2t setup``
    takes -- is folded to it (ticket 47). An unrecognised spelling is returned
    unchanged, so the caller can report it against :data:`KNOWN_ENGINES` without
    this function having to raise; that keeps the one normaliser usable from the
    CLI parser, the config layer and the engine-block reader alike.
    """
    return ENGINE_ALIASES.get(name, name)


def suggest_engine(name: str) -> str | None:
    """Return the canonical engine a near-miss spelling probably meant, or ``None``.

    Matching folds case and the separators that distinguish the two spellings
    (``.``, ``-``, ``_``), so ``Whisper.CPP``, ``whisper-cpp`` and a bare
    ``whisper`` all point at ``whisper_cpp``. This is guidance only: the value is
    still rejected, it is never silently substituted (ADR 0020).
    """
    key = _engine_key(name)
    if not key:
        return None
    spellings: tuple[tuple[str, str], ...] = (
        *((alias, canonical) for alias, canonical in ENGINE_ALIASES.items()),
        *((engine, engine) for engine in KNOWN_ENGINES),
    )
    for spelling, canonical in spellings:
        spelling_key = _engine_key(spelling)
        if key == spelling_key or (len(key) >= 4 and spelling_key.startswith(key)):
            return canonical
    return None


def _engine_key(name: str) -> str:
    """Fold a spelling to lowercase alphanumerics for :func:`suggest_engine`."""
    return "".join(character for character in name.lower() if character.isalnum())


def _unknown_engine_error(path: Path, name: str) -> ConfigError:
    """Build the rejection that names the valid set and, when possible, the right value.

    The valid set is :data:`KNOWN_ENGINES`; when the rejected value looks like the
    CLI token (``whisper.cpp``) or a near miss (``whisper``), the message names
    the canonical value and the ``ges2t setup`` token, so the spelling trap is
    self-correcting (ticket 47).
    """
    known = ", ".join(repr(engine) for engine in KNOWN_ENGINES)
    message = (
        f"{path}: transcriber.engine = {name!r} is not a known engine; "
        f"known engines: {known}"
    )
    suggestion = suggest_engine(name)
    if suggestion is None:
        return ConfigError(message)
    message += f"; did you mean {suggestion!r}?"
    if suggestion == WHISPER_CPP_ENGINE:
        cli_token = next(
            alias for alias, canonical in ENGINE_ALIASES.items()
            if canonical == WHISPER_CPP_ENGINE
        )
        message += (
            f" ('{cli_token}' is the spelling 'ges2t setup' takes; the config "
            f"value is {WHISPER_CPP_ENGINE!r})"
        )
    return ConfigError(message)


def load_config(path: Path | None = None) -> ConfigLoadResult:
    """Load and validate the generic configuration (ADR 0009).

    A missing file yields :func:`default_config` and is not an error. Invalid
    TOML or a wrong-typed value raises :class:`ConfigError`. Unknown keys in
    the generic sections are collected into :attr:`ConfigLoadResult.warnings`.
    Keys inside ``[engine.*]`` tables are never inspected: every such table
    crosses the boundary verbatim as a raw mapping, with only its *name*
    normalised to the canonical engine value (ticket 47).
    ``[injection.<name>]`` blocks are preserved the same way, while
    ``injection.backend`` is the selector.
    """
    resolved = path if path is not None else default_config_path()

    if not resolved.exists():
        return ConfigLoadResult(config=default_config(), warnings=[])

    raw = _read_toml(resolved)
    warnings: list[str] = []
    _warn_unknown_keys(raw, _TOP_LEVEL_KEYS, path=resolved, section=None, warnings=warnings)

    transcriber_table = _expect_table(raw, "transcriber", path=resolved)
    hotkey_table = _expect_table(raw, "hotkey", path=resolved)
    audio_table = _expect_table(raw, "audio", path=resolved)
    injection_table = _expect_table(raw, "injection", path=resolved)
    ui_table = _expect_table(raw, "ui", path=resolved)
    streaming_table = _expect_table(raw, "streaming", path=resolved)

    _warn_unknown_keys(
        transcriber_table,
        _TRANSCRIBER_KEYS,
        path=resolved,
        section="transcriber",
        warnings=warnings,
    )
    _warn_unknown_keys(
        hotkey_table, _HOTKEY_KEYS, path=resolved, section="hotkey", warnings=warnings
    )
    _warn_unknown_keys(
        audio_table, _AUDIO_KEYS, path=resolved, section="audio", warnings=warnings
    )
    # ``injection`` is validated by hand: it mixes the ``backend`` selector with
    # nested ``[injection.<name>]`` blocks, so the generic ``_warn_unknown_keys``
    # would flag every block name as an unknown key.
    _warn_unknown_injection_keys(injection_table, path=resolved, warnings=warnings)
    _warn_unknown_keys(ui_table, _UI_KEYS, path=resolved, section="ui", warnings=warnings)
    _warn_unknown_keys(
        streaming_table, _STREAMING_KEYS, path=resolved, section="streaming", warnings=warnings
    )

    # The selector is normalised before it is either stored or checked, so
    # ``whisper.cpp`` and ``whisper_cpp`` produce the same ``TranscriberConfig``
    # and the same engine block is read for both (ticket 47).
    engine_value = normalize_engine(
        _read_str(
            transcriber_table,
            "engine",
            default=DEFAULT_ENGINE,
            path=resolved,
            section="transcriber",
        )
    )
    if engine_value not in KNOWN_ENGINES:
        raise _unknown_engine_error(resolved, engine_value)
    transcriber = TranscriberConfig(
        engine=engine_value,
        model_path=_read_str(
            transcriber_table, "model_path", default="", path=resolved, section="transcriber"
        ),
    )

    hotkey = HotkeyConfig(
        mode=_read_str(
            hotkey_table,
            "mode",
            default=KNOWN_HOTKEY_MODES[0],
            path=resolved,
            section="hotkey",
        ),
        key=_read_str(hotkey_table, "key", default="", path=resolved, section="hotkey"),
    )
    if hotkey.mode not in KNOWN_HOTKEY_MODES:
        allowed = " and ".join(repr(mode) for mode in KNOWN_HOTKEY_MODES)
        raise ConfigError(
            f"{resolved}: hotkey.mode = {hotkey.mode!r} is not supported; "
            f"expected {allowed}"
        )

    config = Config(
        transcriber=transcriber,
        hotkey=hotkey,
        audio=AudioConfig(
            device=_read_str(audio_table, "device", default="", path=resolved, section="audio")
        ),
        injection=InjectionConfig(
            backend=_read_str(
                injection_table,
                "backend",
                default=INJECTION_DEFAULT_BACKEND,
                path=resolved,
                section="injection",
            )
        ),
        ui=UIConfig(
            tray=_read_bool(ui_table, "tray", default=True, path=resolved, section="ui"),
            notifications=_read_bool(
                ui_table, "notifications", default=True, path=resolved, section="ui"
            ),
            trailing_space=_read_bool(
                ui_table, "trailing_space", default=True, path=resolved, section="ui"
            ),
        ),
        streaming=StreamingConfig(
            enabled=_read_bool(
                streaming_table,
                "enabled",
                default=engine_value not in BATCH_ONLY_ENGINES,
                path=resolved,
                section="streaming",
            )
        ),
        engine_blocks=_read_engine_blocks(raw, path=resolved),
        injection_blocks=_read_injection_blocks(raw, path=resolved),
    )
    return ConfigLoadResult(config=config, warnings=warnings)


def _read_toml(path: Path) -> Mapping[str, Any]:
    """Parse the file, converting parse and read failures into :class:`ConfigError`."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{path}: invalid TOML: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"{path}: cannot read configuration: {exc}") from exc


def _expect_table(raw: Mapping[str, Any], key: str, *, path: Path) -> Mapping[str, Any]:
    """Return the TOML table at ``key``, or an empty mapping when it is absent."""
    if key not in raw:
        return {}
    value = raw[key]
    if not isinstance(value, Mapping):
        raise ConfigError(
            f"{path}: key {key!r}: expected table, got {_describe_value(value)}"
        )
    return value


def _read_engine_blocks(raw: Mapping[str, Any], *, path: Path) -> dict[str, Mapping[str, Any]]:
    """Collect every ``[engine.<name>]`` table, keyed by canonical engine name.

    Block names go through :func:`normalize_engine` for the same reason the
    selector does: ``transcriber.engine = "whisper.cpp"`` normalises to
    ``whisper_cpp``, so the block it reads must be keyed ``whisper_cpp`` too
    (ticket 47). A block written with an alias spelling is read as well, either
    as a quoted key (``[engine."whisper.cpp"]``) or in TOML's dotted form
    (``[engine.whisper.cpp]``, which parses as ``{"whisper": {"cpp": …}}``).
    Two spellings of the same engine in one file are a hard error naming both,
    rather than a silent precedence.
    """
    table = _expect_table(raw, "engine", path=path)
    blocks: dict[str, Mapping[str, Any]] = {}
    spellings: dict[str, str] = {}
    for name, block in table.items():
        if not isinstance(block, Mapping):
            dotted = f"engine.{name}"
            raise ConfigError(
                f"{path}: key {dotted!r}: expected table, got {_describe_value(block)}"
            )
        resolved = _canonical_engine_block(name, block)
        if resolved is None:
            # A block for an engine this build does not know: preserved verbatim
            # and ignored, exactly as ADR 0009 requires (a user may keep it while
            # running another engine).
            blocks[name] = block
            continue
        if resolved.canonical in blocks:
            first = spellings[resolved.canonical]
            raise ConfigError(
                f"{path}: engine blocks {'engine.' + first!r} and "
                f"{'engine.' + resolved.spelling!r} are both spellings of the "
                f"{resolved.canonical!r} engine; keep one"
            )
        spellings[resolved.canonical] = resolved.spelling
        blocks[resolved.canonical] = resolved.body
    return blocks


def _canonical_engine_block(
    name: str, block: Mapping[str, Any]
) -> _EngineBlock | None:
    """Resolve one parsed ``[engine.<name>]`` entry to its canonical engine.

    ``None`` means the entry is not an engine this build knows, so it stays an
    ignored block. A named alias (``whisper.cpp``) resolves directly; TOML's
    dotted form resolves by folding the single nested key through the same
    normaliser, so ``[engine.whisper.cpp]`` reads as ``[engine.whisper_cpp]``.
    """
    canonical = normalize_engine(name)
    if canonical in KNOWN_ENGINES:
        return _EngineBlock(canonical=canonical, spelling=name, body=block)
    if len(block) == 1:
        ((nested_name, nested_body),) = block.items()
        dotted = f"{name}.{nested_name}"
        canonical_dotted = normalize_engine(dotted)
        if canonical_dotted in KNOWN_ENGINES and isinstance(nested_body, Mapping):
            return _EngineBlock(
                canonical=canonical_dotted, spelling=dotted, body=nested_body
            )
    return None


def _read_injection_blocks(
    raw: Mapping[str, Any], *, path: Path
) -> dict[str, Mapping[str, Any]]:
    """Collect every ``[injection.<name>]`` table, uninterpreted (ADR 0009).

    ``injection.backend`` is the selector and is not a block; every other key
    under ``[injection]`` whose value is a table is a backend block and crosses
    the boundary verbatim. Blocks for backends that are not selected are kept,
    exactly as unselected ``[engine.*]`` blocks are, so switching backends does
    not require re-adding configuration.
    """
    table = _expect_table(raw, "injection", path=path)
    blocks: dict[str, Mapping[str, Any]] = {}
    for name, block in table.items():
        if name == "backend":
            continue
        if isinstance(block, Mapping):
            blocks[name] = block
    return blocks


def _warn_unknown_injection_keys(
    table: Mapping[str, Any], *, path: Path, warnings: list[str]
) -> None:
    """Warn about scalar keys under ``[injection]`` that are neither ``backend`` nor a block.

    A nested table is a backend block and its *contents* are deliberately not
    inspected here -- the selected backend's adapter warns about unknown keys
    inside it, exactly as an engine adapter does. A non-table key other than
    ``backend`` is a typo at the generic level and is tolerated-with-a-warning,
    matching the generic layer's treatment of every other unknown key.
    """
    for key, value in table.items():
        if key in _INJECTION_KEYS or isinstance(value, Mapping):
            continue
        warnings.append(f"{path}: unknown key {'injection.' + key!r}")


def _warn_unknown_keys(
    table: Mapping[str, Any],
    known: frozenset[str],
    *,
    path: Path,
    section: str | None,
    warnings: list[str],
) -> None:
    """Append one warning per key outside ``known`` (ADR 0009: tolerated, but surfaced)."""
    for key in table:
        if key not in known:
            dotted = f"{section}.{key}" if section is not None else key
            warnings.append(f"{path}: unknown key {dotted!r}")


def _read_bool(
    table: Mapping[str, Any],
    key: str,
    *,
    default: bool,
    path: Path,
    section: str,
) -> bool:
    """Return a boolean key or raise :class:`ConfigError` naming expected and received types."""
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, bool):
        raise ConfigError(_type_error(path, section, key, "bool", value))
    return value


def _read_str(
    table: Mapping[str, Any],
    key: str,
    *,
    default: str,
    path: Path,
    section: str,
) -> str:
    """Return a string key or raise :class:`ConfigError` naming expected and received types."""
    if key not in table:
        return default
    value = table[key]
    if not isinstance(value, str):
        raise ConfigError(_type_error(path, section, key, "str", value))
    return value


def _type_error(path: Path, section: str, key: str, expected: str, value: object) -> str:
    """Build a message naming the file, the key, the expected type and the received type."""
    dotted = f"{section}.{key}"
    return (
        f"{path}: key {dotted!r}: expected {expected}, got {_describe_value(value)}"
    )


def _describe_value(value: object) -> str:
    """Render a value for an error message as its type name, plus the value when scalar."""
    type_name = type(value).__name__
    if isinstance(value, (bool, int, float, str)):
        return f"{type_name} ({value!r})"
    return type_name
