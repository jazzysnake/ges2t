"""whisper.cpp engine build: clone, configure, compile and install the libraries.

The in-process shape of the whisper backend (ADR 0021) needs **shared libraries**
— ``libwhisper.so`` and the ``libggml*.so`` family — not the ``whisper-cli``
binary. This module is the one place that produces them, and it mirrors the
:mod:`ges2t.model_store` layout and discipline:

* **Nothing is installed and nothing but the pinned clone touches the network.**
  The prerequisites (a C++ toolchain, ``cmake``, and — for the GPU build — the
  Vulkan dev packages) are **checked**, never installed: a missing one raises
  :class:`PrerequisiteError`, whose message names the missing pieces and the
  exact ``pacman`` line. No ``sudo``, no package manager, no ``uvx``, no fetching
  of build tooling (ADR 0018, ADR 0021 §7).
* **Network is used for exactly one thing**: ``git clone`` of whisper.cpp at the
  pinned tag, whose commit is then verified against the pin. A tag that moved
  fails loudly instead of silently building something else (ADR 0018).
* **Idempotent.** Re-running against a complete installed engine verifies it and
  does nothing else: no clone, no ``cmake`` invocation. Completeness means the
  manifest is present *and* every library it names exists.
* **The chosen feature set is recorded.** An ``engine.json`` manifest in the
  install directory names the revision, the tag, the variant (``cpu``/
  ``vulkan``) and the library files, so ``doctor`` and the user can see which
  build is installed without re-deriving it.

The build recipe is the provider research's own
(``docs/project/research/whisper-cpp-provider/REPORT.md`` §2.1/§8), with one
deliberate addition: ``-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON
-DCMAKE_INSTALL_RPATH=$ORIGIN`` bakes a ``$ORIGIN`` ``RUNPATH`` into the produced
libraries, so the installed set is self-contained and loads by absolute path with
``ctypes`` (the research only ran the CLI from its build tree, where an absolute
build-tree RUNPATH happened to work). See this ticket's ``001-comment.md`` for
the verified ``readelf``/``ctypes`` evidence.

The module imports only the standard library (plus
:func:`ges2t.model_store.default_data_dir`, so the ``ges2t`` data directory is
defined in exactly one place). It never imports a backend package, the config
layer, or the CLI.
"""

from __future__ import annotations

import ctypes.util
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Protocol, runtime_checkable

from ges2t.model_store import default_data_dir

__all__ = [
    "BUILD_TARGET",
    "CLONE_TIMEOUT_SECONDS",
    "CONFIGURE_TIMEOUT_SECONDS",
    "DEFAULT_WHISPER_MODEL_CPU",
    "DEFAULT_WHISPER_MODEL_GPU",
    "ENGINE_DIRECTORY_NAME",
    "MANIFEST_NAME",
    "PACMAN_PREFIX",
    "PACKAGE_CMAKE",
    "PACKAGE_SHADERC",
    "PACKAGE_SPIRV_HEADERS",
    "PACKAGE_TOOLCHAIN",
    "PACKAGE_VULKAN_HEADERS",
    "SHADER_COMPILER_NAME",
    "SPIRV_HEADER_PATH",
    "VULKAN_HEADER_PATH",
    "VULKAN_LIBRARY_NAME",
    "WHISPER_CPP_BUILD_TIMEOUT_SECONDS",
    "WHISPER_CPP_GGML_VERSION",
    "WHISPER_CPP_REVISION",
    "WHISPER_CPP_TAG",
    "WHISPER_CPP_URL",
    "WHISPER_CPP_VERSION",
    "BuildFailedError",
    "BuildRequest",
    "BuildVariant",
    "CommandRunner",
    "EngineBuild",
    "EngineBuilder",
    "EngineLibraries",
    "EngineManifestError",
    "HostSystemProbe",
    "Prerequisite",
    "PrerequisiteError",
    "PrerequisiteKind",
    "PrerequisiteReport",
    "ProgressCallback",
    "SubprocessRunner",
    "SystemProbe",
    "VulkanStatus",
    "WhisperCppBuilder",
    "WhisperEngineError",
    "check_prerequisites",
    "default_build_root",
    "default_jobs",
    "default_whisper_model",
    "engine_dir",
    "ensure_engine",
    "inspect_vulkan",
    "manifest_path",
    "prerequisites_for",
    "read_build",
    "verify_build",
    "vulkan_dev_package_hint",
]

# --- The pinned engine (ADR 0021) --------------------------------------------

#: Upstream repository. The only network access this module performs (ADR 0018).
WHISPER_CPP_URL: str = "https://github.com/ggml-org/whisper.cpp.git"

#: The release tag the provider research built and measured.
WHISPER_CPP_TAG: str = "v1.9.5"

#: The commit ``WHISPER_CPP_TAG`` resolved to when the research pinned it
#: (verified with ``git log -1 --format=%H`` in the reference tree). The builder
#: checks the freshly cloned ``HEAD`` against this, so a moved tag cannot pass
#: silently.
WHISPER_CPP_REVISION: str = "d1be6fde11ac6e0407606b4e42fe72d34add8037"

#: Version directory under ``engine/whisper-cpp/``; the tag, so the installation
#: path names exactly what is installed.
WHISPER_CPP_VERSION: str = "v1.9.5"

#: The ggml version the pinned revision brings in (from the produced
#: ``libggml.so.0.26.0``); recorded for humans, never compared.
WHISPER_CPP_GGML_VERSION: str = "0.26.0"

