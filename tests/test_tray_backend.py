"""The mandatory AppIndicator backend assertion (ticket 17, ADR 0013).

Every test injects fake modules into ``sys.modules`` so the assertion's decision
is exercised without importing the real ``gi`` or ``pystray`` and without a
display or a real tray icon. This is the behaviour the whole ticket exists for:
``pystray`` must never be allowed to silently select the XEmbed backend that
Plasma 6.7.5 cannot display.
"""

from __future__ import annotations

import sys
import types

import pytest

from ges2t import pipeline
from ges2t.config import Config, UIConfig
from ges2t.ui import tray
from ges2t.ui.status import NullStatusUI


class _FakeGi(types.ModuleType):
    """A stand-in for ``gi`` that records ``require_version`` and can fail."""

    def __init__(
        self,
        *,
        exc: Exception | None = None,
        unavailable: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__("gi")
        self.require_calls: list[tuple[str, str]] = []
        self._exc = exc
        self._unavailable = unavailable

    def require_version(self, namespace: str, version: str) -> None:
        self.require_calls.append((namespace, version))
        if self._exc is not None:
            raise self._exc
        if namespace in self._unavailable:
            raise ValueError(f"Namespace {namespace} not available for version {version}")


def _fake_pystray(icon_module: str) -> types.ModuleType:
    """Build a fake ``pystray`` whose ``Icon`` claims to come from ``icon_module``."""
    module = types.ModuleType("pystray")
    icon_class = type("Icon", (), {"__module__": icon_module})
    setattr(module, "Icon", icon_class)
    return module


class _RefusingThread:
    """A stand-in for ``threading.Thread`` whose ``start`` fails like an exhausted OS."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def start(self) -> None:
        raise RuntimeError("can't start new thread")

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float | None = None) -> None:
        return None


class _ThreadlessIcon:
    """The minimum icon ``TrayStatusUI._apply_display`` touches, with no display."""

    title = ""
    icon: object = None

    def update_menu(self) -> None:
        return None


# --- assert_appindicator_backend --------------------------------------------


def test_assertion_passes_with_fake_gi_and_appindicator_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_gi = _FakeGi()
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(
        sys.modules, "pystray", _fake_pystray("pystray._appindicator")
    )

    tray.assert_appindicator_backend()

    assert fake_gi.require_calls == [("AppIndicator3", "0.1")]


def test_assertion_rejects_the_xorg_backend_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "gi", _FakeGi())
    monkeypatch.setitem(sys.modules, "pystray", _fake_pystray("pystray._xorg"))

    with pytest.raises(tray.TrayBackendError) as excinfo:
        tray.assert_appindicator_backend()

    message = str(excinfo.value)
    assert "_xorg" in message
    assert "python-gobject" in message
    assert "gtk3" in message
    assert "libappindicator" in message
    assert "--system-site-packages" in message


def test_assertion_rejects_a_missing_gi_with_an_actionable_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # ``None`` in sys.modules makes ``import gi`` raise ImportError, exactly as a
    # venv created without --system-site-packages does.
    monkeypatch.setitem(sys.modules, "gi", None)

    with pytest.raises(tray.TrayBackendError) as excinfo:
        tray.assert_appindicator_backend()

    message = str(excinfo.value)
    assert "python-gobject" in message
    assert "gtk3" in message
    assert "libappindicator" in message
    assert "--system-site-packages" in message


def test_assertion_rejects_a_missing_appindicator_typelib(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_gi = _FakeGi(exc=ValueError("Namespace AppIndicator3 not available"))
    monkeypatch.setitem(sys.modules, "gi", fake_gi)

    with pytest.raises(tray.TrayBackendError) as excinfo:
        tray.assert_appindicator_backend()

    assert "AppIndicator3" in str(excinfo.value)


def test_assertion_accepts_ayatana_when_appindicator3_is_absent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # pystray._appindicator falls back to AyatanaAppIndicator3; a machine with
    # only that typelib has a working tray and must not be refused (review 002).
    fake_gi = _FakeGi(unavailable=frozenset({"AppIndicator3"}))
    monkeypatch.setitem(sys.modules, "gi", fake_gi)
    monkeypatch.setitem(
        sys.modules, "pystray", _fake_pystray("pystray._appindicator")
    )

    tray.assert_appindicator_backend()

    assert fake_gi.require_calls == [
        ("AppIndicator3", "0.1"),
        ("AyatanaAppIndicator3", "0.1"),
    ]


def test_assertion_rejects_when_neither_typelib_is_available(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_gi = _FakeGi(
        unavailable=frozenset({"AppIndicator3", "AyatanaAppIndicator3"})
    )
    monkeypatch.setitem(sys.modules, "gi", fake_gi)

    with pytest.raises(tray.TrayBackendError) as excinfo:
        tray.assert_appindicator_backend()

    message = str(excinfo.value)
    assert "AppIndicator3" in message
    assert "AyatanaAppIndicator3" in message
    assert fake_gi.require_calls == [
        ("AppIndicator3", "0.1"),
        ("AyatanaAppIndicator3", "0.1"),
    ]


# --- the composition root's fallback ----------------------------------------


def test_build_status_ui_is_null_when_the_tray_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called: list[int] = []

    def forbidden(send_command: object) -> object:
        called.append(1)
        raise AssertionError("must not build a tray when ui.tray = false")

    monkeypatch.setattr(pipeline, "build_tray_status_ui", forbidden)

    ui = pipeline.build_status_ui(Config(ui=UIConfig(tray=False)))

    assert isinstance(ui, NullStatusUI)
    assert called == []


def test_build_status_ui_falls_back_with_a_warning_when_the_assertion_fails(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(send_command: object) -> object:
        raise tray.TrayBackendError("pystray selected '_xorg'")

    monkeypatch.setattr(pipeline, "build_tray_status_ui", refuse)

    ui = pipeline.build_status_ui(Config(ui=UIConfig(tray=True)))

    assert isinstance(ui, NullStatusUI)
    stderr = capsys.readouterr().err
    assert "pystray selected '_xorg'" in stderr
    assert "fully usable from the CLI" in stderr


@pytest.mark.parametrize(
    "error_type",
    [ImportError, ModuleNotFoundError, OSError],
)
def test_build_status_ui_falls_back_for_environmental_failures(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error_type: type[Exception],
) -> None:
    # A missing gi/pystray/PIL or a missing display/DBus is environmental, not a
    # bug: it must still degrade to NullStatusUI with the warning (ADR 0013).
    def unavailable(send_command: object) -> object:
        raise error_type("no display or typelib")

    monkeypatch.setattr(pipeline, "build_tray_status_ui", unavailable)

    ui = pipeline.build_status_ui(Config(ui=UIConfig(tray=True)))

    assert isinstance(ui, NullStatusUI)
    stderr = capsys.readouterr().err
    assert "no display or typelib" in stderr
    assert "fully usable from the CLI" in stderr


def test_build_status_ui_falls_back_when_the_tray_thread_cannot_start(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Review 003 (blocking, Angle A): the OS refusing a thread is environmental,
    # so TrayStatusUI.start() names it locally as TrayBackendError and the tray
    # degrades to NullStatusUI instead of aborting ges2t (ADR 0013). The real
    # build_tray_status_ui path is exercised, minus the display and the thread.
    monkeypatch.setattr(tray, "assert_appindicator_backend", lambda: None)
    monkeypatch.setattr(tray.TrayStatusUI, "_create_icon", lambda self: _ThreadlessIcon())
    monkeypatch.setattr(tray, "threading", types.SimpleNamespace(Thread=_RefusingThread))

    ui = pipeline.build_status_ui(Config(ui=UIConfig(tray=True)))

    assert isinstance(ui, NullStatusUI)
    stderr = capsys.readouterr().err
    assert "the tray thread could not be started" in stderr
    assert "can't start new thread" in stderr
    assert "fully usable from the CLI" in stderr


@pytest.mark.parametrize(
    "error_type",
    [AttributeError, TypeError, NameError, AssertionError, KeyError],
)
def test_build_status_ui_propagates_programming_errors(
    monkeypatch: pytest.MonkeyPatch, error_type: type[Exception]
) -> None:
    # Review 002 (blocking): a bug in tray construction must crash loudly, not
    # be converted into "the tray is disabled; ges2t remains fully usable".
    def broken(send_command: object) -> object:
        raise error_type("a genuine bug in tray construction")

    monkeypatch.setattr(pipeline, "build_tray_status_ui", broken)

    with pytest.raises(error_type):
        pipeline.build_status_ui(Config(ui=UIConfig(tray=True)))


def test_run_service_builds_the_status_ui_when_none_is_injected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = NullStatusUI()
    seen: list[object] = []
    monkeypatch.setattr(pipeline, "build_status_ui", lambda config: sentinel)

    def stop_startup(config: Config, status_ui: object) -> object:
        seen.append(status_ui)
        raise pipeline.PipelineError("stop before serving")

    monkeypatch.setattr(pipeline, "_startup", stop_startup)

    result = pipeline.run_service(Config())

    assert result != 0
    assert seen == [sentinel]
