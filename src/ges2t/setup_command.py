"""``ges2t setup``: backend- and model-parameterised acquisition (ADR 0021 §6).

This module is the body of the ``setup`` subcommand; :mod:`ges2t.cli` is the thin
adapter that parses the arguments and prints the result. It exists separately so
that the parsing / default-rule / orchestration logic is testable without going
through :mod:`argparse`, and so that a config change elsewhere does not drag the
setup vocabulary into :mod:`ges2t.config`.

The surface it implements (ADR 0021 §6, ADR 0021 §10):

* ``ges2t setup`` — unchanged behaviour: set up the ``needle`` engine and its
  default model, ``whistle``.
* ``ges2t setup needle [model]`` — the model argument is honoured too: the needle
  model is **selectable**, defaulting to ``whistle``.
* ``ges2t setup whisper.cpp [model]`` — **build** the whisper.cpp engine and
  **acquire** the named model. With no model, the ADR 0021 §4 default rule picks
  ``medium-q5_0`` when a Vulkan device is available, ``small-q8_0`` otherwise.

Two properties the ticket makes load-bearing:

* **Validation precedes work.** :func:`plan_setup` resolves and validates the
  backend and the model name — raising a typed, actionable error — *before* any
  build or download starts. An unknown backend, an unknown model, or a quant
  upstream does not publish never reaches a ``cmake`` run or a fetch.
* **Partial failure is diagnosable, not corrupt.** The whisper path builds the
  engine first (so the prerequisite gate fires before the big download) and then
  acquires the model; if the model fails, the engine install is a complete,
  manifest-bearing directory (the atomic install in :mod:`ges2t.whisper_engine`),
  and :class:`PartialSetupError` names what is installed and what is missing.
  Every step is idempotent, so re-running resumes rather than restarts.

Nothing here installs a package or fetches build tooling: the prerequisite gate
is owned by :mod:`ges2t.whisper_engine` and its exact ``pacman`` line is never
duplicated or weakened here. Network I/O happens only inside the acquisition
operations, which ``ges2t setup`` is the only command to invoke (ADR 0018).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from ges2t import model_store
from ges2t.model_store import (
    DEFAULT_NEEDLE_MODEL,
    ModelStoreError,
    ProgressCallback,
)
from ges2t.whisper_engine import (
    DEFAULT_WHISPER_MODEL_CPU,
    DEFAULT_WHISPER_MODEL_GPU,
    EngineBuild,
    SystemProbe,
    default_whisper_model,
    ensure_engine,
)

__all__ = [
    "DEFAULT_SETUP_BACKEND",
    "DEFAULT_WHISPER_MODEL_CPU",
    "DEFAULT_WHISPER_MODEL_GPU",
    "NeedleEngineInstaller",
    "NeedleModelInstaller",
    "PartialSetupError",
    "ProgressFactory",
    "SetupBackend",
    "SetupDependencies",
    "SetupError",
    "SetupOutcome",
    "SetupPlan",
    "UnknownBackendError",
    "WhisperEngineInstaller",
    "WhisperModelInstaller",
    "default_dependencies",
    "default_whisper_model",
    "plan_setup",
    "run_setup",
]

# --- The ADR 0021 §4 default-model rule ---------------------------------------
#
# The rule itself lives in :mod:`ges2t.whisper_engine`, next to
# :func:`~ges2t.whisper_engine.inspect_vulkan`, so the model ``ges2t setup``
# fetches and the model the service loads from an omitted config are chosen by
# the **same** probe (ticket 45). ``DEFAULT_WHISPER_MODEL_GPU``,
# ``DEFAULT_WHISPER_MODEL_CPU`` and :func:`default_whisper_model` are re-exported
# here because they are part of this module's public API (ticket 44).


# --- Errors --------------------------------------------------------------------


class SetupError(Exception):
    """A user-facing ``ges2t setup`` failure, printed as a sentence (ADR 0016)."""


class UnknownBackendError(SetupError):
    """The backend argument is not one ``ges2t setup`` knows (ADR 0021 §6).

    Names the valid set, and is raised by :func:`plan_setup` **before** any build
    or download starts.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.valid = tuple(backend.value for backend in SetupBackend)
        super().__init__(
            f"unknown backend {name!r}; valid backends: {', '.join(self.valid)}"
        )


