"""Tests for button_presser.py, including deliberate failure-path checks."""

import importlib.util
import sys
import threading
import types
from pathlib import Path

import pytest


# These tests exercise application logic without starting OS-level keyboard
# hooks or tray services. The real application still imports the documented
# packages normally when launched.
keyboard_stub = types.ModuleType("keyboard")
keyboard_stub.add_hotkey = lambda *args, **kwargs: None
keyboard_stub.remove_hotkey = lambda *args, **kwargs: None
sys.modules["keyboard"] = keyboard_stub

pystray_stub = types.ModuleType("pystray")
pystray_stub.Menu = lambda *args, **kwargs: None
pystray_stub.MenuItem = lambda *args, **kwargs: None
pystray_stub.Icon = object
sys.modules["pystray"] = pystray_stub

if "tkinter" not in sys.modules:
    try:
        import tkinter  # noqa: F401
    except ModuleNotFoundError:
        tkinter_stub = types.ModuleType("tkinter")

        class _CanvasStub:
            pass

        tkinter_stub.Canvas = _CanvasStub
        tkinter_stub.ROUND = "round"
        tkinter_stub.Frame = object
        tkinter_stub.Label = object
        tkinter_stub.StringVar = object
        tkinter_stub_ttk = types.ModuleType("tkinter.ttk")
        tkinter_stub.ttk = tkinter_stub_ttk
        tkinter_stub_ttk.Style = object
        tkinter_stub_ttk.Button = object
        tkinter_stub_ttk.Combobox = object
        sys.modules["tkinter"] = tkinter_stub
        sys.modules["tkinter.ttk"] = tkinter_stub_ttk


MODULE_PATH = Path(__file__).with_name("button_presser.py")
spec = importlib.util.spec_from_file_location("button_presser", MODULE_PATH)
button_presser = importlib.util.module_from_spec(spec)
spec.loader.exec_module(button_presser)


def test_press_key_normalizes_names_and_releases_after_hold(monkeypatch):
    calls = []
    monkeypatch.setattr(button_presser, "_send_key_event",
                        lambda vk, extended, key_up: calls.append((vk, extended, key_up)))
    monkeypatch.setattr(button_presser.time, "sleep", lambda seconds: None)

    button_presser.press_key("  RIGHT CTRL ")

    assert calls == [(0xA3, True, False), (0xA3, True, True)]


def test_press_key_attempts_release_when_hold_sleep_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(button_presser, "_send_key_event",
                        lambda vk, extended, key_up: calls.append((vk, extended, key_up)))

    def fail_sleep(_):
        raise RuntimeError("interrupted")

    monkeypatch.setattr(button_presser.time, "sleep", fail_sleep)

    with pytest.raises(RuntimeError, match="interrupted"):
        button_presser.press_key("scroll lock")

    assert calls == [(0x91, False, False), (0x91, False, True)]


@pytest.mark.parametrize("bad_name", [None, "", "not-a-key"])
def test_press_key_rejects_bad_names_as_key_error(bad_name):
    with pytest.raises(KeyError):
        button_presser.press_key(bad_name)


def test_key_options_include_requested_modifiers_and_lock_keys():
    requested = {
        "right ctrl", "left ctrl", "right shift", "left shift", "tab",
        "caps lock", "num lock", "scroll lock",
    }

    assert requested <= {option.lower() for option in button_presser.KEY_OPTIONS}
    assert button_presser.DEFAULT_KEY in button_presser.KEY_OPTIONS


def test_send_key_event_is_explicitly_unsupported_off_windows(monkeypatch):
    monkeypatch.setattr(button_presser, "user32", None)

    with pytest.raises(OSError, match="only available on Windows"):
        button_presser._send_key_event(0x91, False, False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"from_": 1, "to": 1},
        {"from_": 2, "to": 1},
        {"from_": 0, "to": 1, "low": -0.1},
        {"from_": 0, "to": 1, "high": 2},
        {"from_": 0, "to": 1, "low": 0.8, "high": 0.2},
        {"from_": 0, "to": 1, "low": float("nan")},
        {"from_": 0, "to": 1, "width": 36},
    ],
)
def test_dual_slider_rejects_invalid_configuration(kwargs):
    with pytest.raises(ValueError):
        button_presser.DualSlider(None, **kwargs)


def test_dual_slider_coordinate_conversion_clamps_input():
    slider = button_presser.DualSlider.__new__(button_presser.DualSlider)
    slider.from_, slider.to, slider.w, slider.pad = 0.05, 10.0, 340, 18

    assert slider._x_to_val(-100) == pytest.approx(0.05)
    assert slider._x_to_val(1000) == pytest.approx(10.0)
    assert slider._val_to_x(0.05) == pytest.approx(18)
    assert slider._val_to_x(10.0) == pytest.approx(322)


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class _Widget:
    def __init__(self):
        self.calls = []

    def configure(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class _Root:
    def after(self, _delay, callback, *args):
        callback(*args)


def app_without_gui(key="scroll lock"):
    app = button_presser.KeyPresserApp.__new__(button_presser.KeyPresserApp)
    app.running = False
    app.press_count = 0
    app.stop_event = threading.Event()
    app._worker = None
    app._hotkey_registered = False
    app._count_lock = threading.Lock()
    app.root = _Root()
    app.key_var = _Value(key)
    app.status_var = _Value("Stopped")
    app.count_var = _Value("Presses: 0")
    app.status_label = _Widget()
    app.toggle_btn = _Widget()
    app.slider = type("Slider", (), {"get": lambda self: (0, 0)})()
    return app


def test_start_rejects_unknown_key_before_creating_worker(monkeypatch):
    app = app_without_gui("definitely-not-a-key")
    created = []
    monkeypatch.setattr(button_presser.threading, "Thread",
                        lambda **kwargs: created.append(kwargs))

    app.start()

    assert app.running is False
    assert app.status_var.value == "Unknown key name"
    assert created == []


def test_worker_stops_cleanly_when_event_is_set_before_press(monkeypatch):
    app = app_without_gui()
    event = threading.Event()
    event.set()
    pressed = []
    monkeypatch.setattr(button_presser, "press_key", lambda key: pressed.append(key))

    app._press_loop("scroll lock", event)

    assert pressed == []
    assert app.press_count == 0


def test_worker_reports_unexpected_exception_and_does_not_claim_running(monkeypatch):
    app = app_without_gui()
    app.running = True

    def fail(_):
        raise ValueError("bad fake device")

    monkeypatch.setattr(button_presser, "press_key", fail)
    app._press_loop("scroll lock", app.stop_event)

    assert app.running is False
    assert app.stop_event.is_set()
    assert app.status_var.value == "Worker error: ValueError"
    assert app.toggle_btn.calls


def test_worker_reports_slider_failure_instead_of_dying_silently():
    app = app_without_gui()
    app.running = True
    app.slider = type("BrokenSlider", (), {
        "get": lambda self: (_ for _ in ()).throw(OverflowError("bad range"))
    })()

    app._press_loop("scroll lock", app.stop_event)

    assert app.running is False
    assert app.status_var.value == "Worker error: OverflowError"


def test_stale_worker_failure_cannot_stop_newer_run():
    app = app_without_gui()
    old_event = threading.Event()
    new_event = threading.Event()
    app.stop_event = new_event
    app.running = True

    app._worker_failed("old failure", old_event)

    assert app.running is True
    assert not new_event.is_set()
    assert app.status_var.value == "Stopped"
