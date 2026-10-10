"""Engine-specific configuration for the Whistle transcriber (ADR 0009).

The generic layer (:mod:`ges2t.config`) treats every ``[engine.<name>]`` table as
an uninterpreted raw mapping. This module is the Whistle adapter's half of that
contract: it validates the ``[engine.whistle]`` block, applies its defaults, and
warns about keys it does not recognise -- the same warn-don't-raise behaviour the
generic layer uses.

The block owns exactly three keys:

* ``language`` -- passed through to the engine; the empty string means
  auto-detect. Any string is accepted, because the engine's language set is
  upstream data and not our contract.
* ``window_seconds`` -- the streaming commit-window cap (ADR 0011).
* ``chunk_seconds`` -- the streaming feed granularity (ADR 0011).

An absent block is normal: the generic layer never synthesizes
``[engine.whistle]``, so :meth:`WhistleConfig.from_mapping` accepts an empty
mapping and returns all defaults. Callers select the block themselves, e.g.
``WhistleConfig.from_mapping(config.engine_blocks.get(config.transcriber.engine, {}))``.

This module must stay importable with no backend present (ADR 0010): it imports
no engine and no third-party package.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ges2t.identifiers import WHISTLE_ENGINE

__all__ = [
    "BLOCK_NAME",
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_LANGUAGE",
    "DEFAULT_WINDOW_SECONDS",
    "KNOWN_KEYS",
    "LATENCY_KNEE_SECONDS",
    "MAX_WINDOW_SECONDS",
    "MIN_CHUNK_SECONDS",
    "WhistleConfig",
    "WhistleConfigError",
    "WhistleConfigResult",
]

#: Dotted name of the block this module owns; it prefixes every message so a
#: ``doctor`` line names the exact key the user must edit.
BLOCK_NAME: str = f"engine.{WHISTLE_ENGINE}"

#: Default ``language``: empty means the engine auto-detects (ADR 0009).
DEFAULT_LANGUAGE: str = ""

#: Default streaming commit-window cap in seconds (ADR 0011).
DEFAULT_WINDOW_SECONDS: float = 5.0

#: Default streaming feed granularity in seconds (ADR 0011).
DEFAULT_CHUNK_SECONDS: float = 1.0

#: Smallest accepted ``chunk_seconds``: the engine is fed about one second at a
#: time, and anything below a quarter second is pointless re-transcription (ADR 0011).
MIN_CHUNK_SECONDS: float = 0.25

#: Largest accepted ``window_seconds``: the engine's own per-call ceiling (ADR 0005).
MAX_WINDOW_SECONDS: float = 30.0

#: The measured latency knee: above this many buffered seconds the per-chunk
#: latency crosses the 1 s budget, so exceeding it is warned about, not rejected
#: (ADR 0011).
LATENCY_KNEE_SECONDS: float = 6.0

#: Keys this block understands. Everything else is an unknown-key warning.
KNOWN_KEYS: frozenset[str] = frozenset({"language", "window_seconds", "chunk_seconds"})


class WhistleConfigError(Exception):
    """The ``[engine.whistle]`` block has a wrong-typed or out-of-range value.

    Every message names the offending key, the received value, and the allowed
    range or type, so ``ges2t doctor`` can print it verbatim. Unknown keys are
    *not* raised; they are returned as warnings by
    :meth:`WhistleConfig.from_mapping`, mirroring the generic layer (ADR 0009).
    """


@dataclass(frozen=True)
class WhistleConfig:
    """Validated ``[engine.whistle]`` settings (ADR 0009, ADR 0011)."""

    language: str = DEFAULT_LANGUAGE
    window_seconds: float = DEFAULT_WINDOW_SECONDS
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WhistleConfigResult:
        """Validate a raw ``[engine.whistle]`` mapping (ADR 0009).

        An empty mapping -- the block is absent, which the generic layer never
        synthesizes -- yields all defaults with no warnings. Unknown keys are
        collected into the result's warnings and ignored. A wrong-typed or
        out-of-range value raises :class:`WhistleConfigError`.

        ``int`` and ``float`` are both accepted for the two numeric keys and
        normalised to ``float``: TOML parses ``window_seconds = 5`` as ``int``,
        and that is the spelling of the ADR's own example. ``bool`` is excluded
        explicitly, because in Python ``bool`` is an ``int`` subclass and
        ``tray = true`` must not silently become ``1.0``.
        """
        warnings: list[str] = [
            _unknown_key_warning(key) for key in raw if key not in KNOWN_KEYS
        ]

        language = _read_language(raw)
        window_seconds = _read_seconds(raw, "window_seconds", DEFAULT_WINDOW_SECONDS)
        chunk_seconds = _read_seconds(raw, "chunk_seconds", DEFAULT_CHUNK_SECONDS)

        _validate_ranges(
            window_seconds=window_seconds,
            chunk_seconds=chunk_seconds,
            warnings=warnings,
        )

        return WhistleConfigResult(
            config=cls(
                language=language,
                window_seconds=window_seconds,
                chunk_seconds=chunk_seconds,
            ),
            warnings=warnings,
        )


@dataclass(frozen=True)
class WhistleConfigResult:
    """The validated Whistle config plus non-fatal problems found while validating.

    ``warnings`` are unknown keys and the latency-budget notice. They are
    surfaced through ``ges2t doctor`` and never prevent the configuration from
    being used.
    """

    config: WhistleConfig
    warnings: list[str]


def _unknown_key_warning(key: str) -> str:
    """Build the warning for a key outside :data:`KNOWN_KEYS`."""
    return f"{BLOCK_NAME}: unknown key {key!r}"


def _read_language(raw: Mapping[str, Any]) -> str:
    """Return ``language`` when present, defaulting to auto-detect (any string is valid)."""
    key = "language"
    if key not in raw:
        return DEFAULT_LANGUAGE
    value = raw[key]
    if not isinstance(value, str):
        raise WhistleConfigError(
            f"{BLOCK_NAME}.{key}: expected a string, got {_describe_value(value)}"
        )
    return value


def _read_seconds(raw: Mapping[str, Any], key: str, default: float) -> float:
    """Return a numeric seconds key as ``float``, rejecting bools and non-finite values."""
    if key not in raw:
        return default
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WhistleConfigError(
            f"{BLOCK_NAME}.{key}: expected a number (int or float, not bool), "
            f"got {_describe_value(value)}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise WhistleConfigError(
            f"{BLOCK_NAME}.{key} = {number!r} is invalid: expected a finite number of seconds"
        )
    return number


def _validate_ranges(
    *,
    window_seconds: float,
    chunk_seconds: float,
    warnings: list[str],
) -> None:
    """Enforce the ADR 0011 ranges, appending the latency warning rather than raising it."""
    if chunk_seconds < MIN_CHUNK_SECONDS:
        raise WhistleConfigError(
            f"{BLOCK_NAME}.chunk_seconds = {chunk_seconds!r} is too small: "
            f"chunk_seconds must be at least {MIN_CHUNK_SECONDS:g} seconds"
        )
    if chunk_seconds >= window_seconds:
        raise WhistleConfigError(
            f"{BLOCK_NAME}.chunk_seconds = {chunk_seconds!r} is invalid: "
            f"chunk_seconds must be less than window_seconds = {window_seconds!r} seconds"
        )
    if window_seconds > MAX_WINDOW_SECONDS:
        raise WhistleConfigError(
            f"{BLOCK_NAME}.window_seconds = {window_seconds!r} is too large: "
            f"window_seconds must be at most {MAX_WINDOW_SECONDS:g} seconds "
            f"(the engine's per-call limit)"
        )
    if window_seconds > LATENCY_KNEE_SECONDS:
        warnings.append(
            f"{BLOCK_NAME}.window_seconds = {window_seconds!r} is too large for the "
            f"1 s latency budget: measured per-chunk latency crosses 1 s above about "
            f"{LATENCY_KNEE_SECONDS:g} seconds of buffered audio, so this knowingly "
            f"trades latency for context"
        )


def _describe_value(value: object) -> str:
    """Render a value for an error message as its type name, plus the value when scalar."""
    type_name = type(value).__name__
    if isinstance(value, (bool, int, float, str)):
        return f"{type_name} ({value!r})"
    return type_name
