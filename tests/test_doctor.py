"""Tests for ``ges2t doctor`` (ticket 12, ADR 0016).

Every external probe is injected: no test starts a tray, opens a display, spawns
a real ``pw-dump``/``ydotoold``, contacts a daemon, or reads the user's real data
directory. Each check's ``OK``/``WARN``/``FAIL`` branch is exercised with a fake
probe, and the report-level guarantees (exit code, fix lines, no network) are
asserted through :func:`run_doctor` with a fully fake :class:`DoctorProbes`.
"""

from __future__ import annotations

import hashlib
import importlib
import json
import os
import socket
import subprocess
import sys
import types
import urllib.request
from dataclasses import replace
from pathlib import Path

import pytest

from ges2t import cli, doctor, model_store
from ges2t.config import Config, ConfigError, ConfigLoadResult, default_config
from ges2t.doctor import (
    EXIT_FAILURE,
    EXIT_SUCCESS,
    ArtifactTarget,
    CheckResult,
    CheckStatus,
    DoctorOptions,
    DoctorProbes,
    DoctorReport,
    build_report,
    check_architecture,
    check_audio_device,
    check_cactus_needle_importable,
    check_clipboard_backend,
    check_engine_present,
    check_hotkey_bindings,
    check_keyboard_layout,
    check_model_present,
    check_model_verified,
    check_package_imports,
    check_pipewire_session,
    check_pw_record,
    check_service_socket,
    check_tray_backend,
    check_tray_failures,
    check_uinput,
    check_versions,
    check_ydotoold,
    diagnose_config,
    diagnose_engine_config,
    diagnose_injection_config,
    render_report,
    run_doctor,
)
from ges2t.injectors.keymap import XkbKeymapCompileError
from ges2t.injectors.layout import (
    AUTO_LAYOUT,
    InjectionConfigError,
    LayoutResolutionError,
    LayoutSource,
    ResolvedLayout,
    YdotoolConfig,
)
from ges2t.model_store import ArtifactPin
from ges2t.transcribers.whistle_config import WhistleConfigError
from ges2t.ui.status import TrayFailure

# -- helpers ---------------------------------------------------------------


def _pin(name: str, payload: bytes) -> ArtifactPin:
    """Build a pin that matches ``payload`` exactly (used for verify tests)."""
    return ArtifactPin(
        name=name,
        url=f"https://example.invalid/{name}",
        sha256=hashlib.sha256(payload).hexdigest(),
        size=len(payload),
    )


def _write_sized(path: Path, size: int) -> None:
    """Create ``path`` (and parents) as a file of exactly ``size`` bytes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)


def _write_payload(path: Path, payload: bytes) -> None:
    """Create ``path`` (and parents) containing exactly ``payload``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


_LIVE_KEYMAP = "xkb_keymap {}"


def _live_layout(layout: str = AUTO_LAYOUT, variant: str = "") -> ResolvedLayout:
    """A live-keymap resolution, the usual production outcome (ADR 0020)."""
    return ResolvedLayout(
        source=LayoutSource.LIVE_KEYMAP,
        layout=None,
        variant=None,
        display_name=None,
        keymap_text=_LIVE_KEYMAP,
    )


_PASSING_PROBES = DoctorProbes(
    importer=importlib.import_module,
    version_reader=lambda name: model_store.PINNED_CACTUS_NEEDLE_VERSION,
    node_probe=lambda: frozenset({"default"}),
    session_probe=lambda: True,
    binary_lookup=lambda name: f"/usr/bin/{name}",
    ydotoold_probe=lambda path: True,
    uinput_probe=lambda path: True,
    tray_assertion=lambda: None,
    layout_resolver=lambda layout, variant: _live_layout(layout, variant),
    layout_compiler=lambda resolved: None,
    listening_probe=lambda path: True,
    tray_failure_probe=None,
)