#: Directory name under the ges2t data dir's ``engine/`` directory.
ENGINE_DIRECTORY_NAME: str = "whisper-cpp"

#: The installed manifest, naming revision, variant and library files.
MANIFEST_NAME: str = "engine.json"

#: The CMake target that produces ``libwhisper.so`` and, transitively, the
#: ``libggml*.so`` family. The CLI (``whisper-cli``) is deliberately **not**
#: built: the integration is in-process ``ctypes`` (ADR 0021).
BUILD_TARGET: str = "whisper"

#: Files installed from the CMake build's ``bin/`` directory. Symlinks are
#: preserved, so the SONAME chain (``libwhisper.so`` -> ``libwhisper.so.1`` ->
#: ``libwhisper.so.1.9.5``) survives intact.
LIBRARY_GLOBS: tuple[str, ...] = ("libwhisper.so*", "libggml*.so*")

#: The library the adapter loads; every other installed library is a dependency.
ENTRY_LIBRARY_NAME: str = "libwhisper.so"

# --- The ADR 0021 §4 default-model rule ---------------------------------------
#
# The rule lives here, next to :func:`inspect_vulkan`, because the default model
# and the default build variant are decided by the **same** probe: the model must
# match the engine that would actually be built, and choosing it anywhere else
# would duplicate the probe (ticket 45). ``ges2t setup`` (which fetches the
# model) and the service startup (which loads it) both call
# :func:`default_whisper_model`, so a machine can never be set up with one model
# and run with another.

#: The whisper.cpp model the rule selects when a Vulkan build is possible (ADR
#: 0021 §4): the research's most accurate model and the best accuracy-per-second
#: on Vulkan.
DEFAULT_WHISPER_MODEL_GPU: str = "medium-q5_0"

#: The whisper.cpp model the rule selects on a CPU-only machine. Upstream
#: publishes **no** ``small-q5_0`` (that only existed via a local quantiser) and
#: ``q8_0`` scored identically, so the CPU default is ``small-q8_0`` (ADR 0021 §4).
DEFAULT_WHISPER_MODEL_CPU: str = "small-q8_0"

# --- Prerequisites (ADR 0021 §7) ---------------------------------------------

#: The one command prefix used in every prerequisite message. ges2t never runs
#: it: the user does.
PACMAN_PREFIX: str = "sudo pacman -S"

#: C/C++ toolchain and ``make``.
PACKAGE_TOOLCHAIN: str = "base-devel"

#: The build system generator.
PACKAGE_CMAKE: str = "cmake"

#: Vulkan headers (``vulkan/vulkan.h``), needed for a GPU build.
PACKAGE_VULKAN_HEADERS: str = "vulkan-headers"

#: SPIR-V headers, required by ``find_package(SPIRV-Headers)`` in ggml-vulkan.
PACKAGE_SPIRV_HEADERS: str = "spirv-headers"

#: ``glslc``, the shader compiler ggml-vulkan invokes to build its shaders.
PACKAGE_SHADERC: str = "shaderc"

#: Vulkan loader library, probed to decide CPU-vs-Vulkan. Its **absence selects
#: the CPU-only build** rather than failing, which is why it is not in the
#: installable package list below (ADR 0021 §7: "No Vulkan runtime -> CPU-only").
VULKAN_LIBRARY_NAME: str = "vulkan"

#: Vulkan's entry header, from ``vulkan-headers``.
VULKAN_HEADER_PATH: str = "vulkan/vulkan.h"

#: SPIR-V header, from ``spirv-headers``.
SPIRV_HEADER_PATH: str = "spirv/unified1/spirv.h"

#: Shader compiler executable, from ``shaderc``.
SHADER_COMPILER_NAME: str = "glslc"

# --- Bounded external commands ------------------------------------------------

#: The shallow clone of one tag is small and fast; the bound exists so a wedged
#: network cannot hang ``ges2t setup`` forever.
CLONE_TIMEOUT_SECONDS: float = 600.0

#: CMake's configure step is seconds.
CONFIGURE_TIMEOUT_SECONDS: float = 600.0

#: Compiling ggml (and the Vulkan shaders) is the long step. Generous but still
#: bounded: a build that outlives this is wedged, not slow.
WHISPER_CPP_BUILD_TIMEOUT_SECONDS: float = 3600.0

#: Directory roots searched for headers and libraries. ``CPATH``/``LIBRARY_PATH``
#: are prepended when set, so a staged SDK is honoured without any flag.
DEFAULT_INCLUDE_DIRECTORIES: tuple[Path, ...] = (
    Path("/usr/include"),
    Path("/usr/local/include"),
)
DEFAULT_LIBRARY_DIRECTORIES: tuple[Path, ...] = (
    Path("/usr/lib"),
    Path("/usr/lib64"),
    Path("/usr/local/lib"),
)

#: Callable shape shared with :mod:`ges2t.model_store` and
#: :class:`ges2t.interfaces.ProgressCallback`: the completed fraction in
#: ``[0.0, 1.0]``.
ProgressCallback = Callable[[float], None]


# --- Errors -------------------------------------------------------------------


class WhisperEngineError(Exception):
    """Base class for every failure raised by this module."""


class PrerequisiteError(WhisperEngineError):
    """A build prerequisite is missing. Never installs anything (ADR 0021 §7).

    The message names each missing prerequisite *and its package*, then prints
    the exact ``sudo pacman -S ...`` line for the variant that was being built,
    so the user can copy it verbatim.
    """

    def __init__(self, report: PrerequisiteReport) -> None:
        self.report = report
        missing = "; ".join(
            f"{item.name} ({item.package})" for item in report.missing
        )
        super().__init__(
            f"cannot build the whisper.cpp engine ({report.variant}): missing "
            f"{missing}.\nInstall the prerequisites, then re-run this command:\n"
            f"    {report.pacman_command}"
        )


