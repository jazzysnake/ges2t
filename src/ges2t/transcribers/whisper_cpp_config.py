"""Engine-specific configuration for the whisper.cpp transcriber (ADR 0009, ADR 0021).

The generic layer (:mod:`ges2t.config`) treats every ``[engine.<name>]`` table as
an uninterpreted raw mapping. This module is the whisper.cpp adapter's half of
that contract: it validates the ``[engine.whisper_cpp]`` block, applies its
defaults, and warns about keys it does not recognise -- the same
warn-don't-raise behaviour the generic layer and the Whistle block use.

The block owns exactly four keys:

* ``model`` -- a ``<size>-<quant>`` name from :mod:`ges2t.model_store`'s
  catalogue (e.g. ``medium-q5_0``). It is validated here so a typo fails at
  config load rather than at model acquisition.
* ``language`` -- passed through to whisper.cpp; the empty string means
  auto-detect. Any string is accepted, because the language set is upstream data
  and not our contract.
* ``threads`` -- the decoder thread count. Absent means "this machine's core
  count" (:func:`default_threads`), resolved by the adapter at load time.
* ``gpu`` -- ``auto`` (the default), ``cpu`` or ``vulkan``; see
  :class:`GpuPreference` and the device rule in
  :mod:`ges2t.transcribers.whisper_cpp`.

An absent block is normal: the generic layer never synthesizes
``[engine.whisper_cpp]``, so :meth:`WhisperCppConfig.from_mapping` accepts an
empty mapping and returns all defaults.

The **device-dependent default model** (``medium-q5_0`` on a Vulkan machine,
``small-q5_0`` otherwise -- ADR 0021) and the strict *startup* validation are the
config ticket's concerns (``whisper-backend-config-and-validation``); this module
only names a safe CPU default and does not probe the machine.

This module must stay importable with no backend present (ADR 0010): it imports
no native library and no third-party package. It reads the catalogue from
:mod:`ges2t.model_store` only to validate a model name.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ges2t import model_store

__all__ = [
    "BLOCK_NAME",
    "DEFAULT_GPU",
    "DEFAULT_LANGUAGE",
    "DEFAULT_MODEL",
    "DEFAULT_THREADS",
    "KNOWN_KEYS",
    "GpuPreference",
    "WhisperCppConfig",
    "WhisperCppConfigError",
    "WhisperCppConfigResult",
    "default_threads",
]

#: Dotted name of the block this module owns; it prefixes every message so a
#: ``doctor`` line names the exact key the user must edit.
BLOCK_NAME: str = "engine.whisper_cpp"

#: The CPU-safe default model. ADR 0021's device rule picks ``medium-q5_0`` when a
#: Vulkan device is available, but that rule belongs to the config layer (it must
#: probe the machine); this module names the CPU-safe fallback so an adapter
#: constructed without a block still works. It is ``small-q8_0``, not the ADR's
#: shorthand ``small-q5_0``: upstream does not publish a ``q5_0`` for ``small``
#: (only ``q5_1``/``q8_0``), and ges2t ships no quantiser, so ``q8_0`` -- measured
#: WER-identical to ``q5_0`` -- is the acquirable small model (ticket 41).
DEFAULT_MODEL: str = "small-q8_0"

#: Default ``language``: empty means whisper.cpp auto-detects (ADR 0009).
DEFAULT_LANGUAGE: str = ""

#: Default ``threads``: ``None`` means "the machine's core count", resolved at
#: load time by the adapter rather than frozen into the config value.
DEFAULT_THREADS: int | None = None

#: Keys this block understands. Everything else is an unknown-key warning.
KNOWN_KEYS: frozenset[str] = frozenset(
    {"model", "language", "threads", "gpu"}
)


class GpuPreference(StrEnum):
    """The user's ``gpu`` choice; ``AUTO`` is resolved against the built engine.

    ``AUTO`` uses Vulkan when the installed engine advertises a Vulkan device and
    CPU otherwise. ``VULKAN`` demands a Vulkan engine build and **fails loudly**
    when there is none, so a user who asked for the GPU is never silently moved
    to the CPU (ADR 0020's no-silent-substitution rule applied to devices).
    ``CPU`` always uses the CPU path.
    """

    AUTO = "auto"
    CPU = "cpu"
    VULKAN = "vulkan"


#: The default GPU preference, kept in sync with :class:`GpuPreference`.
DEFAULT_GPU: GpuPreference = GpuPreference.AUTO


class WhisperCppConfigError(Exception):
    """The ``[engine.whisper_cpp]`` block has a wrong-typed or invalid value.

    Every message names the offending key, the received value, and the allowed
    range/type, so ``ges2t doctor`` can print it verbatim. Unknown keys are *not*
    raised; they are returned as warnings by :meth:`WhisperCppConfig.from_mapping`
    (ADR 0009).
    """


@dataclass(frozen=True)
class WhisperCppConfig:
    """Validated ``[engine.whisper_cpp]`` settings (ADR 0009, ADR 0021)."""

    model: str = DEFAULT_MODEL
    language: str = DEFAULT_LANGUAGE
    threads: int | None = DEFAULT_THREADS
    gpu: GpuPreference = DEFAULT_GPU

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> WhisperCppConfigResult:
        """Validate a raw ``[engine.whisper_cpp]`` mapping (ADR 0009).

        An empty mapping -- the block is absent, which the generic layer never
        synthesizes -- yields all defaults with no warnings. Unknown keys are
        collected into the result's warnings and ignored. A wrong-typed or
        invalid value raises :class:`WhisperCppConfigError`.
        """
        warnings: list[str] = [
            _unknown_key_warning(key) for key in raw if key not in KNOWN_KEYS
        ]

        return WhisperCppConfigResult(
            config=cls(
                model=_read_model(raw),
                language=_read_language(raw),
                threads=_read_threads(raw),
                gpu=_read_gpu(raw),
            ),
            warnings=warnings,
        )


@dataclass(frozen=True)
class WhisperCppConfigResult:
    """The validated whisper.cpp config plus non-fatal problems found validating it.

    ``warnings`` are unknown keys. They are surfaced through ``ges2t doctor`` and
    never prevent the configuration from being used.
    """

    config: WhisperCppConfig
    warnings: list[str]


def default_threads() -> int:
    """Return the machine's usable core count, or ``1``.

    The adapter resolves ``config.threads is None`` with this, matching
    whisper.cpp's own "use every core" default while keeping it a ges2t value a
    test can pin.
    """
    return os.cpu_count() or 1


def _unknown_key_warning(key: str) -> str:
    """Build the warning for a key outside :data:`KNOWN_KEYS`."""
    return f"{BLOCK_NAME}: unknown key {key!r}"


def _read_model(raw: Mapping[str, Any]) -> str:
    """Return ``model``, validated against the catalogue, defaulting to the CPU model."""
    key = "model"
    if key not in raw:
        return DEFAULT_MODEL
    value = raw[key]
    if not isinstance(value, str):
        raise WhisperCppConfigError(
            f"{BLOCK_NAME}.{key}: expected a string, got {_describe_value(value)}"
        )
    try:
        model_store.parse_whisper_model_name(value)
    except model_store.WhisperModelNameError as exc:
        raise WhisperCppConfigError(f"{BLOCK_NAME}.{key} = {value!r}: {exc}") from exc
    return value


def _read_language(raw: Mapping[str, Any]) -> str:
    """Return ``language``, defaulting to auto-detect (any string is valid)."""
    key = "language"
    if key not in raw:
        return DEFAULT_LANGUAGE
    value = raw[key]
    if not isinstance(value, str):
        raise WhisperCppConfigError(
            f"{BLOCK_NAME}.{key}: expected a string, got {_describe_value(value)}"
        )
    return value


def _read_threads(raw: Mapping[str, Any]) -> int | None:
    """Return ``threads`` as a positive ``int``, or ``None`` for the machine default."""
    key = "threads"
    if key not in raw:
        return DEFAULT_THREADS
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise WhisperCppConfigError(
            f"{BLOCK_NAME}.{key}: expected an integer, got {_describe_value(value)}"
        )
    if value < 1:
        raise WhisperCppConfigError(
            f"{BLOCK_NAME}.{key} = {value!r} is invalid: threads must be at least 1"
        )
    return value


def _read_gpu(raw: Mapping[str, Any]) -> GpuPreference:
    """Return ``gpu`` as a :class:`GpuPreference`, defaulting to ``auto``."""
    key = "gpu"
    if key not in raw:
        return DEFAULT_GPU
    value = raw[key]
    if not isinstance(value, str):
        raise WhisperCppConfigError(
            f"{BLOCK_NAME}.{key}: expected a string, got {_describe_value(value)}"
        )
    try:
        return GpuPreference(value)
    except ValueError as exc:
        valid = ", ".join(member.value for member in GpuPreference)
        raise WhisperCppConfigError(
            f"{BLOCK_NAME}.{key} = {value!r} is invalid: valid values are {valid}"
        ) from exc


def _describe_value(value: object) -> str:
    """Render a value for an error message as its type name, plus the value when scalar."""
    type_name = type(value).__name__
    if isinstance(value, (bool, int, float, str)):
        return f"{type_name} ({value!r})"
    return type_name