class PartialSetupError(SetupError):
    """A later acquisition step failed after an earlier one succeeded.

    The earlier artifact is installed and complete (the engine install and the
    model download are each atomic and idempotent), so the failure is
    **diagnosable, not corrupt**: the message names what is installed and what is
    missing, and every step can simply be re-run (ADR 0021 §6).
    """

    def __init__(self, installed: str, missing: str, cause: Exception) -> None:
        self.installed = installed
        self.missing = missing
        self.cause = cause
        super().__init__(
            f"{installed} is installed, but {missing} could not be acquired: {cause}\n"
            f"    the installed artifact is complete; re-run 'ges2t setup' to finish"
        )


# --- Vocabulary ----------------------------------------------------------------


class SetupBackend(StrEnum):
    """The engines ``ges2t setup`` can prepare (ADR 0021 §6)."""

    NEEDLE = "needle"
    WHISPER_CPP = "whisper.cpp"


#: The backend ``ges2t setup`` sets up when none is named: the shipped default.
DEFAULT_SETUP_BACKEND: SetupBackend = SetupBackend.NEEDLE


@dataclass(frozen=True)
class SetupPlan:
    """A validated setup request: which backend, and which model name.

    ``model`` is always a canonical, catalogue-validated name, so
    :func:`run_setup` never has to re-validate it and no acquisition starts on an
    unknown name.
    """

    backend: SetupBackend
    model: str


@dataclass(frozen=True)
class SetupOutcome:
    """What a successful :func:`run_setup` produced, for the CLI to print.

    Exactly one backend's fields are populated, keyed by :attr:`SetupPlan.backend`.
    """

    plan: SetupPlan
    needle_model_path: Path | None = None
    needle_engine_path: Path | None = None
    whisper_engine: EngineBuild | None = None
    whisper_model_path: Path | None = None


# --- Collaborators (ADR 0019: Protocols in parameters) -------------------------


@runtime_checkable
class NeedleModelInstaller(Protocol):
    """Ensures a needle/Whistle model is present; :func:`ges2t.model_store.ensure_model`."""

    def __call__(
        self, name: str, *, progress: ProgressCallback | None = None
    ) -> Path:
        """Acquire ``name`` and return its installed path."""
        ...


@runtime_checkable
class NeedleEngineInstaller(Protocol):
    """Ensures the Needle engine is present; :func:`ges2t.model_store.ensure_engine`."""

    def __call__(self, *, progress: ProgressCallback | None = None) -> Path:
        """Acquire the engine and return its installed path."""
        ...


@runtime_checkable
class WhisperEngineInstaller(Protocol):
    """Builds the whisper.cpp engine; :func:`ges2t.whisper_engine.ensure_engine`."""

    def __call__(self, *, progress: ProgressCallback | None = None) -> EngineBuild:
        """Build or verify the engine and return the installed build."""
        ...


@runtime_checkable
class WhisperModelInstaller(Protocol):
    """Ensures a whisper.cpp ggml model is present; :func:`ges2t.model_store.ensure_whisper_model`."""

    def __call__(
        self, name: str, *, progress: ProgressCallback | None = None
    ) -> Path:
        """Acquire ``name`` and return its installed path."""
        ...


@dataclass(frozen=True)
class SetupDependencies:
    """The four acquisition operations a setup run composes (ADR 0019).

    A class of Protocol-typed collaborators rather than module-level calls, so a
    test drives every path with fakes and :func:`run_setup` never touches the
    network, ``git`` or ``cmake``.
    """

    needle_model: NeedleModelInstaller
    needle_engine: NeedleEngineInstaller
    whisper_engine: WhisperEngineInstaller
    whisper_model: WhisperModelInstaller


def default_dependencies() -> SetupDependencies:
    """Return the real acquisition operations this module orchestrates."""
    return SetupDependencies(
        needle_model=model_store.ensure_model,
        needle_engine=model_store.ensure_engine,
        whisper_engine=ensure_engine,
        whisper_model=model_store.ensure_whisper_model,
    )


