"""Tests for the ges2t command surface (ADR 0016).

No test starts a real service, downloads anything, or touches the user's real
data or config directories. The socket client and the model store are stubbed
at the ``ges2t.cli`` seam.
"""

from __future__ import annotations

import hashlib
import importlib
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

from ges2t import __version__, cli, model_store
from ges2t.cli import (
    EXIT_ENGINE_FAILURE,
    EXIT_FAILURE,
    EXIT_INTERRUPTED,
    EXIT_SERVICE_NOT_RUNNING,
    EXIT_SUCCESS,
    main,
)
from ges2t.model_store import ArtifactPin, Fetcher, ProgressCallback
from ges2t.service import CommandResponse, ServiceState

SUBCOMMANDS: tuple[str, ...] = (
    "run",
    "toggle",
    "ptt-start",
    "ptt-stop",
    "cancel",
    "status",
    "setup",
    "doctor",
    "models",
)

KEY_BOUND_COMMANDS: tuple[str, ...] = ("toggle", "ptt-start", "ptt-stop", "cancel")

FORBIDDEN_MODULES: tuple[str, ...] = (
    "needle",
    "gi",
    "pystray",
    "ges2t.pipeline",
    "ges2t.doctor",
)


# -- test doubles ---------------------------------------------------------


class StubClient:
    """A ``_send_command`` replacement that returns one canned response."""

    def __init__(self, response: CommandResponse) -> None:
        self.response = response
        self.commands: list[str] = []

    def __call__(self, command: str) -> CommandResponse:
        self.commands.append(command)
        return self.response


class RaisingClient:
    """A ``_send_command`` replacement that raises ``error`` on every call."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(self, command: str) -> CommandResponse:
        raise self.error


class FixedPath:
    """A replacement for ``model_store.model_path``/``engine_path``."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def __call__(self) -> Path:
        return self._path