class EngineManifestError(WhisperEngineError):
    """The installed engine manifest exists but cannot be read back."""


class BuildFailedError(WhisperEngineError):
    """The clone, configure, compile or install step failed or produced nothing."""


# --- Records ------------------------------------------------------------------


class BuildVariant(StrEnum):
    """Which backends the installed engine can use (ADR 0021 §2)."""

    CPU = "cpu"
    VULKAN = "vulkan"

    @property
    def label(self) -> str:
        """The user-facing name of the variant."""
        return "Vulkan (GPU)" if self is BuildVariant.VULKAN else "CPU-only"


class PrerequisiteKind(StrEnum):
    """How a prerequisite is located."""

    EXECUTABLE = "executable"
    HEADER = "header"
    LIBRARY = "library"


@dataclass(frozen=True)
class Prerequisite:
    """One build prerequisite: what it is, how to find it, which package ships it."""

    name: str
    kind: PrerequisiteKind
    probe: str
    package: str


@dataclass(frozen=True)
class PrerequisiteReport:
    """The outcome of checking every prerequisite for one variant.

    ``required`` is the full set for the variant (so ``pacman_command`` is the
    canonical, copy-pasteable line); ``missing`` is the subset that failed.
    """

    variant: BuildVariant
    required: tuple[Prerequisite, ...]
    missing: tuple[Prerequisite, ...]

    @property
    def satisfied(self) -> bool:
        """Whether every required prerequisite was found."""
        return not self.missing

    @property
    def packages(self) -> tuple[str, ...]:
        """The unique packages of ``required``, in declaration order."""
        unique: list[str] = []
        for item in self.required:
            if item.package not in unique:
                unique.append(item.package)
        return tuple(unique)

    @property
    def pacman_command(self) -> str:
        """The exact command that installs this variant's prerequisites."""
        return f"{PACMAN_PREFIX} {' '.join(self.packages)}"


@dataclass(frozen=True)
class VulkanStatus:
    """What was detected for a Vulkan build; ``None`` means "not found".

    A Vulkan build is chosen **only** when every field is set. Anything missing
    selects the CPU-only build with no error (ADR 0021 §7).
    """

    runtime: Path | None
    headers: Path | None
    spirv_headers: Path | None
    shader_compiler: Path | None

    @property
    def available(self) -> bool:
        """Whether the Vulkan runtime, dev headers and shader compiler are all present."""
        return None not in (
            self.runtime,
            self.headers,
            self.spirv_headers,
            self.shader_compiler,
        )

    @property
    def missing(self) -> tuple[str, ...]:
        """Human-readable names of the pieces that were not found."""
        names: list[str] = []
        if self.runtime is None:
            names.append("Vulkan runtime (vulkan-icd-loader)")
        if self.headers is None:
            names.append("Vulkan headers (vulkan-headers)")
        if self.spirv_headers is None:
            names.append("SPIR-V headers (spirv-headers)")
        if self.shader_compiler is None:
            names.append("shader compiler (shaderc)")
        return tuple(names)

    @property
    def missing_packages(self) -> tuple[str, ...]:
        """The Arch packages for the **dev** pieces that were not found.

        The Vulkan runtime is deliberately excluded: its absence selects the
        CPU-only build (ADR 0021 §7) and no installable dev package is implied.
        This is the list :func:`vulkan_dev_package_hint` names, so the GPU-path
        hint is a package list, not a prose list.
        """
        packages: list[str] = []
        if self.headers is None:
            packages.append(PACKAGE_VULKAN_HEADERS)
        if self.spirv_headers is None:
            packages.append(PACKAGE_SPIRV_HEADERS)
        if self.shader_compiler is None:
            packages.append(PACKAGE_SHADERC)
        return tuple(packages)

    def summary(self) -> str:
        """One line naming the detected device libraries or what is missing."""
        if self.available:
            return "Vulkan runtime, headers, SPIR-V headers and glslc present"
        return "CPU-only: " + ", ".join(self.missing)


@dataclass(frozen=True)
class EngineLibraries:
    """The installed shared libraries: the entry point plus its ggml dependencies.

    ``libwhisper`` is what the adapter loads by absolute path; ``ggml`` holds
    every ``libggml*.so*`` file (real files and their SONAME symlinks), which
    ``libwhisper.so`` resolves through its ``$ORIGIN`` RUNPATH.
    """

    libwhisper: Path
    ggml: tuple[Path, ...]

    @property
    def all(self) -> tuple[Path, ...]:
        """Every installed library file, the entry point first."""
        return (self.libwhisper, *self.ggml)

    def missing(self) -> tuple[Path, ...]:
        """The installed libraries that are not present on disk."""
        return tuple(path for path in self.all if not path.exists())

    def complete(self) -> bool:
        """Whether every installed library is present."""
        return not self.missing()


@dataclass(frozen=True)
class EngineBuild:
    """One installed engine: what it is, which variant, and where it lives."""

    version: str
    tag: str
    revision: str
    variant: BuildVariant
    directory: Path
    libraries: EngineLibraries
    vulkan: VulkanStatus
    built_at: str

    @property
    def backend(self) -> str:
        """``"Vulkan"``/``"CPU"``, the device backend the build can use."""
        return "Vulkan" if self.variant is BuildVariant.VULKAN else "CPU"

    @property
    def summary(self) -> str:
        """One line fit for ``doctor`` and setup output."""
        return (
            f"whisper.cpp {self.tag} ({self.variant.label}) at {self.directory}"
        )