def _stub_model_dir(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point ``model_store``'s data dir at ``tmp_path`` and create both artifacts."""
    monkeypatch.setattr(model_store, "default_data_dir", lambda: tmp_path)
    _write_sized(model_store.model_path(), model_store.MODEL_PIN.size)
    _write_sized(model_store.engine_path(), model_store.ENGINE_PIN.size)


# -- result objects --------------------------------------------------------


def test_ok_result_needs_no_fix_line() -> None:
    result = CheckResult(name="x", status=CheckStatus.OK, summary="fine")
    assert result.fix is None


@pytest.mark.parametrize("status", [CheckStatus.WARN, CheckStatus.FAIL])
def test_non_ok_result_requires_a_non_empty_fix_line(status: CheckStatus) -> None:
    with pytest.raises(ValueError):
        CheckResult(name="x", status=status, summary="bad", fix=None)
    with pytest.raises(ValueError):
        CheckResult(name="x", status=status, summary="bad", fix="   ")
    assert CheckResult(name="x", status=status, summary="bad", fix="do y").fix == "do y"


def test_report_exit_code_is_zero_when_only_warnings() -> None:
    report = DoctorReport(
        results=(
            CheckResult("a", CheckStatus.OK, "fine"),
            CheckResult("b", CheckStatus.WARN, "meh", fix="do y"),
        )
    )
    assert report.exit_code == EXIT_SUCCESS
    assert report.failures == ()
    assert len(report.warnings) == 1


def test_report_exit_code_is_nonzero_when_any_check_fails() -> None:
    report = DoctorReport(
        results=(CheckResult("a", CheckStatus.FAIL, "broken", fix="run ges2t setup"),)
    )
    assert report.exit_code == EXIT_FAILURE
    assert report.exit_code != EXIT_SUCCESS


# -- package imports -------------------------------------------------------


def test_package_imports_ok() -> None:
    module = types.ModuleType("ges2t.config")

    result = check_package_imports(importer=lambda name: module)

    assert result.status is CheckStatus.OK
    assert "modules import" in result.summary


def test_package_imports_fail_names_the_broken_module_and_a_fix() -> None:
    def importer(name: str) -> types.ModuleType:
        if name == "ges2t.service":
            raise ImportError("no service")
        return types.ModuleType(name)

    result = check_package_imports(importer=importer)

    assert result.status is CheckStatus.FAIL
    assert "ges2t.service" in result.summary
    assert result.fix is not None and "uv sync" in result.fix


# -- architecture ----------------------------------------------------------


def test_architecture_ok_when_platform_is_pinned() -> None:
    result = check_architecture(
        platform_key="linux-x86_64", supported_platforms=frozenset({"linux-x86_64"})
    )

    assert result.status is CheckStatus.OK


def test_architecture_fail_names_the_pinned_platforms() -> None:
    result = check_architecture(
        platform_key="darwin-arm64", supported_platforms=frozenset({"linux-x86_64"})
    )

    assert result.status is CheckStatus.FAIL
    assert "darwin-arm64" in result.summary
    assert result.fix is not None and "linux-x86_64" in result.fix


# -- model present ---------------------------------------------------------


def test_model_present_ok_at_the_pinned_size(tmp_path: Path) -> None:
    target = tmp_path / "whistle.cact"
    payload = b"model-bytes"
    _write_payload(target, payload)
    artifact = ArtifactTarget(path=target, expected_size=len(payload), pin=_pin("whistle.cact", payload))

    result = check_model_present(default_config(), target=artifact)

    assert result.status is CheckStatus.OK
    assert "whistle.cact" in result.summary


def test_model_present_fail_when_missing(tmp_path: Path) -> None:
    artifact = ArtifactTarget(path=tmp_path / "missing.cact", expected_size=10, pin=None)

    result = check_model_present(default_config(), target=artifact)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "ges2t setup" in result.fix


def test_model_present_fail_on_wrong_size(tmp_path: Path) -> None:
    target = tmp_path / "whistle.cact"
    _write_sized(target, 5)
    artifact = ArtifactTarget(path=target, expected_size=10, pin=None)

    result = check_model_present(default_config(), target=artifact)

    assert result.status is CheckStatus.FAIL
    assert "5 bytes" in result.summary and "10 bytes" in result.summary
    assert result.fix is not None and "ges2t setup" in result.fix


def test_model_present_accepts_a_configured_override(tmp_path: Path) -> None:
    target = tmp_path / "custom.cact"
    _write_sized(target, 3)
    config = Config(transcriber=default_config().transcriber.__class__(model_path=str(target)))

    result = check_model_present(config)

    assert result.status is CheckStatus.OK


# -- model verified --------------------------------------------------------


def test_model_verified_skipped_without_the_flag(tmp_path: Path) -> None:
    target = tmp_path / "whistle.cact"
    payload = b"payload"
    _write_sized(target, len(payload))
    artifact = ArtifactTarget(path=target, expected_size=len(payload), pin=_pin("whistle.cact", payload))

    result = check_model_verified(default_config(), verify=False, target=artifact)

    assert result.status is CheckStatus.OK
    assert "--verify" in result.summary


def test_model_verified_ok_when_the_sha256_matches(tmp_path: Path) -> None:
    target = tmp_path / "whistle.cact"
    payload = b"exact-model"
    _write_payload(target, payload)
    artifact = ArtifactTarget(path=target, expected_size=len(payload), pin=_pin("whistle.cact", payload))

    result = check_model_verified(default_config(), verify=True, target=artifact)

    assert result.status is CheckStatus.OK
    assert "matches the pinned sha256" in result.summary


def test_model_verified_fail_on_sha256_mismatch(tmp_path: Path) -> None:
    target = tmp_path / "whistle.cact"
    _write_sized(target, 4)
    artifact = ArtifactTarget(path=target, expected_size=4, pin=_pin("whistle.cact", b"other"))

    result = check_model_verified(default_config(), verify=True, target=artifact)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "ges2t setup" in result.fix


def test_model_verified_fail_when_missing(tmp_path: Path) -> None:
    artifact = ArtifactTarget(path=tmp_path / "nope.cact", expected_size=1, pin=_pin("x", b"y"))

    result = check_model_verified(default_config(), verify=True, target=artifact)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "ges2t setup" in result.fix


def test_model_verified_skips_a_custom_model_path(tmp_path: Path) -> None:
    target = tmp_path / "custom.cact"
    _write_sized(target, 3)
    config = Config(transcriber=default_config().transcriber.__class__(model_path=str(target)))

    result = check_model_verified(config, verify=True)

    assert result.status is CheckStatus.OK
    assert "no ges2t pin" in result.summary


# -- engine present --------------------------------------------------------


def test_engine_present_ok(tmp_path: Path) -> None:
    payload = b"engine"
    target = tmp_path / "libneedle.so"
    _write_sized(target, len(payload))
    artifact = ArtifactTarget(path=target, expected_size=len(payload), pin=_pin("libneedle.so", payload))

    result = check_engine_present(target=artifact)

    assert result.status is CheckStatus.OK
    assert "libneedle.so" in result.summary


def test_engine_present_fail_when_missing(tmp_path: Path) -> None:
    artifact = ArtifactTarget(path=tmp_path / "nope.so", expected_size=1, pin=_pin("libneedle.so", b"x"))

    result = check_engine_present(target=artifact)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "ges2t setup" in result.fix


def test_engine_present_fail_on_wrong_size(tmp_path: Path) -> None:
    target = tmp_path / "libneedle.so"
    _write_sized(target, 2)
    artifact = ArtifactTarget(path=target, expected_size=9, pin=_pin("libneedle.so", b"x"))

    result = check_engine_present(target=artifact)

    assert result.status is CheckStatus.FAIL
    assert "2 bytes" in result.summary


def test_engine_present_fail_when_no_pin_exists(tmp_path: Path) -> None:
    artifact = ArtifactTarget(path=tmp_path / "libneedle.so", expected_size=None, pin=None)

    result = check_engine_present(target=artifact)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "linux-x86_64" in result.fix


# -- cactus-needle importable ----------------------------------------------


def test_cactus_needle_importable_ok() -> None:
    module = types.ModuleType("needle")

    result = check_cactus_needle_importable(importer=lambda name: module)

    assert result.status is CheckStatus.OK


def test_cactus_needle_import_defuses_telemetry_and_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NEEDLE_TELEMETRY", raising=False)
    monkeypatch.delenv("DO_NOT_TRACK", raising=False)
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)

    result = check_cactus_needle_importable(importer=lambda name: types.ModuleType(name))

    assert result.status is CheckStatus.OK
    assert os.environ["NEEDLE_TELEMETRY"] == "0"
    assert os.environ["DO_NOT_TRACK"] == "1"
    assert os.environ["HF_HUB_OFFLINE"] == "1"


