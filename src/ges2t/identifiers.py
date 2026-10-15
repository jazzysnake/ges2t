"""Single source of truth for the identifier strings ges2t compares in code.

Rule 1 of ``docs/code-quality.md``: an identifier string -- an engine name, an
injection backend, a setup token, a model name, a hotkey mode, a config key that
is compared -- is **defined once** and compared through a named constant. No bare
string literal in an ``if``, ``match`` or comparison. Two modules spelling the
same identifier twice is how ``whisper.cpp`` and ``whisper_cpp`` shipped as one
thing with two names (ticket 47); this module is the answer (ticket 48).

Why a single leaf module rather than per-domain constants
---------------------------------------------------------
The defect this guards against is *cross-module drift*: a spelling that differs
between the module that defines a value and the module that compares it. A
per-domain split would re-open the very question -- "is the engine name owned by
the config layer or by the transcriber?" -- that produced the drift. One file
makes that question have exactly one answer. It is deliberately a **leaf**: this
module imports nothing from ``ges2t``, so every other module may depend on it
without an import cycle. Collections *derived* from these values
(``KNOWN_ENGINES``, ``ENGINE_ALIASES``, ``BATCH_ONLY_ENGINES``, the model
catalogues) stay in the module that owns the collection; only the raw values live
here.

The values are **not** renamed by this module: it is a de-duplication, not a
rename, so every string here is byte-identical to the literal it replaces.
"""

from __future__ import annotations

__all__ = [
    "CLIPBOARD_BACKEND",
    "CONFIG_KEY_BACKEND",
    "HOTKEY_MODE_PTT",
    "HOTKEY_MODE_TOGGLE",
    "INJECTION_DEFAULT_BACKEND",
    "NEEDLE_SETUP_BACKEND",
    "NEMO_SPEECH_ENGINE",
    "WHISPER_CPP_CLI_TOKEN",
    "WHISPER_CPP_ENGINE",
    "WHISPER_MODEL_MEDIUM_Q5_0",
    "WHISPER_MODEL_SMALL_Q8_0",
    "WHISTLE_ENGINE",
    "WHISTLE_MODEL",
    "YDOTOOL_BACKEND",
]

# --- Engines ------------------------------------------------------------------

#: The engine value of the shipped default: the Whistle model on the Needle 3
#: engine. First in :data:`ges2t.config.KNOWN_ENGINES`, so it stays the default
#: (ADR 0021 §1).
WHISTLE_ENGINE: str = "whistle"

#: The **canonical** engine value of the whisper.cpp backend (ADR 0021). A TOML
#: block name cannot contain the dot of the CLI token, so the underscore form is
#: canonical and ``whisper.cpp`` is accepted only as an alias (ticket 47).
WHISPER_CPP_ENGINE: str = "whisper_cpp"

#: The **CLI token** for the whisper.cpp backend: the spelling ``ges2t setup``
#: takes and the config layer forgives as an alias. One spelling, one definition
#: (ticket 47): the setup backend token and the config alias key are the same
#: string and must never diverge.
WHISPER_CPP_CLI_TOKEN: str = "whisper.cpp"

#: The engine value of the NeMo-Speech.cpp streaming backend (ADR 0022). Unlike
#: whisper.cpp there is **no** separate CLI token: the engine value contains no
#: dot, so ``ges2t setup``, ``transcriber.engine`` and the TOML block name all
#: spell it identically. Defining a second constant here would be a second
#: spelling of one identifier (rule 1), so the setup backend reuses this value.
NEMO_SPEECH_ENGINE: str = "nemo_speech"

# --- Injection backends -------------------------------------------------------

#: The uinput-injection backend (ADR 0003, ADR 0020).
YDOTOOL_BACKEND: str = "ydotool"

#: The clipboard-paste backend (ticket 25, ADR 0003 amended).
CLIPBOARD_BACKEND: str = "clipboard"

#: Default ``injection.backend`` (ADR 0003).
INJECTION_DEFAULT_BACKEND: str = YDOTOOL_BACKEND

# --- Setup backends -----------------------------------------------------------

#: The setup backend token that prepares the needle engine (ADR 0021 §6). The
#: whisper.cpp setup token is :data:`WHISPER_CPP_CLI_TOKEN`.
NEEDLE_SETUP_BACKEND: str = "needle"

# --- Model names --------------------------------------------------------------

#: The needle/Whistle model name. It coincides with :data:`WHISTLE_ENGINE` but is
#: a distinct identifier (a model, not an engine); the two are defined separately
#: so they can diverge without one silently changing the other.
WHISTLE_MODEL: str = "whistle"

#: The whisper.cpp model the ADR 0021 §4 rule selects when a Vulkan build is
#: possible: the most accurate model per second on Vulkan.
WHISPER_MODEL_MEDIUM_Q5_0: str = "medium-q5_0"

#: The whisper.cpp model the same rule selects on a CPU-only machine; upstream
#: publishes no ``small-q5_0`` and ``q8_0`` scored identically (ADR 0021 §4).
WHISPER_MODEL_SMALL_Q8_0: str = "small-q8_0"

# --- Hotkey modes -------------------------------------------------------------

#: ``hotkey.mode`` press-to-talk (the default, ADR 0009).
HOTKEY_MODE_PTT: str = "ptt"

#: ``hotkey.mode`` toggle (ADR 0009).
HOTKEY_MODE_TOGGLE: str = "toggle"

# --- Config keys compared in code ---------------------------------------------

#: The ``injection`` key that selects the backend. It is the one generic key the
#: loader compares by name (``injection_blocks`` skips it), so it is named here.
CONFIG_KEY_BACKEND: str = "backend"