@dataclass(frozen=True)
class BuildRequest:
    """Everything a builder needs to produce one engine.

    The builder receives the install ``directory`` rather than deriving it, so a
    test can build into a temporary tree and the orchestration keeps ownership of
    the installation location.
    """

    version: str
    tag: str
    revision: str
    url: str
    variant: BuildVariant
    directory: Path
    jobs: int


# --- Interfaces (ADR 0019) ----------------------------------------------------


@runtime_checkable
class CommandRunner(Protocol):
    """Runs one external command to completion, or raises.

    The only implementation is :class:`SubprocessRunner`; tests substitute a
    recording fake so the suite never invokes ``git`` or ``cmake``.
    """

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None,
        timeout_seconds: float,
    ) -> str:
        """Run ``argv`` and return its standard output; raise on a non-zero exit."""
        ...


@runtime_checkable
class SystemProbe(Protocol):
    """Locates executables, headers and libraries on this machine.

    Read-only by construction: this is what makes the prerequisite check safe to
    run (it never installs, never shells out to a package manager).
    """

    def executable(self, name: str) -> Path | None:
        """Return the path of ``name`` on ``PATH``, or ``None``."""
        ...

    def header(self, relative_path: str) -> Path | None:
        """Return the path of the header at ``relative_path``, or ``None``."""
        ...

    def library(self, name: str) -> Path | None:
        """Return the path of ``lib<name>.so*``, or ``None``."""
        ...


@runtime_checkable
class EngineBuilder(Protocol):
    """Produces one installed engine build.

    :func:`ensure_engine` depends on this Protocol, not on
    :class:`WhisperCppBuilder`, so the engine can be built by a test fake.
    """

    def build(
        self,
        request: BuildRequest,
        progress: ProgressCallback | None,
    ) -> EngineBuild:
        """Clone, configure, compile and install, returning the installed build."""
        ...


# --- Locations ----------------------------------------------------------------


def engine_dir(version: str = WHISPER_CPP_VERSION) -> Path:
    """Return ``<data_dir>/engine/whisper-cpp/<version>/``.

    Mirrors Needle's ``engine/<version>/libneedle.so`` layout (the engine the
    whisper build replaces) with a product directory, because whisper.cpp
    installs a *set* of libraries rather than one file.
    """
    return default_data_dir() / "engine" / ENGINE_DIRECTORY_NAME / version


def manifest_path(directory: Path) -> Path:
    """Return the manifest path inside an installed engine directory."""
    return directory / MANIFEST_NAME


def default_build_root(version: str = WHISPER_CPP_VERSION) -> Path:
    """Return the scratch root the clone and CMake build tree live under.

    ``$XDG_CACHE_HOME/ges2t/whisper-cpp-build/<version>`` (default
    ``~/.cache/ges2t/...``). A cache, not the data directory: the sources and the
    build tree are throwaway, and a failed build must not be mistaken for an
    installed engine. The builder removes its own working directory on every
    path.
    """
    raw_cache = os.environ.get("XDG_CACHE_HOME")
    base = Path(raw_cache) if raw_cache else Path.home() / ".cache"
    return base / "ges2t" / "whisper-cpp-build" / version


def default_jobs() -> int:
    """Return the CPU-parallel build job count (every available core)."""
    return os.cpu_count() or 1


# --- Host probing --------------------------------------------------------------


def _environment_directories(variable: str) -> tuple[Path, ...]:
    """Return the non-empty ``os.pathsep``-separated entries of ``variable``."""
    raw = os.environ.get(variable, "")
    return tuple(Path(entry) for entry in raw.split(os.pathsep) if entry)


class HostSystemProbe:
    """The real :class:`SystemProbe`: ``PATH``, ``CPATH`` and the system roots."""

    def __init__(
        self,
        *,
        include_directories: tuple[Path, ...] | None = None,
        library_directories: tuple[Path, ...] | None = None,
        path: str | None = None,
    ) -> None:
        self._include_directories = include_directories or (
            *_environment_directories("CPATH"),
            *DEFAULT_INCLUDE_DIRECTORIES,
        )
        self._library_directories = library_directories or (
            *_environment_directories("LIBRARY_PATH"),
            *DEFAULT_LIBRARY_DIRECTORIES,
        )
        self._path = path

    def executable(self, name: str) -> Path | None:
        """Return the path of ``name`` on ``PATH``, or ``None``."""
        found = shutil.which(name, path=self._path)
        return Path(found) if found else None

    def header(self, relative_path: str) -> Path | None:
        """Return the first matching header under the include roots, or ``None``."""
        for base in self._include_directories:
            candidate = base / relative_path
            if candidate.is_file():
                return candidate
        return None

    def library(self, name: str) -> Path | None:
        """Return the first ``lib<name>.so*`` under the library roots, or ``None``.

        Directory search first, so a precise absolute path is reported;
        :func:`ctypes.util.find_library` is the fallback for a loader outside the
        standard roots (a custom ``ld.so.conf`` directory, for example).
        """
        for base in self._library_directories:
            matches = sorted(base.glob(f"lib{name}.so*"))
            if matches:
                return matches[0]
        resolved = ctypes.util.find_library(name)
        if resolved:
            candidate = Path(resolved)
            if candidate.is_absolute() and candidate.exists():
                return candidate
            for base in self._library_directories:
                beside = base / resolved
                if beside.exists():
                    return beside
            found = shutil.which(resolved, path=self._path)
            if found:
                return Path(found)
        return None