def test_cactus_needle_importable_fail_says_uv_sync() -> None:
    def importer(name: str) -> types.ModuleType:
        raise ModuleNotFoundError(name)

    result = check_cactus_needle_importable(importer=importer)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "uv sync" in result.fix


# -- versions --------------------------------------------------------------


def test_versions_ok_when_installed_matches_the_pin() -> None:
    result = check_versions(
        version_reader=lambda name: model_store.PINNED_CACTUS_NEEDLE_VERSION
    )

    assert result.status is CheckStatus.OK
    assert "engine 3.2.0" in result.summary


def test_versions_warns_on_installed_skew_and_names_both() -> None:
    result = check_versions(version_reader=lambda name: "9.9.9")

    assert result.status is CheckStatus.WARN
    assert "9.9.9" in result.summary and model_store.PINNED_CACTUS_NEEDLE_VERSION in result.summary
    assert result.fix is not None
    assert "9.9.9" in result.fix and model_store.PINNED_CACTUS_NEEDLE_VERSION in result.fix


def test_versions_warns_when_the_distribution_is_absent() -> None:
    def reader(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    result = check_versions(version_reader=reader)

    assert result.status is CheckStatus.WARN
    assert result.fix is not None and "uv sync" in result.fix


# -- audio device ----------------------------------------------------------


def test_audio_device_empty_means_default() -> None:
    result = check_audio_device(default_config(), node_probe=lambda: frozenset())

    assert result.status is CheckStatus.OK
    assert "system default" in result.summary


def test_audio_device_ok_when_present() -> None:
    config = Config(audio=default_config().audio.__class__(device="alsa_input.usb"))

    result = check_audio_device(config, node_probe=lambda: frozenset({"alsa_input.usb"}))

    assert result.status is CheckStatus.OK


def test_audio_device_warns_when_absent_and_says_how_to_list() -> None:
    config = Config(audio=default_config().audio.__class__(device="typo-device"))

    result = check_audio_device(config, node_probe=lambda: frozenset({"default"}))

    assert result.status is CheckStatus.WARN
    assert "typo-device" in result.summary
    assert result.fix is not None and ("wpctl" in result.fix or "pw-dump" in result.fix)


def test_audio_device_fails_on_a_syntactically_invalid_name() -> None:
    config = Config(audio=default_config().audio.__class__(device="bad\nname"))

    result = check_audio_device(config, node_probe=lambda: frozenset())

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "device" in result.fix


def test_audio_device_warns_when_the_nodes_cannot_be_read() -> None:
    config = Config(audio=default_config().audio.__class__(device="some-device"))

    result = check_audio_device(config, node_probe=lambda: None)

    assert result.status is CheckStatus.WARN
    assert result.fix is not None


# -- pw-record and the session ---------------------------------------------


def test_pw_record_ok() -> None:
    result = check_pw_record(lookup=lambda name: "/usr/bin/pw-record")

    assert result.status is CheckStatus.OK


def test_pw_record_fail_names_the_package() -> None:
    result = check_pw_record(lookup=lambda name: None)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "pipewire" in result.fix


def test_pipewire_session_ok() -> None:
    assert check_pipewire_session(probe=lambda: True).status is CheckStatus.OK


def test_pipewire_session_fail_says_how_to_check() -> None:
    result = check_pipewire_session(probe=lambda: False)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "pw-cli info 0" in result.fix


# -- ydotoold and uinput ---------------------------------------------------


def test_ydotoold_ok_when_the_socket_accepts() -> None:
    result = check_ydotoold(socket_path=Path("/tmp/.ydotool_socket"), probe=lambda path: True)

    assert result.status is CheckStatus.OK


def test_ydotoold_fail_gives_the_systemctl_fix() -> None:
    result = check_ydotoold(socket_path=Path("/tmp/.ydotool_socket"), probe=lambda path: False)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None
    assert "systemctl --user enable --now ydotool" in result.fix
    assert "ydotoold" in result.fix


def test_ydotoold_probe_accepts_a_datagram_socket(tmp_path: Path) -> None:
    """ydotoold listens on a SOCK_DGRAM socket; the probe must use that type."""
    path = tmp_path / "daemon.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    try:
        server.bind(str(path))
        assert doctor._ydotoold_reachable(path) is True
    finally:
        server.close()


def test_ydotoold_probe_rejects_a_stream_socket(tmp_path: Path) -> None:
    """A SOCK_STREAM listener is not ydotoold; the probe must not accept it.

    This is the mutation guard for the measured bug: a SOCK_STREAM probe reports
    a running ydotoold as unreachable, and a stream listener as reachable.
    """
    path = tmp_path / "stream.sock"
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.bind(str(path))
        server.listen(1)
        assert doctor._ydotoold_reachable(path) is False
    finally:
        server.close()


def test_uinput_ok_when_writable() -> None:
    assert check_uinput(path=Path("/dev/uinput"), probe=lambda path: True).status is CheckStatus.OK


def test_uinput_fail_mentions_the_input_group_and_a_re_login() -> None:
    result = check_uinput(path=Path("/dev/uinput"), probe=lambda path: False)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None
    assert "input" in result.fix and "log out" in result.fix


# -- tray backend and failures ---------------------------------------------


def test_tray_backend_ok_when_the_assertion_passes() -> None:
    assert check_tray_backend(assertion=lambda: None).status is CheckStatus.OK


def test_tray_backend_fail_names_the_packages_and_the_venv() -> None:
    def assertion() -> None:
        raise RuntimeError("pystray selected 'pystray._xorg'")

    result = check_tray_backend(assertion=assertion)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None
    assert "python-gobject" in result.fix and "--system-site-packages" in result.fix


def _install_fake_pystray(monkeypatch: pytest.MonkeyPatch, icon_module: str) -> None:
    """Install fake ``gi``/``pystray`` modules selecting ``icon_module`` as backend."""
    fake_gi = types.ModuleType("gi")
    fake_gi.require_version = lambda namespace, version: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "gi", fake_gi)

    class FakeIcon:
        pass

    FakeIcon.__module__ = icon_module
    fake_pystray = types.ModuleType("pystray")
    fake_pystray.Icon = FakeIcon  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pystray", fake_pystray)