class FakeEnsure:
    """A replacement for ``model_store.ensure_*`` that records its progress callback."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.calls = 0
        self.progress_callbacks: list[ProgressCallback | None] = []

    def __call__(
        self,
        *,
        fetcher: Fetcher | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        self.calls += 1
        self.progress_callbacks.append(progress)
        if progress is not None:
            progress(1.0)
        return self.path


class FailingEnsure:
    """A replacement for ``model_store.ensure_*`` that raises ``error``."""

    def __init__(self, error: Exception) -> None:
        self.error = error

    def __call__(
        self,
        *,
        fetcher: Fetcher | None = None,
        progress: ProgressCallback | None = None,
    ) -> Path:
        raise self.error


def _ok(
    state: ServiceState = ServiceState.IDLE,
    *,
    message: str | None = None,
) -> CommandResponse:
    return CommandResponse(ok=True, state=state, message=message)


def _failed(state: ServiceState, *, message: str | None = None) -> CommandResponse:
    return CommandResponse(ok=False, state=state, error="test-error", message=message)


def _pin(name: str, payload: bytes) -> ArtifactPin:
    return ArtifactPin(
        name=name,
        url=f"https://example.invalid/{name}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


def _create_sized_file(path: Path, size: int) -> None:
    with path.open("wb") as handle:
        handle.truncate(size)


def _install_missing_module(monkeypatch: pytest.MonkeyPatch, missing: str) -> None:
    """Make ``importlib.import_module(missing)`` fail, leaving every other import alone."""
    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> ModuleType:
        if name == missing:
            raise ModuleNotFoundError(f"No module named {name!r}")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)


# -- version and help -----------------------------------------------------


def test_version_flag_prints_the_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.strip() == __version__


def test_top_level_help_lists_every_subcommand(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--help"])

    assert exit_info.value.code == 0
    out = capsys.readouterr().out
    for name in SUBCOMMANDS:
        assert name in out


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_each_subcommand_help_exits_zero(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([name, "--help"])

    assert exit_info.value.code == 0
    assert f"ges2t {name}" in capsys.readouterr().out


@pytest.mark.parametrize("name", KEY_BOUND_COMMANDS)
def test_key_bound_help_explains_the_kde_binding(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit):
        main([name, "--help"])

    out = capsys.readouterr().out
    assert "System Settings" in out
    assert "Shortcuts" in out
    assert "Command or URL" in out
    assert "key-release" in out


def test_doctor_help_mentions_the_recommended_bindings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        main(["doctor", "--help"])

    out = capsys.readouterr().out
    assert "System Settings" in out
    assert "Shortcuts" in out


def test_no_subcommand_prints_help_and_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == EXIT_SUCCESS
    assert "usage: ges2t" in capsys.readouterr().out


# -- argument parsing -----------------------------------------------------


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_each_subcommand_parses_and_accepts_config(name: str) -> None:
    args = cli.build_parser().parse_args([name, "--config", "/tmp/ges2t-custom.toml"])

    assert args.command == name
    assert args.config == "/tmp/ges2t-custom.toml"


@pytest.mark.parametrize("name", SUBCOMMANDS)
def test_config_defaults_to_none_when_absent(name: str) -> None:
    args = cli.build_parser().parse_args([name])

    assert getattr(args, "config", None) is None


def test_models_verify_flag_parses() -> None:
    args = cli.build_parser().parse_args(["models", "--verify"])

    assert args.verify is True


def test_debug_flag_is_accepted_before_and_after_the_subcommand() -> None:
    parser = cli.build_parser()

    assert parser.parse_args(["--debug", "toggle"]).debug is True
    assert parser.parse_args(["toggle", "--debug"]).debug is True
    assert parser.parse_args(["toggle"]).debug is False


def test_load_config_resolves_the_flag_path(tmp_path: Path) -> None:
    config_file = tmp_path / "broken.toml"
    config_file.write_text("[transcriber\nthis is not toml", encoding="utf-8")
    args = cli.build_parser().parse_args(["run", "--config", str(config_file)])

    with pytest.raises(cli.CliError) as error_info:
        cli._load_config(args)

    assert str(config_file) in str(error_info.value)
    assert error_info.value.hint is not None


# -- thin clients and exit codes ------------------------------------------


def test_documented_exit_code_literals_are_pinned() -> None:
    """ADR 0016 documents these exit codes as user-facing API.

    Compositor bindings and scripts branch on the raw process status, so the
    literal values are contract. Asserting a path against its own constant
    cannot catch a renumbered constant, so pin the numbers independently.
    """
    assert EXIT_SUCCESS == 0
    assert EXIT_FAILURE == 1
    assert EXIT_SERVICE_NOT_RUNNING == 2
    assert EXIT_ENGINE_FAILURE == 3
    assert EXIT_INTERRUPTED == 130


@pytest.mark.parametrize(
    ("name", "verb"),
    (
        ("toggle", "toggle"),
        ("ptt-start", "ptt-start"),
        ("ptt-stop", "ptt-stop"),
        ("cancel", "cancel"),
    ),
)
def test_thin_client_sends_the_matching_verb(
    name: str, verb: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = StubClient(_ok(ServiceState.RECORDING))
    monkeypatch.setattr(cli, "_send_command", client)

    assert main([name]) == EXIT_SUCCESS
    assert client.commands == [verb]


def test_toggle_without_a_service_exits_two(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    # The real client is used here: the path is a private temp dir with no socket.
    # ADR 0016 documents 2 as the user-facing "service not running" status, so
    # assert the literal rather than the constant that could be renumbered.
    assert main(["toggle"]) == 2

    err = capsys.readouterr().err
    assert "not running" in err
    assert "ges2t run" in err


def test_engine_failure_exits_three(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = StubClient(_failed(ServiceState.ERROR, message="injection failed"))
    monkeypatch.setattr(cli, "_send_command", client)

    # ADR 0016 documents 3 as the user-facing "engine/injection failed" status.
    assert main(["toggle"]) == 3

    err = capsys.readouterr().err
    assert "injection failed" in err


def test_service_refusal_that_is_not_an_engine_error_exits_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = StubClient(_failed(ServiceState.IDLE, message="the service is not recording"))
    monkeypatch.setattr(cli, "_send_command", client)

    assert main(["cancel"]) == EXIT_FAILURE
    assert "not recording" in capsys.readouterr().err


def test_status_prints_the_state_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = StubClient(_ok(ServiceState.RECORDING))
    monkeypatch.setattr(cli, "_send_command", client)

    assert main(["status"]) == EXIT_SUCCESS
    assert capsys.readouterr().out.strip() == "recording"


def test_status_prints_error_state_but_still_exits_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client = StubClient(_failed(ServiceState.ERROR, message="engine exploded"))
    monkeypatch.setattr(cli, "_send_command", client)

    assert main(["status"]) == EXIT_SUCCESS
    assert capsys.readouterr().out.strip() == "error"


def test_status_without_a_service_exits_two(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))

    # ADR 0016 documents 2 as the user-facing "service not running" status.
    assert main(["status"]) == 2


def test_unexpected_failure_has_no_traceback_without_debug(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_send_command", RaisingClient(RuntimeError("boom")))

    assert main(["toggle"]) == EXIT_FAILURE

    err = capsys.readouterr().err
    assert "boom" in err
    assert "Traceback" not in err


def test_debug_flag_prints_the_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "_send_command", RaisingClient(RuntimeError("boom")))

    assert main(["--debug", "toggle"]) == EXIT_FAILURE
    assert "Traceback" in capsys.readouterr().err


def test_debug_environment_variable_prints_the_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("GES2T_DEBUG", "1")
    monkeypatch.setattr(cli, "_send_command", RaisingClient(RuntimeError("boom")))

    assert main(["toggle"]) == EXIT_FAILURE
    assert "Traceback" in capsys.readouterr().err


# -- laziness -------------------------------------------------------------


def _forbidden_modules_after_importing_cli_in_a_subprocess() -> list[str]:
    """Return the forbidden modules present after ``import ges2t.cli`` alone.

    A subprocess is the only way to see *import-time* dependencies: this test
    module imports ``ges2t.cli`` at collection time and Python never re-runs a
    module-level import statement, so deleting entries from ``sys.modules`` in
    the parent cannot reveal a top-level ``import pystray`` added to ``cli.py``.
    """
    script = (
        "import sys\n"
        "import ges2t.cli\n"
        f"forbidden = {FORBIDDEN_MODULES!r}\n"
        "print('\\n'.join(name for name in forbidden if name in sys.modules))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return [line for line in completed.stdout.splitlines() if line]


def test_importing_the_cli_does_not_load_the_engine_gui_or_later_tickets() -> None:
    assert _forbidden_modules_after_importing_cli_in_a_subprocess() == []


def test_toggle_does_not_import_the_engine_gui_or_later_tickets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in FORBIDDEN_MODULES:
        monkeypatch.delitem(sys.modules, name, raising=False)
    client = StubClient(_ok(ServiceState.RECORDING))
    monkeypatch.setattr(cli, "_send_command", client)

    assert main(["toggle"]) == EXIT_SUCCESS
    assert client.commands == ["toggle"]
    assert [name for name in FORBIDDEN_MODULES if name in sys.modules] == []


# -- run and doctor lazy-import contract ----------------------------------


def test_run_reports_a_missing_pipeline_module_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_missing_module(monkeypatch, "ges2t.pipeline")

    assert main(["run"]) == EXIT_FAILURE

    err = capsys.readouterr().err
    assert "ges2t.pipeline" in err
    assert "Traceback" not in err


def test_doctor_reports_a_missing_doctor_module_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _install_missing_module(monkeypatch, "ges2t.doctor")

    assert main(["doctor"]) == EXIT_FAILURE

    err = capsys.readouterr().err
    assert "ges2t.doctor" in err
    assert "Traceback" not in err


# -- setup ----------------------------------------------------------------


def test_setup_calls_the_model_store_functions_with_progress(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_file = tmp_path / "whistle.cact"
    engine_file = tmp_path / "libneedle.so"
    ensure_model = FakeEnsure(model_file)
    ensure_engine = FakeEnsure(engine_file)
    monkeypatch.setattr(model_store, "ensure_model", ensure_model)
    monkeypatch.setattr(model_store, "ensure_engine", ensure_engine)

    assert main(["setup"]) == EXIT_SUCCESS

    assert ensure_model.calls == 1
    assert ensure_engine.calls == 1
    assert all(callback is not None for callback in ensure_model.progress_callbacks)
    assert all(callback is not None for callback in ensure_engine.progress_callbacks)

    out = capsys.readouterr().out
    assert str(model_file) in out
    assert str(engine_file) in out
    assert "complete" in out.lower()


def test_setup_reports_a_model_store_failure(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        model_store,
        "ensure_model",
        FailingEnsure(model_store.ModelStoreError("download refused")),
    )

    assert main(["setup"]) == EXIT_FAILURE

    err = capsys.readouterr().err
    assert "download refused" in err
    assert "Traceback" not in err


# -- models ---------------------------------------------------------------


def test_models_lists_present_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_file = tmp_path / "whistle.cact"
    engine_file = tmp_path / "libneedle.so"
    _create_sized_file(model_file, model_store.MODEL_SIZE)
    _create_sized_file(engine_file, model_store.ENGINE_SIZE)
    monkeypatch.setattr(model_store, "model_path", FixedPath(model_file))
    monkeypatch.setattr(model_store, "engine_path", FixedPath(engine_file))

    assert main(["models"]) == EXIT_SUCCESS

    out = capsys.readouterr().out
    assert "whistle.cact" in out
    assert "libneedle.so" in out
    assert out.count("present ") == 2


def test_models_lists_missing_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(model_store, "model_path", FixedPath(tmp_path / "none.cact"))
    monkeypatch.setattr(model_store, "engine_path", FixedPath(tmp_path / "none.so"))

    assert main(["models"]) == EXIT_SUCCESS

    out = capsys.readouterr().out
    assert out.count("missing ") == 2


def test_models_verify_reports_verified_artifacts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_bytes = b"whistle-weights-payload"
    engine_bytes = b"needle-engine-payload"
    model_file = tmp_path / "whistle.cact"
    engine_file = tmp_path / "libneedle.so"
    model_file.write_bytes(model_bytes)
    engine_file.write_bytes(engine_bytes)
    monkeypatch.setattr(model_store, "MODEL_PIN", _pin("whistle.cact", model_bytes))
    monkeypatch.setattr(model_store, "ENGINE_PIN", _pin("libneedle.so", engine_bytes))
    monkeypatch.setattr(model_store, "model_path", FixedPath(model_file))
    monkeypatch.setattr(model_store, "engine_path", FixedPath(engine_file))

    assert main(["models", "--verify"]) == EXIT_SUCCESS

    out = capsys.readouterr().out
    assert out.count("verified ") == 2


def test_models_verify_reports_a_corrupt_artifact(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    model_bytes = b"whistle-weights-payload"
    engine_bytes = b"needle-engine-payload"
    model_file = tmp_path / "whistle.cact"
    engine_file = tmp_path / "libneedle.so"
    model_file.write_bytes(model_bytes)
    # Same byte size as the pin, different content: only the hash can catch it.
    engine_file.write_bytes(b"\x00" * len(engine_bytes))
    monkeypatch.setattr(model_store, "MODEL_PIN", _pin("whistle.cact", model_bytes))
    monkeypatch.setattr(model_store, "ENGINE_PIN", _pin("libneedle.so", engine_bytes))
    monkeypatch.setattr(model_store, "model_path", FixedPath(model_file))
    monkeypatch.setattr(model_store, "engine_path", FixedPath(engine_file))

    assert main(["models", "--verify"]) == EXIT_FAILURE

    captured = capsys.readouterr()
    assert "corrupt " in captured.out
    assert "ges2t setup" in captured.err