class SubprocessRunner:
    """A :class:`CommandRunner` over :mod:`subprocess`, always bounded and reaped.

    ``check=True`` makes a non-zero exit an exception, and ``timeout`` guarantees
    no command can outlive its budget (a wedged clone cannot hang
    ``ges2t setup``). Output is captured, so a build log never lands on the
    user's terminal unannounced.

    Exactly the failures a spawned command can produce are turned into a
    :class:`BuildFailedError` naming the command and (where there is one) the tail
    of its stderr: a non-zero exit, a timeout, and an :class:`OSError` from the
    spawn itself (a missing **or non-executable** ``argv[0]``). A non-positive
    ``timeout_seconds`` is rejected outright as a caller bug, so "bounded" is a
    property of this class rather than a promise about the constant passed in.
    """

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        timeout_seconds: float = WHISPER_CPP_BUILD_TIMEOUT_SECONDS,
    ) -> str:
        """Run ``argv`` (no shell), returning stdout; raise on failure or timeout."""
        if timeout_seconds <= 0:
            raise ValueError(
                f"a command must have a positive timeout, not {timeout_seconds}"
            )
        command = " ".join(argv)
        try:
            completed = subprocess.run(
                list(argv),
                cwd=cwd,
                check=True,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.CalledProcessError as exc:
            detail = _tail(exc.stderr or "")
            message = f"command failed (exit {exc.returncode}): {command}"
            if detail:
                message = f"{message}\n{detail}"
            raise BuildFailedError(message) from exc
        except subprocess.TimeoutExpired as exc:
            raise BuildFailedError(
                f"command timed out after {timeout_seconds:.0f}s: {command}"
            ) from exc
        except FileNotFoundError as exc:
            raise BuildFailedError(f"command not found: {argv[0]}") from exc
        except OSError as exc:
            raise BuildFailedError(f"cannot run {argv[0]}: {exc}") from exc
        return completed.stdout


def _tail(text: str, *, lines: int = 10) -> str:
    """Return the last ``lines`` non-empty lines of ``text``, indented."""
    interesting = [line for line in text.splitlines() if line.strip()]
    return "\n".join(f"    {line}" for line in interesting[-lines:])


# --- Prerequisites -------------------------------------------------------------


def prerequisites_for(variant: BuildVariant) -> tuple[Prerequisite, ...]:
    """Return the full prerequisite set for ``variant`` (ADR 0021 §7).

    The CPU set is the exact ``sudo pacman -S base-devel cmake`` line; the Vulkan
    set adds the three dev packages the research needed, giving the epic's line
    ``sudo pacman -S base-devel cmake vulkan-headers spirv-headers shaderc``.
    The Vulkan *loader* is deliberately absent: a missing runtime selects the
    CPU-only build instead of failing (see :data:`VULKAN_LIBRARY_NAME`).
    """
    common: tuple[Prerequisite, ...] = (
        Prerequisite(
            name="C++ compiler",
            kind=PrerequisiteKind.EXECUTABLE,
            probe="c++",
            package=PACKAGE_TOOLCHAIN,
        ),
        Prerequisite(
            name="C compiler",
            kind=PrerequisiteKind.EXECUTABLE,
            probe="cc",
            package=PACKAGE_TOOLCHAIN,
        ),
        Prerequisite(
            name="make",
            kind=PrerequisiteKind.EXECUTABLE,
            probe="make",
            package=PACKAGE_TOOLCHAIN,
        ),
        Prerequisite(
            name="cmake",
            kind=PrerequisiteKind.EXECUTABLE,
            probe="cmake",
            package=PACKAGE_CMAKE,
        ),
    )
    if variant is BuildVariant.CPU:
        return common
    return (
        *common,
        Prerequisite(
            name="Vulkan headers",
            kind=PrerequisiteKind.HEADER,
            probe=VULKAN_HEADER_PATH,
            package=PACKAGE_VULKAN_HEADERS,
        ),
        Prerequisite(
            name="SPIR-V headers",
            kind=PrerequisiteKind.HEADER,
            probe=SPIRV_HEADER_PATH,
            package=PACKAGE_SPIRV_HEADERS,
        ),
        Prerequisite(
            name="GLSL shader compiler",
            kind=PrerequisiteKind.EXECUTABLE,
            probe=SHADER_COMPILER_NAME,
            package=PACKAGE_SHADERC,
        ),
    )


def _locate(item: Prerequisite, system: SystemProbe) -> Path | None:
    """Return where ``item`` was found, or ``None``."""
    if item.kind is PrerequisiteKind.EXECUTABLE:
        return system.executable(item.probe)
    if item.kind is PrerequisiteKind.HEADER:
        return system.header(item.probe)
    return system.library(item.probe)


def check_prerequisites(
    variant: BuildVariant,
    system: SystemProbe,
) -> PrerequisiteReport:
    """Inspect every prerequisite for ``variant`` without installing anything."""
    required = prerequisites_for(variant)
    missing = tuple(item for item in required if _locate(item, system) is None)
    return PrerequisiteReport(variant=variant, required=required, missing=missing)


def inspect_vulkan(system: SystemProbe) -> VulkanStatus:
    """Detect the Vulkan runtime, dev headers and shader compiler (ADR 0021 §7)."""
    return VulkanStatus(
        runtime=system.library(VULKAN_LIBRARY_NAME),
        headers=system.header(VULKAN_HEADER_PATH),
        spirv_headers=system.header(SPIRV_HEADER_PATH),
        shader_compiler=system.executable(SHADER_COMPILER_NAME),
    )


def default_whisper_model(system: SystemProbe | None = None) -> str:
    """Return the ADR 0021 §4 default whisper.cpp model for this machine.

    ``medium-q5_0`` when a Vulkan build is possible, ``small-q8_0`` otherwise.
    The probe is the **same** :func:`inspect_vulkan` the engine build uses to
    choose CPU-vs-Vulkan, so the default model always matches the engine that
    would actually be built: a machine with a Vulkan runtime but no dev
    headers/``glslc`` gets a CPU build *and* the CPU model.

    This is the **single source of truth** for the rule. ``ges2t setup`` calls it
    to decide which model to fetch (ADR 0021 §6) and the service startup calls it
    to resolve an omitted ``[engine.whisper_cpp] model`` (ADR 0021 §8), so the
    two cannot drift apart.

    ``system`` is the test seam; the default is the real host probe.
    """
    probe = system if system is not None else HostSystemProbe()
    if inspect_vulkan(probe).available:
        return DEFAULT_WHISPER_MODEL_GPU
    return DEFAULT_WHISPER_MODEL_CPU


def vulkan_dev_package_hint(status: VulkanStatus) -> str | None:
    """Explain a CPU-only build when a GPU runtime is present but dev pieces are missing.

    This is the one wording for the fact both ``ges2t setup`` (ticket 47) and
    ``ges2t doctor`` (ticket 46) surface: a machine with a Vulkan **runtime** but
    a missing **dev** package (``spirv-headers`` and friends) silently builds
    CPU-only, and one ``pacman`` line would instead enable the GPU path. Keeping
    it here, next to :func:`inspect_vulkan`, means the two commands cannot drift.

    Returns ``None`` in the two cases where there is nothing to say: no Vulkan
    runtime at all (a GPU is not simply one package away) and every dev piece
    already present. The returned sentence already names the package(s) and the
    exact ``pacman`` line, so a caller can print it verbatim as a hint or a fix.
    """
    if status.runtime is None or status.available:
        return None
    packages = status.missing_packages
    return (
        "a Vulkan runtime is present, but the GPU build is missing "
        f"{', '.join(packages)}, so the engine builds CPU-only; install them with "
        f"'{PACMAN_PREFIX} {' '.join(packages)}' and rebuild with "
        "'ges2t setup whisper.cpp <model>' to enable the GPU path"
    )


# --- Manifest ------------------------------------------------------------------


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string (second resolution)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _optional_path_text(path: Path | None) -> str | None:
    """Return ``str(path)`` or ``None``."""
    return None if path is None else str(path)


def _build_mapping(build: EngineBuild) -> dict[str, object]:
    """Convert ``build`` to the JSON-ready manifest mapping (relative names)."""
    return {
        "version": build.version,
        "tag": build.tag,
        "revision": build.revision,
        "variant": str(build.variant),
        "entry_library": build.libraries.libwhisper.name,
        "libraries": [path.name for path in build.libraries.ggml],
        "built_at": build.built_at,
        "vulkan": {
            "runtime": _optional_path_text(build.vulkan.runtime),
            "headers": _optional_path_text(build.vulkan.headers),
            "spirv_headers": _optional_path_text(build.vulkan.spirv_headers),
            "shader_compiler": _optional_path_text(build.vulkan.shader_compiler),
        },
    }


def _required_text(source: Mapping[str, object], key: str) -> str:
    """Return ``source[key]`` as a ``str``, or raise :class:`EngineManifestError`."""
    value = source.get(key)
    if not isinstance(value, str):
        raise EngineManifestError(f"engine manifest: {key!r} must be a string")
    return value


def _optional_path(source: Mapping[str, object], key: str) -> Path | None:
    """Return ``source[key]`` as a :class:`~pathlib.Path`, ``None``, or raise."""
    value = source.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise EngineManifestError(f"engine manifest: {key!r} must be a string")
    return Path(value)


def _text_tuple(source: Mapping[str, object], key: str) -> tuple[str, ...]:
    """Return ``source[key]`` as a tuple of strings, or raise."""
    value = source.get(key)
    if not isinstance(value, list) or not all(
        isinstance(entry, str) for entry in value
    ):
        raise EngineManifestError(
            f"engine manifest: {key!r} must be a list of strings"
        )
    return tuple(entry for entry in value if isinstance(entry, str))


def _build_from_mapping(directory: Path, source: Mapping[str, object]) -> EngineBuild:
    """Rebuild an :class:`EngineBuild` from a manifest mapping."""
    raw_vulkan = source.get("vulkan")
    if not isinstance(raw_vulkan, Mapping):
        raise EngineManifestError("engine manifest: 'vulkan' must be an object")
    variant_text = _required_text(source, "variant")
    try:
        variant = BuildVariant(variant_text)
    except ValueError as exc:
        raise EngineManifestError(
            f"engine manifest: unknown variant {variant_text!r}"
        ) from exc
    ggml_names = _text_tuple(source, "libraries")
    return EngineBuild(
        version=_required_text(source, "version"),
        tag=_required_text(source, "tag"),
        revision=_required_text(source, "revision"),
        variant=variant,
        directory=directory,
        libraries=EngineLibraries(
            libwhisper=directory / _required_text(source, "entry_library"),
            ggml=tuple(directory / name for name in ggml_names),
        ),
        vulkan=VulkanStatus(
            runtime=_optional_path(raw_vulkan, "runtime"),
            headers=_optional_path(raw_vulkan, "headers"),
            spirv_headers=_optional_path(raw_vulkan, "spirv_headers"),
            shader_compiler=_optional_path(raw_vulkan, "shader_compiler"),
        ),
        built_at=_required_text(source, "built_at"),
    )


def _write_manifest(build: EngineBuild) -> Path:
    """Write ``build``'s manifest atomically and return its path.

    Temp file plus ``os.replace`` in the same directory: a reader never sees a
    half-written manifest, mirroring :mod:`ges2t.model_store`'s install.
    """
    destination = manifest_path(build.directory)
    destination.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(_build_mapping(build), indent=2, sort_keys=True) + "\n"
    handle, name = tempfile.mkstemp(
        prefix=f".{MANIFEST_NAME}.",
        suffix=".part",
        dir=destination.parent,
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, destination)
    except BaseException:
        _remove(temporary)
        raise
    return destination


def read_build(directory: Path) -> EngineBuild | None:
    """Read the installed manifest; ``None`` when there is none.

    Raises :class:`EngineManifestError` when the file exists but is not a valid
    manifest, so ``doctor`` can report genuine corruption instead of pretending
    the engine is simply absent.
    """
    path = manifest_path(directory)
    if not path.is_file():
        return None
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineManifestError(
            f"{path}: cannot read the engine manifest: {exc}"
        ) from exc
    if not isinstance(raw, Mapping):
        raise EngineManifestError(f"{path}: the engine manifest is not an object")
    return _build_from_mapping(directory, raw)


def verify_build(directory: Path) -> EngineBuild | None:
    """Return the installed build when it is complete, else ``None``.

    "Complete" means a readable manifest that names at least one ``ggml``
    library and whose entry library and every ``ggml`` library still exist.
    Anything else — absent directory, absent manifest, corrupt manifest, a
    library deleted by hand — is ``None``, which makes :func:`ensure_engine`
    rebuild rather than trust a broken tree.
    """
    try:
        build = read_build(directory)
    except EngineManifestError:
        return None
    if build is None or not build.libraries.ggml or not build.libraries.complete():
        return None
    return build


# --- The real builder ----------------------------------------------------------


class WhisperCppBuilder:
    """Clones the pinned revision, builds the library target, installs the libraries.

    The scratch tree (clone + CMake build) lives under
    :func:`default_build_root` and is removed on **every** exit path; only the
    final library set and the manifest land in the install directory, atomically
    enough that a failed build leaves either the previous engine or nothing.
    """

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        system: SystemProbe | None = None,
        build_root: Path | None = None,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._runner = runner if runner is not None else SubprocessRunner()
        self._system = system if system is not None else HostSystemProbe()
        self._build_root = build_root
        self._clock = clock if clock is not None else _utc_now

    def build(
        self,
        request: BuildRequest,
        progress: ProgressCallback | None = None,
    ) -> EngineBuild:
        """Clone, configure, compile and install; return the installed build."""
        cmake = self._system.executable("cmake")
        if cmake is None:
            raise PrerequisiteError(
                check_prerequisites(request.variant, self._system)
            )

        request.directory.mkdir(parents=True, exist_ok=True)
        root = self._build_root or default_build_root(request.version)
        root.mkdir(parents=True, exist_ok=True)
        _report(progress, 0.0)

        with tempfile.TemporaryDirectory(prefix="build-", dir=root) as scratch_text:
            scratch = Path(scratch_text)
            source = scratch / "whisper.cpp"
            build_tree = scratch / "build"

            self._clone(request, source)
            _report(progress, 0.2)

            self._configure(cmake, request, source, build_tree)
            _report(progress, 0.3)

            self._runner.run(
                [
                    str(cmake),
                    "--build",
                    str(build_tree),
                    "--target",
                    BUILD_TARGET,
                    "-j",
                    str(request.jobs),
                ],
                cwd=None,
                timeout_seconds=WHISPER_CPP_BUILD_TIMEOUT_SECONDS,
            )
            _report(progress, 0.9)

            libraries = _install_libraries(
                build_tree / "bin", request.directory, request.variant
            )

        build = EngineBuild(
            version=request.version,
            tag=request.tag,
            revision=request.revision,
            variant=request.variant,
            directory=request.directory,
            libraries=libraries,
            vulkan=inspect_vulkan(self._system),
            built_at=self._clock(),
        )
        _write_manifest(build)
        _report(progress, 1.0)
        return build

    def _clone(self, request: BuildRequest, source: Path) -> None:
        """Shallow-clone the pinned tag and verify the commit against the pin.

        ``git`` is guarded here rather than added to the prerequisite report: the
        ticket fixes that report's package list to the toolchain, ``cmake`` and
        the Vulkan dev packages, and ``base-devel`` does not ship ``git``. A
        missing ``git`` is therefore still an actionable typed error, just a
        separate one.
        """
        if self._system.executable("git") is None:
            raise BuildFailedError(
                "git is required to clone whisper.cpp; install it with:\n"
                "    sudo pacman -S git"
            )
        self._runner.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--branch",
                request.tag,
                request.url,
                str(source),
            ],
            cwd=None,
            timeout_seconds=CLONE_TIMEOUT_SECONDS,
        )
        head = self._runner.run(
            ["git", "-C", str(source), "rev-parse", "HEAD"],
            cwd=None,
            timeout_seconds=CLONE_TIMEOUT_SECONDS,
        ).strip()
        if head != request.revision:
            raise BuildFailedError(
                f"whisper.cpp {request.tag} resolved to {head}, but ges2t pins "
                f"{request.revision}; refusing to build an unpinned revision"
            )

    def _configure(
        self,
        cmake: Path,
        request: BuildRequest,
        source: Path,
        build_tree: Path,
    ) -> None:
        """Configure the library-only build with the pinned recipe."""
        argv = [
            str(cmake),
            "-S",
            str(source),
            "-B",
            str(build_tree),
            "-DCMAKE_BUILD_TYPE=Release",
            "-DWHISPER_BUILD_TESTS=OFF",
            "-DWHISPER_BUILD_EXAMPLES=OFF",
            # $ORIGIN RUNPATH, so the installed set is self-contained: every
            # library is found next to libwhisper.so with no LD_LIBRARY_PATH
            # (verified with readelf + ctypes; see 001-comment.md).
            "-DCMAKE_BUILD_WITH_INSTALL_RPATH=ON",
            "-DCMAKE_INSTALL_RPATH=$ORIGIN",
        ]
        if request.variant is BuildVariant.VULKAN:
            argv.append("-DGGML_VULKAN=ON")
        self._runner.run(
            argv, cwd=None, timeout_seconds=CONFIGURE_TIMEOUT_SECONDS
        )