#: Maps a human label to the progress sink for that artifact, so the CLI can
#: print one labelled percentage run per artifact.
ProgressFactory = Callable[[str], ProgressCallback]


def _silent_progress(label: str) -> ProgressCallback:
    """Return a progress sink that discards its fraction (the default)."""
    del label

    def report(fraction: float) -> None:
        del fraction

    return report


# --- Planning ------------------------------------------------------------------


def _parse_backend(token: str | None) -> SetupBackend:
    """Resolve the backend argument, defaulting to needle; unknown names are typed.

    The membership check is against :class:`SetupBackend`, so adding a backend is
    a one-line change here and the error's valid set follows automatically.
    """
    if token is None:
        return DEFAULT_SETUP_BACKEND
    try:
        return SetupBackend(token)
    except ValueError as exc:
        raise UnknownBackendError(token) from exc


def plan_setup(
    backend: str | None,
    model: str | None,
    *,
    system: SystemProbe | None = None,
) -> SetupPlan:
    """Validate the ``setup`` arguments and resolve the model default.

    Every validation happens here, before :func:`run_setup` can build or
    download anything: an unknown backend raises :class:`UnknownBackendError`, an
    unknown needle model or an unpublished whisper quant raises the model store's
    typed :class:`~ges2t.model_store.ModelStoreError` subclasses.
    """
    selected = _parse_backend(backend)
    if selected is SetupBackend.NEEDLE:
        resolved = (
            model_store.needle_model(model).name
            if model is not None
            else DEFAULT_NEEDLE_MODEL
        )
    elif model is not None:
        resolved = model_store.parse_whisper_model_name(model).name
    else:
        resolved = default_whisper_model(system)
    return SetupPlan(backend=selected, model=resolved)


# --- Orchestration -------------------------------------------------------------


def run_setup(
    plan: SetupPlan,
    *,
    progress: ProgressFactory | None = None,
    dependencies: SetupDependencies | None = None,
) -> SetupOutcome:
    """Carry out a validated plan, leaving a consistent state on partial failure.

    The whisper order is **engine first, then model**: the prerequisite gate (and
    its exact ``pacman`` line) fires before ~0.5 GB is downloaded, and a model
    failure leaves a complete, diagnosable engine rather than a half-finished
    tree. The needle order is unchanged from the pre-ticket behaviour (model,
    then engine).
    """
    deps = dependencies if dependencies is not None else default_dependencies()
    factory = progress if progress is not None else _silent_progress
    if plan.backend is SetupBackend.WHISPER_CPP:
        return _run_whisper(plan, deps, factory)
    return _run_needle(plan, deps, factory)


def _run_needle(
    plan: SetupPlan,
    deps: SetupDependencies,
    factory: ProgressFactory,
) -> SetupOutcome:
    """Acquire the needle model and its engine, preserving the existing order."""
    model_path = deps.needle_model(
        plan.model, progress=factory(f"model {plan.model}")
    )
    engine_path = deps.needle_engine(progress=factory("needle engine"))
    return SetupOutcome(
        plan=plan,
        needle_model_path=model_path,
        needle_engine_path=engine_path,
    )


def _run_whisper(
    plan: SetupPlan,
    deps: SetupDependencies,
    factory: ProgressFactory,
) -> SetupOutcome:
    """Build the whisper.cpp engine, then acquire the model.

    An engine failure propagates unchanged, so the prerequisite gate's message
    (and its exact package line) is what the user sees. A **model** failure is
    wrapped in :class:`PartialSetupError` because the engine is already installed.
    """
    build = deps.whisper_engine(progress=factory("whisper.cpp engine"))
    try:
        model_path = deps.whisper_model(
            plan.model, progress=factory(f"model {plan.model}")
        )
    except (ModelStoreError, OSError) as exc:
        raise PartialSetupError(
            installed=f"the whisper.cpp engine ({build.summary})",
            missing=f"model {plan.model}",
            cause=exc,
        ) from exc
    return SetupOutcome(
        plan=plan,
        whisper_engine=build,
        whisper_model_path=model_path,
    )