def test_tray_backend_ok_with_a_fake_appindicator_pystray(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_pystray(monkeypatch, "pystray._appindicator")

    assert check_tray_backend().status is CheckStatus.OK


def test_tray_backend_fail_with_a_fake_xorg_pystray(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_pystray(monkeypatch, "pystray._xorg")

    result = check_tray_backend()

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "python-gobject" in result.fix


def test_tray_failures_warn_without_a_provider_instead_of_claiming_ok() -> None:
    """No provider must not read as verification; it says the state is unknown."""
    result = check_tray_failures(failure_report=None)

    assert result.status is not CheckStatus.OK
    assert result.status is CheckStatus.WARN
    assert "cannot be determined" in result.summary
    assert result.fix is not None and "ges2t run" in result.fix


def test_tray_failures_ok_when_the_tray_worked() -> None:
    assert check_tray_failures(failure_report=lambda: None).status is CheckStatus.OK


def test_tray_failures_fail_with_the_recorded_summary() -> None:
    report = TrayFailure(count=3, last_message="state update: no display")

    result = check_tray_failures(failure_report=lambda: report)

    assert result.status is CheckStatus.FAIL
    assert result.summary == report.summary()
    assert result.fix is not None


# -- service socket --------------------------------------------------------


def test_service_socket_ok_when_absent(tmp_path: Path) -> None:
    result = check_service_socket(path=tmp_path / "missing.sock", listening=lambda path: False)

    assert result.status is CheckStatus.OK
    assert "not running" in result.summary


def test_service_socket_ok_when_listening(tmp_path: Path) -> None:
    live = tmp_path / "live.sock"
    live.write_bytes(b"")

    result = check_service_socket(path=live, listening=lambda path: True)

    assert result.status is CheckStatus.OK
    assert "listening" in result.summary


def test_service_socket_fail_when_stale(tmp_path: Path) -> None:
    stale = tmp_path / "stale.sock"
    stale.write_bytes(b"")

    result = check_service_socket(path=stale, listening=lambda path: False)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "ges2t run" in result.fix


# -- keyboard layout and clipboard -----------------------------------------


def test_keyboard_layout_ok_when_configured() -> None:
    resolved = ResolvedLayout(
        source=LayoutSource.CONFIGURED,
        layout="hu",
        variant="",
        display_name=None,
        keymap_text=None,
    )

    result = check_keyboard_layout(
        layout="hu", resolver=lambda layout, variant: resolved, compiler=lambda r: None
    )

    assert result.status is CheckStatus.OK
    assert "configuration" in result.summary


def test_keyboard_layout_ok_when_detected() -> None:
    resolved = ResolvedLayout(
        source=LayoutSource.DETECTED,
        layout="hu",
        variant="",
        display_name="Hungarian",
        keymap_text=None,
    )

    result = check_keyboard_layout(resolver=lambda l, v: resolved, compiler=lambda r: None)

    assert result.status is CheckStatus.OK
    assert "detection" in result.summary


def test_keyboard_layout_ok_resolves_the_real_live_keymap() -> None:
    # Exercises the real compiler: libxkbcommon must load and the XKB data compile.
    result = check_keyboard_layout(layout="us", variant="")

    assert result.status is CheckStatus.OK
    assert "compiled" in result.summary


def test_keyboard_layout_fail_when_unresolvable() -> None:
    def resolver(layout: str, variant: str) -> ResolvedLayout:
        raise LayoutResolutionError("no keymap and no layout service")

    result = check_keyboard_layout(layout=AUTO_LAYOUT, resolver=resolver)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "layout" in result.fix


def test_keyboard_layout_warns_instead_of_failing_when_not_required() -> None:
    def resolver(layout: str, variant: str) -> ResolvedLayout:
        raise LayoutResolutionError("no keymap")

    result = check_keyboard_layout(resolver=resolver, required=False)

    assert result.status is CheckStatus.WARN
    assert result.fix is not None


def test_keyboard_layout_fail_when_the_keymap_will_not_compile() -> None:
    def compiler(resolved: ResolvedLayout) -> None:
        raise XkbKeymapCompileError("libxkbcommon refuses it")

    result = check_keyboard_layout(
        resolver=lambda l, v: _live_layout(), compiler=compiler, required=True
    )

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "libxkbcommon" in result.fix


def test_keyboard_layout_warns_on_compile_failure_when_not_required() -> None:
    def compiler(resolved: ResolvedLayout) -> None:
        raise XkbKeymapCompileError("nope")

    result = check_keyboard_layout(
        resolver=lambda l, v: _live_layout(), compiler=compiler, required=False
    )

    assert result.status is CheckStatus.WARN


def test_clipboard_backend_ok_when_not_selected() -> None:
    result = check_clipboard_backend(backend="ydotool", lookup=lambda name: None)

    assert result.status is CheckStatus.OK
    assert "not required" in result.summary


def test_clipboard_backend_ok_when_wl_copy_exists() -> None:
    result = check_clipboard_backend(backend="clipboard", lookup=lambda name: "/usr/bin/wl-copy")

    assert result.status is CheckStatus.OK


def test_clipboard_backend_fail_when_wl_copy_is_missing() -> None:
    result = check_clipboard_backend(backend="clipboard", lookup=lambda name: None)

    assert result.status is CheckStatus.FAIL
    assert result.fix is not None and "wl-clipboard" in result.fix


# -- hotkey bindings -------------------------------------------------------


def test_hotkey_bindings_always_warns_with_the_system_settings_path() -> None:
    result = check_hotkey_bindings()

    assert result.status is CheckStatus.WARN
    assert result.fix is not None and "System Settings" in result.fix


# -- configuration diagnosis -----------------------------------------------


def test_config_valid_ok_when_the_loader_succeeds() -> None:
    diagnosis = diagnose_config(
        loader=lambda: ConfigLoadResult(config=default_config(), warnings=[])
    )

    assert diagnosis.validity.status is CheckStatus.OK
    assert diagnosis.generic_warnings == ()


def test_config_valid_fail_reports_a_config_error_with_a_fix() -> None:
    def loader() -> ConfigLoadResult:
        raise ConfigError("/etc/ges2t/config.toml: key 'audio.device': expected str, got int")

    diagnosis = diagnose_config(loader=loader)

    assert diagnosis.validity.status is CheckStatus.FAIL
    assert "audio.device" in diagnosis.validity.summary
    assert "invalid configuration" in diagnosis.validity.summary
    assert "crashed" not in diagnosis.validity.summary
    assert diagnosis.validity.fix is not None


def test_config_valid_turns_an_unexpected_loader_crash_into_a_fail() -> None:
    def loader() -> ConfigLoadResult:
        raise RuntimeError("probe exploded")

    diagnosis = diagnose_config(loader=loader)

    assert diagnosis.validity.status is CheckStatus.FAIL
    assert "crashed" in diagnosis.validity.summary


def test_engine_config_ok_and_collects_unknown_key_warnings() -> None:
    config = Config(engine_blocks={"whistle": {"chunk_seconds": 1.0, "bogus": 1}})

    diagnosis = diagnose_engine_config(config)

    assert diagnosis.validity.status is CheckStatus.OK
    assert any("bogus" in warning for warning in diagnosis.warnings)


def test_engine_config_fail_catches_whistle_config_error_with_block_and_key() -> None:
    config = Config(engine_blocks={"whistle": {"chunk_seconds": 0.01}})

    diagnosis = diagnose_engine_config(config)

    assert diagnosis.validity.status is CheckStatus.FAIL
    assert "engine.whistle.chunk_seconds" in diagnosis.validity.summary
    assert "crashed" not in diagnosis.validity.summary
    assert diagnosis.validity.fix is not None and "engine.whistle" in diagnosis.validity.fix


def test_injection_config_ok_for_ydotool() -> None:
    config = Config(injection_blocks={"ydotool": {"layout": "auto"}})

    diagnosis = diagnose_injection_config(config)

    assert diagnosis.validity.status is CheckStatus.OK


def test_injection_config_fail_catches_injection_config_error() -> None:
    config = Config(injection_blocks={"ydotool": {"key_delay_ms": -5}})

    diagnosis = diagnose_injection_config(config)

    assert diagnosis.validity.status is CheckStatus.FAIL
    assert "injection.ydotool.key_delay_ms" in diagnosis.validity.summary
    assert "crashed" not in diagnosis.validity.summary
    assert diagnosis.validity.fix is not None and "injection.ydotool" in diagnosis.validity.fix


def test_injection_config_warns_on_unknown_clipboard_key() -> None:
    config = Config(
        injection=default_config().injection.__class__(backend="clipboard"),
        injection_blocks={"clipboard": {"paste_keys": ["ctrl+v"], "junk": 1}},
    )

    diagnosis = diagnose_injection_config(config)

    assert diagnosis.validity.status is CheckStatus.OK
    assert any("junk" in warning for warning in diagnosis.warnings)


# -- report assembly and exit codes ----------------------------------------


def test_build_report_runs_every_check_and_merges_block_warnings(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[engine.whistle]\nbogus_key = 1\n")
    config = Config(engine_blocks={"whistle": {"bogus_key": 1}})

    report = build_report(
        config, DoctorOptions(config_path=config_path, probes=_PASSING_PROBES)
    )

    names = [result.name for result in report.results]
    assert "Config valid" in names
    assert "Keyboard layout" in names
    assert "Hotkey bindings" in names
    warnings_line = next(r for r in report.results if r.name == "Config warnings")
    assert warnings_line.status is CheckStatus.WARN
    assert "bogus_key" in warnings_line.summary


def test_build_report_reports_a_generic_config_error_as_a_line(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[audio]\ndevice = 123\n")

    report = build_report(
        default_config(), DoctorOptions(config_path=config_path, probes=_PASSING_PROBES)
    )

    config_line = next(r for r in report.results if r.name == "Config valid")
    assert config_line.status is CheckStatus.FAIL
    assert "device" in config_line.summary
    assert config_line.fix is not None


def test_run_doctor_returns_zero_with_only_a_warning(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_model_dir(monkeypatch, tmp_path)
    options = DoctorOptions(
        config_path=tmp_path / "missing.toml", probes=_PASSING_PROBES
    )

    code = run_doctor(default_config(), options)

    out = capsys.readouterr().out
    assert code == EXIT_SUCCESS
    assert "warning(s)" in out
    assert "0 failure(s)" in out
    assert "Failures (listed again" not in out


def test_run_doctor_returns_nonzero_and_lists_every_failure_with_a_fix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _stub_model_dir(monkeypatch, tmp_path)
    options = DoctorOptions(
        config_path=tmp_path / "missing.toml",
        probes=replace(_PASSING_PROBES, ydotoold_probe=lambda path: False),
    )

    code = run_doctor(default_config(), options)

    out = capsys.readouterr().out
    assert code == EXIT_FAILURE and code != 0
    assert "Failures (listed again with their fixes):" in out
    assert "systemctl --user enable --now ydotool" in out
    for line in out.splitlines():
        if line.strip().startswith("fix:"):
            assert line.strip() != "fix:"


def test_run_doctor_every_failure_result_has_a_fix_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _stub_model_dir(monkeypatch, tmp_path)
    options = DoctorOptions(
        config_path=tmp_path / "missing.toml",
        probes=replace(
            _PASSING_PROBES,
            ydotoold_probe=lambda path: False,
            uinput_probe=lambda path: False,
            session_probe=lambda: False,
        ),
    )

    report = build_report(default_config(), options)

    assert report.failures, "the forced-failure probes should produce failures"
    for failure in report.failures:
        assert failure.fix is not None and failure.fix.strip() != ""


def test_render_report_relists_failures_and_prints_the_bindings() -> None:
    report = DoctorReport(
        results=(
            CheckResult("Model present", CheckStatus.FAIL, "missing", fix="run 'ges2t setup'"),
            CheckResult("Hotkey bindings", CheckStatus.WARN, "bind it", fix="do it"),
        )
    )

    rendered = render_report(report)

    assert "Failures (listed again with their fixes):" in rendered
    assert "run 'ges2t setup'" in rendered
    assert "Recommended KDE key bindings" in rendered
    assert "ges2t toggle" in rendered


def test_run_doctor_performs_no_network_io(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def forbidden(*args: object, **kwargs: object) -> object:
        raise AssertionError("doctor must not perform network I/O")

    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    options = DoctorOptions(
        config_path=tmp_path / "missing.toml", probes=_PASSING_PROBES
    )

    # The pre-existing checks warn/fail on the tmp data dir, but nothing may reach
    # the network; an AssertionError would escape and fail the test.
    run_doctor(default_config(), options)


def test_doctor_socket_probes_are_unix_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    families: list[int] = []
    real_socket = socket.socket

    def guarded(family: int = socket.AF_UNIX, *args: object, **kwargs: object) -> socket.socket:
        families.append(family)
        assert family == socket.AF_UNIX, f"doctor opened a non-Unix socket: {family}"
        return real_socket(family, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(doctor.socket, "socket", guarded)
    stale = tmp_path / "stale.sock"
    stale.write_bytes(b"")

    check_ydotoold(socket_path=tmp_path / "absent.sock", probe=doctor._ydotoold_reachable)
    check_service_socket(path=stale, listening=doctor.is_service_listening)

    assert families, "the socket probes should have created a socket"
    assert all(family == socket.AF_UNIX for family in families)


def test_importing_doctor_pulls_in_no_gui_or_backend() -> None:
    script = (
        "import sys\n"
        "import ges2t.doctor\n"
        "banned = ['gi', 'pystray', 'needle', 'PIL']\n"
        "print('\\n'.join(name for name in banned if name in sys.modules))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True
    )

    assert completed.returncode == 0, completed.stderr
    assert [line for line in completed.stdout.splitlines() if line] == []


# -- CLI wiring ------------------------------------------------------------


def test_doctor_cli_passes_the_path_and_verify_flag_to_run_doctor(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_run_doctor(config: Config, options: DoctorOptions) -> int:
        captured["config"] = config
        captured["path"] = options.config_path
        captured["verify"] = options.verify
        return 0

    monkeypatch.setattr(doctor, "run_doctor", fake_run_doctor)
    config_path = tmp_path / "config.toml"

    code = cli.main(["doctor", "--verify", "--config", str(config_path)])

    assert code == 0
    assert captured["path"] == config_path
    assert captured["verify"] is True


def test_doctor_cli_does_not_pre_empt_a_generic_config_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An invalid config must still reach doctor, which reports it as a line."""
    called: dict[str, object] = {}

    def fake_run_doctor(config: Config, options: DoctorOptions) -> int:
        called["path"] = options.config_path
        return 7

    monkeypatch.setattr(doctor, "run_doctor", fake_run_doctor)
    bad = tmp_path / "bad.toml"
    bad.write_text("[audio]\ndevice = 123\n")

    code = cli.main(["doctor", "--config", str(bad)])

    assert code == 7
    assert called["path"] == bad


def test_doctor_cli_reports_a_missing_module_cleanly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    real_import = importlib.import_module

    def fake_import(name: str, package: str | None = None) -> types.ModuleType:
        if name == "ges2t.doctor":
            raise ModuleNotFoundError("No module named 'ges2t.doctor'")
        return real_import(name, package)

    monkeypatch.setattr(importlib, "import_module", fake_import)

    assert cli.main(["doctor"]) == EXIT_FAILURE
    assert "ges2t.doctor" in capsys.readouterr().err


def test_doctor_parser_accepts_verify() -> None:
    args = cli.build_parser().parse_args(["doctor", "--verify"])

    assert args.verify is True


def test_doctor_with_a_genuinely_missing_module_prints_the_curated_message() -> None:
    """A fresh interpreter whose ges2t.doctor is unimportable must name it.

    The in-process test above passes even with the buggy ordering, because the
    test module has already imported ges2t.doctor; only a fresh interpreter with
    the module blocked exercises the real path (review 002, finding 4).
    """
    script = (
        "import sys\n"
        "class BlockDoctor:\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name == 'ges2t.doctor':\n"
        "            raise ImportError('blocked for the test')\n"
        "        return None\n"
        "sys.meta_path.insert(0, BlockDoctor())\n"
        "from ges2t import cli\n"
        "raise SystemExit(cli.main(['doctor']))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60
    )

    assert completed.returncode == 1
    assert "ges2t.doctor.run_doctor" in completed.stderr
    assert "not available in this build" in completed.stderr
    assert "unexpected failure" not in completed.stderr