def _install_libraries(
    build_bin: Path,
    destination: Path,
    variant: BuildVariant,
) -> EngineLibraries:
    """Copy the built library set into ``destination``, preserving symlinks.

    Symlinks are recreated as symlinks, so the SONAME chain stays intact and
    ``libwhisper.so`` keeps resolving. Existing files are replaced, which is what
    makes a rebuild over an old install safe.
    """
    names = sorted(
        {path.name for pattern in LIBRARY_GLOBS for path in build_bin.glob(pattern)}
    )
    entry_name = ENTRY_LIBRARY_NAME
    if entry_name not in names:
        found = ", ".join(names) if names else "nothing"
        raise BuildFailedError(
            f"the {BUILD_TARGET} target produced no {entry_name} in {build_bin} "
            f"(found: {found})"
        )

    destination.mkdir(parents=True, exist_ok=True)
    for name in names:
        source = build_bin / name
        target = destination / name
        if target.is_symlink() or target.exists():
            target.unlink()
        if source.is_symlink():
            os.symlink(os.readlink(source), target)
        else:
            shutil.copy2(source, target, follow_symlinks=False)

    ggml = tuple(
        destination / name for name in names if name.startswith("libggml")
    )
    if variant is BuildVariant.VULKAN and not any(
        path.name.startswith("libggml-vulkan") for path in ggml
    ):
        raise BuildFailedError(
            f"a Vulkan build produced no libggml-vulkan.so in {build_bin}"
        )
    return EngineLibraries(libwhisper=destination / entry_name, ggml=ggml)


