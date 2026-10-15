"""Engine-specific configuration for the NeMo-Speech.cpp transcriber (ADR 0009, ADR 0022).

The generic layer (:mod:`ges2t.config`) treats every ``[engine.<name>]`` table as
an uninterpreted raw mapping. This module is the ``nemo_speech`` adapter's half of
that contract: it validates the ``[engine.nemo_speech]`` block, applies its
defaults, and warns about keys it does not recognise -- the same
warn-don't-raise behaviour the generic layer and the other engine blocks use
(ADR 0009).

The block owns exactly three keys:

* ``device`` -- ``auto`` (the default), ``vulkan`` or ``cpu``; see
  :class:`NemoDevicePreference` and the device rule in
  :mod:`ges2t.transcribers.nemo_speech`. ``vulkan`` on a machine without a Vulkan
  loader is a hard error rather than a silent fallback (ADR 0020).
* ``chunk_seconds`` -- the streaming feed granularity (ADR 0022). Default
  ``0.16`` (160 ms), the chunk size ticket 55 measured on this machine. The batch
  path of ticket 58 does not consume it; it is validated here so the streaming
  driver (ticket 59) and the composition root (ticket 60) read one value.
* ``model`` -- a path to a GGUF file overriding the managed pinned model. Unlike
  whisper.cpp's ``model`` (a catalogue *name*, because there are many models) this
  engine has **exactly one** pinned model and no catalogue, so a name key would be
  degenerate; a path is the only override with an effect. It mirrors the adapter's
  ``model_path_override`` seam. Omitted (``None``) means the managed pinned model,
  resolved by the adapter, not here.

``window_seconds`` is **not** a key of this block. It is meaningless for this
engine (ADR 0022: no sliding window) and therefore falls through to the ordinary
unknown-key warning, which "says so rather than silently accepting a value that
has no effect" -- exactly the behaviour ADR 0022's consequences section requires.

An absent block is normal: the generic layer never synthesizes
``[engine.nemo_speech]``, so :meth:`NemoSpeechConfig.from_mapping` accepts an empty
mapping and returns all defaults.

This module must stay importable with no backend present (ADR 0010): it imports
no native library and no third-party package. It reads the pinned model file name
from :mod:`ges2t.model_store` only to derive the accepted GGUF suffix, so the
model key and the pinned artifact cannot drift apart.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ges2t import model_store
from ges2t.identifiers import NEMO_SPEECH_ENGINE

__all__ = [
    "BLOCK_NAME",
    "DEFAULT_CHUNK_SECONDS",
    "DEFAULT_DEVICE",
    "DEFAULT_MODEL",
    "GGUF_SUFFIX",
    "KNOWN_KEYS",
    "NemoDevicePreference",
    "NemoSpeechConfig",
    "NemoSpeechConfigError",
    "NemoSpeechConfigResult",
]

#: Dotted name of the block this module owns; it prefixes every message so a
#: ``doctor`` line names the exact key the user must edit.
BLOCK_NAME: str = f"engine.{NEMO_SPEECH_ENGINE}"

#: The accepted suffix for the ``model`` override, derived from the pinned model
#: file name (``.gguf``) rather than spelled a second time (rule 1). A value that
#: does not end in it is rejected, so a whisper-style catalogue name
#: (``medium-q5_0``) given to this key fails at config load with a clear message
#: instead of being read as a relative path.
GGUF_SUFFIX: str = Path(model_store.NEMO_MODEL_FILE_NAME).suffix


class NemoDevicePreference(StrEnum):
    """The user's ``device`` choice; ``AUTO`` is resolved against the host.

    ``AUTO`` uses Vulkan when the machine has a Vulkan loader and CPU otherwise.
    ``VULKAN`` demands the GPU and **fails loudly** when there is no Vulkan
    loader, so a user who asked for the GPU is never silently moved to the CPU
    (ADR 0020's no-silent-substitution rule applied to devices). ``CPU`` always
    uses the CPU path.
    """

    AUTO = "auto"
    CPU = "cpu"
    VULKAN = "vulkan"


#: The default device preference, kept in sync with :class:`NemoDevicePreference`.
DEFAULT_DEVICE: NemoDevicePreference = NemoDevicePreference.AUTO

#: Default streaming feed granularity in seconds (ADR 0022). 160 ms is the chunk
#: size ticket 55 measured: ~41-45 ms per chunk on Vulkan, ~72-88 ms on CPU.
DEFAULT_CHUNK_SECONDS: float = 0.16

#: The default ``model``: ``None`` means "the managed pinned model". It is
#: deliberately not a path -- a config parse must not resolve (or probe) the data
#: directory (ADR 0009), and the adapter owns that resolution.
DEFAULT_MODEL: str | None = None

#: Keys this block understands. Everything else -- notably ``window_seconds`` --
#: is an unknown-key warning.
KNOWN_KEYS: frozenset[str] = frozenset({"device", "chunk_seconds", "model"})


class NemoSpeechConfigError(Exception):
    """The ``[engine.nemo_speech]`` block has a wrong-typed or invalid value.

    Every message names the offending key, the received value, and the allowed
    range/type, so ``ges2t doctor`` can print it verbatim. Unknown keys are *not*
    raised; they are returned as warnings by
    :meth:`NemoSpeechConfig.from_mapping` (ADR 0009).
    """


@dataclass(frozen=True)
class NemoSpeechConfig:
    """Validated ``[engine.nemo_speech]`` settings (ADR 0009, ADR 0022).

    ``model`` is ``None`` when the block omitted it; the adapter then loads the
    managed pinned model (:func:`ges2t.model_store.nemo_model_path`).
    """

    device: NemoDevicePreference = DEFAULT_DEVICE
    chunk_seconds: float = DEFAULT_CHUNK_SECONDS
    model: str | None = DEFAULT_MODEL

    @classmethod
    def from_mapping(cls, raw: Mapping[str, Any]) -> NemoSpeechConfigResult:
        """Validate a raw ``[engine.nemo_speech]`` mapping (ADR 0009).

        An empty mapping -- the block is absent, which the generic layer never
        synthesizes -- yields all defaults with no warnings. Unknown keys are
        collected into the result's warnings and ignored; ``window_seconds`` is
        deliberately among them (ADR 0022). A wrong-typed or invalid value raises
        :class:`NemoSpeechConfigError`.
        """
        warnings: list[str] = [
            _unknown_key_warning(key) for key in raw if key not in KNOWN_KEYS
        ]

        return NemoSpeechConfigResult(
            config=cls(
                device=_read_device(raw),
                chunk_seconds=_read_chunk_seconds(raw),
                model=_read_model(raw),
            ),
            warnings=warnings,
        )


@dataclass(frozen=True)
class NemoSpeechConfigResult:
    """The validated NeMo-Speech config plus non-fatal problems found validating it.

    ``warnings`` are unknown keys. They are surfaced through ``ges2t doctor`` and
    never prevent the configuration from being used.
    """

    config: NemoSpeechConfig
    warnings: list[str]


def _unknown_key_warning(key: str) -> str:
    """Build the warning for a key outside :data:`KNOWN_KEYS`.

    The single source of truth for this sentence: ``window_seconds`` gets exactly
    the same wording as any other unknown key (ADR 0022 requires the block to
    *say so*, not to invent a special message that could drift).
    """
    return f"{BLOCK_NAME}: unknown key {key!r}"


def _read_device(raw: Mapping[str, Any]) -> NemoDevicePreference:
    """Return ``device`` as a :class:`NemoDevicePreference`, defaulting to ``auto``."""
    key = "device"
    if key not in raw:
        return DEFAULT_DEVICE
    value = raw[key]
    if not isinstance(value, str):
        raise NemoSpeechConfigError(
            f"{BLOCK_NAME}.{key}: expected a string, got {_describe_value(value)}"
        )
    try:
        return NemoDevicePreference(value)
    except ValueError as exc:
        valid = ", ".join(member.value for member in NemoDevicePreference)
        raise NemoSpeechConfigError(
            f"{BLOCK_NAME}.{key} = {value!r} is invalid: valid values are {valid}"
        ) from exc


def _read_chunk_seconds(raw: Mapping[str, Any]) -> float:
    """Return ``chunk_seconds`` as a positive, finite ``float``.

    ``int`` and ``float`` are both accepted and normalised to ``float`` (TOML
    parses ``chunk_seconds = 1`` as ``int``); ``bool`` is excluded explicitly
    because in Python it is an ``int`` subclass. A non-positive or non-finite value
    is a hard error: the engine is fed at this granularity, and zero or a negative
    chunk cannot be fed at all.
    """
    key = "chunk_seconds"
    if key not in raw:
        return DEFAULT_CHUNK_SECONDS
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NemoSpeechConfigError(
            f"{BLOCK_NAME}.{key}: expected a number (int or float, not bool), "
            f"got {_describe_value(value)}"
        )
    number = float(value)
    if not math.isfinite(number):
        raise NemoSpeechConfigError(
            f"{BLOCK_NAME}.{key} = {number!r} is invalid: expected a finite number "
            "of seconds"
        )
    if number <= 0.0:
        raise NemoSpeechConfigError(
            f"{BLOCK_NAME}.{key} = {number!r} is invalid: chunk_seconds must be "
            "greater than 0"
        )
    return number


def _read_model(raw: Mapping[str, Any]) -> str | None:
    """Return the ``model`` GGUF path override, or ``None`` when omitted.

    The value must be a non-empty string ending in :data:`GGUF_SUFFIX`. The
    filesystem is deliberately **not** touched here (a config parse stays
    probe-free, ADR 0009); the adapter reports a missing file at load, naming the
    exact path.
    """
    key = "model"
    if key not in raw:
        return DEFAULT_MODEL
    value = raw[key]
    if not isinstance(value, str):
        raise NemoSpeechConfigError(
            f"{BLOCK_NAME}.{key}: expected a string path, got {_describe_value(value)}"
        )
    if not value.lower().endswith(GGUF_SUFFIX):
        raise NemoSpeechConfigError(
            f"{BLOCK_NAME}.{key} = {value!r} is invalid: expected a path to a "
            f"{GGUF_SUFFIX} model file (the managed model is used when the key is "
            "omitted)"
        )
    return value


def _describe_value(value: object) -> str:
    """Render a value for an error message as its type name, plus the value when scalar."""
    type_name = type(value).__name__
    if isinstance(value, (bool, int, float, str)):
        return f"{type_name} ({value!r})"
    return type_name