# --- Orchestration -------------------------------------------------------------


def _report(progress: ProgressCallback | None, fraction: float) -> None:
    """Forward the completed fraction when a callback was supplied.

    A broken progress sink must never abort an otherwise successful build, so
    callback exceptions are swallowed (``KeyboardInterrupt``/``SystemExit`` still
    propagate). Same contract as :mod:`ges2t.model_store`.
    """
    if progress is not None:
        try:
            progress(fraction)
        except Exception:
            pass


def _remove(path: Path) -> None:
    """Delete ``path`` if it exists."""
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def ensure_engine(
    *,
    version: str = WHISPER_CPP_VERSION,
    tag: str = WHISPER_CPP_TAG,
    revision: str = WHISPER_CPP_REVISION,
    directory: Path | None = None,
    jobs: int | None = None,
    progress: ProgressCallback | None = None,
    system: SystemProbe | None = None,
    builder: EngineBuilder | None = None,
) -> EngineBuild:
    """Ensure a whisper.cpp engine is built and return it (ADR 0021 §7).

    This is the module's composition point and the only place a concrete
    collaborator is constructed (ADR 0019): every dependency the caller can vary
    is a Protocol-typed parameter, defaulting to the real implementation.

    Order of operations, each one deliberate:

    1. **Idempotency first.** A complete installed engine is returned untouched —
       no clone, no ``cmake``, no network.
    2. **Variant.** Vulkan only when the runtime, dev headers and shader compiler
       are all present; otherwise CPU-only, with no error.
    3. **Prerequisites.** Checked before anything is fetched or compiled; a
       missing one raises :class:`PrerequisiteError` naming the exact
       ``pacman`` line. Nothing is installed (ADR 0021 §7).
    4. **Build.** The only network access is the pinned clone inside the builder
       (ADR 0018).
    """
    probe = system if system is not None else HostSystemProbe()
    target = directory if directory is not None else engine_dir(version)

    installed = verify_build(target)
    if installed is not None:
        _report(progress, 1.0)
        return installed

    variant = (
        BuildVariant.VULKAN if inspect_vulkan(probe).available else BuildVariant.CPU
    )
    report = check_prerequisites(variant, probe)
    if not report.satisfied:
        raise PrerequisiteError(report)

    request = BuildRequest(
        version=version,
        tag=tag,
        revision=revision,
        url=WHISPER_CPP_URL,
        variant=variant,
        directory=target,
        jobs=jobs if jobs is not None else default_jobs(),
    )
    engine_builder = (
        builder if builder is not None else WhisperCppBuilder(system=probe)
    )
    return engine_builder.build(request, progress)
