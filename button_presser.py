#!/usr/bin/env python3
"""
Button Presser for Windows
==================================================

A small desktop utility that presses Scroll Lock repeatedly with a random
delay between presses, chosen uniformly between a "min" and "max" number
of seconds set with a two-handle (dual-thumb) slider. Intended to keep a
Windows session from going idle/locking by generating normal, standard
OS-level input activity -- not a mouse automation tool, and not disguised as
anything other than what it is.

The key press itself is sent via the raw Windows SendInput API (ctypes),
building the exact scan code Windows expects. Scroll Lock is a good
choice for this: pressing it toggles a real, OS-tracked state (visible as
the "SCRL" indicator in Excel's status bar, or the Scroll Lock LED on
keyboards that have one), so it's easy to visually confirm the presses
are actually registering at the OS level. The global F6 hotkey listener
still uses the `keyboard` library, which is fine for that since it isn't
the part that needs to be hardware-accurate.

Requirements (install before running or building):
    pip install keyboard pystray pillow

Build a standalone .exe (on Windows) with PyInstaller:
    pip install pyinstaller
    pyinstaller --onefile --windowed --name Button-Presser-for-Windows button_presser.py

    The resulting exe will be in the "dist" folder.
    (--windowed hides the console window; drop --onefile if you'd rather
    have a folder of files instead of a single exe.)

    Note: simulating input system-wide on Windows generally requires the
    the app to run with the same or higher privilege level as whatever
    window has focus. If key presses don't seem to register, try running
    the exe as Administrator.

Controls:
    - Set Min / Max seconds with the dual slider (drag either handle).
    - Scroll Lock is the key pressed by default.
    - Press "Start" or hit F6 (global hotkey, works even when minimized
      to the tray) to toggle on/off.
    - Closing the window (the X button) minimizes it to the system tray
      instead of quitting -- right-click the tray icon to Show or Quit.
"""

import ctypes
import logging
import math
import random
import sys
import threading
import time

import tkinter as tk
from tkinter import ttk

import keyboard
import pystray
from PIL import Image, ImageDraw

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Low-level SendInput key press (ctypes) -- used instead of the `keyboard`
# library's press_and_release() for the simulated key itself, because
# `keyboard` doesn't reliably set Windows' "extended key" scan-code flag.
# That flag is what tells Windows (and things hooked into it, like the
# Ease of Access "press CTRL to locate pointer" feature) that a key is the
# the right-hand version of a key rather than the left-hand one -- real
# hardware sets it automatically, injected input has to set it explicitly.
# ---------------------------------------------------------------------------
user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None

INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MAPVK_VK_TO_VSC = 0
KEY_HOLD_SECONDS = 0.03
SLIDER_PADDING = 18

# Virtual-key code and "is this the extended/right-hand variant" flag,
# for the keys people commonly use for this kind of keep-awake tool.
VK_MAP = {
    "right ctrl": (0xA3, True),
    "left ctrl": (0xA2, False),
    "right shift": (0xA1, False),
    "left shift": (0xA0, False),
    "right alt": (0xA5, True),
    "left alt": (0xA4, False),
    "tab": (0x09, False),
    "caps lock": (0x14, False),
    "scroll lock": (0x91, False),
    "num lock": (0x90, True),
    "insert": (0x2D, True),
    "delete": (0x2E, True),
    "home": (0x24, True),
    "end": (0x23, True),
    "page up": (0x21, True),
    "page down": (0x22, True),
    "up": (0x26, True),
    "down": (0x28, True),
    "left": (0x25, True),
    "right": (0x27, True),
    "f13": (0x7C, False), "f14": (0x7D, False), "f15": (0x7E, False),
    "f16": (0x7F, False), "f17": (0x80, False), "f18": (0x81, False),
    "f19": (0x82, False), "f20": (0x83, False), "f21": (0x84, False),
    "f22": (0x85, False), "f23": (0x86, False), "f24": (0x87, False),
}

# The lock keys are included so users can intentionally toggle their indicators.
KEY_OPTIONS = tuple(key.title() for key in VK_MAP)


class _KeyBdInput(ctypes.Structure):
    _fields_ = [("wVk", ctypes.c_ushort), ("wScan", ctypes.c_ushort),
                ("dwFlags", ctypes.c_ulong), ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _MouseInput(ctypes.Structure):
    # Unused, but Windows' real INPUT union includes this -- it's larger
    # than KEYBDINPUT, so the union (and therefore the whole INPUT struct)
    # must include it for ctypes to compute the size SendInput expects.
    # Without it, SendInput rejects every call with ERROR_INVALID_PARAMETER.
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _HardwareInput(ctypes.Structure):
    _fields_ = [("uMsg", ctypes.c_ulong), ("wParamL", ctypes.c_short),
                ("wParamH", ctypes.c_ushort)]


class _Input_I(ctypes.Union):
    _fields_ = [("ki", _KeyBdInput), ("mi", _MouseInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", ctypes.c_ulong), ("ii", _Input_I)]


def _send_key_event(vk: int, extended: bool, key_up: bool) -> None:
    if user32 is None:
        raise OSError("SendInput is only available on Windows")
    # VK-code based method (no KEYEVENTF_SCANCODE) -- the standard, reliable
    # way to toggle lock keys like Scroll Lock programmatically. The
    # scan-code method can be silently ignored by the toggle-state logic
    # on some systems even though SendInput reports success.
    flags = (KEYEVENTF_EXTENDEDKEY if extended else 0) | (KEYEVENTF_KEYUP if key_up else 0)
    scan = user32.MapVirtualKeyW(vk, MAPVK_VK_TO_VSC)
    if not scan:
        err = ctypes.get_last_error()
        raise OSError(f"MapVirtualKeyW failed for virtual key {vk} (GetLastError={err})")
    extra = ctypes.c_ulong(0)
    inp = _Input(INPUT_KEYBOARD, _Input_I(ki=_KeyBdInput(vk, scan, flags, 0,
                                                         ctypes.pointer(extra))))
    ctypes.set_last_error(0)
    sent = user32.SendInput(1, ctypes.pointer(inp), ctypes.sizeof(inp))
    if sent != 1:
        err = ctypes.get_last_error()
        raise OSError(f"SendInput failed (sent={sent}, GetLastError={err}: "
                      f"{ctypes.WinError(err).strerror if err else 'no error code'})")


def press_key(name: str) -> None:
    """Press and release a mapped key using ``SendInput``."""
    try:
        vk, extended = VK_MAP[name.strip().lower()]
    except (AttributeError, KeyError) as exc:
        raise KeyError(name) from exc
    _send_key_event(vk, extended, key_up=False)
    try:
        time.sleep(KEY_HOLD_SECONDS)
    finally:
        # Do not leave Ctrl/Alt (or another key) stuck if the hold is interrupted.
        _send_key_event(vk, extended, key_up=True)


# ---------------------------------------------------------------------------
# Color palette / style
# ---------------------------------------------------------------------------
BG = "#1e1f29"
PANEL = "#282a3a"
ACCENT = "#7c5cff"
ACCENT_DIM = "#4d3d99"
TEXT = "#e6e6f0"
SUBTEXT = "#9797ab"
GREEN = "#4caf7d"
RED = "#e05c5c"
TRACK = "#3c3e52"

TOGGLE_HOTKEY = "f6"
DEFAULT_KEY = "Scroll Lock"


class DualSlider(tk.Canvas):
    """A two-handle range slider drawn on a Canvas (no external deps)."""

    def __init__(self, parent, from_=0.1, to=10.0, low=0.5, high=2.0,
                 width=360, height=54, on_change=None, **kwargs):
        if not all(isinstance(value, (int, float)) and math.isfinite(value)
                   for value in (from_, to, low, high)):
            raise ValueError("slider values must be finite numbers")
        if from_ >= to:
            raise ValueError("slider 'from_' must be less than 'to'")
        if not from_ <= low <= high <= to:
            raise ValueError("slider values must satisfy from_ <= low <= high <= to")
        if width <= 2 * SLIDER_PADDING:
            raise ValueError("slider width is too small for its padding")
        super().__init__(parent, width=width, height=height,
                         bg=PANEL, highlightthickness=0, **kwargs)
        self.from_ = from_
        self.to = to
        self.low = low
        self.high = high
        self.w = width
        self.h = height
        self.pad = SLIDER_PADDING
        self.on_change = on_change
        self._drag = None  # "low" or "high"

        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda e: setattr(self, "_drag", None))

        self.draw()

    # -- value <-> pixel helpers -------------------------------------------------
    def _val_to_x(self, v):
        frac = (v - self.from_) / (self.to - self.from_)
        return self.pad + frac * (self.w - 2 * self.pad)

    def _x_to_val(self, x):
        frac = (x - self.pad) / (self.w - 2 * self.pad)
        frac = min(max(frac, 0), 1)
        return self.from_ + frac * (self.to - self.from_)

    # -- drawing -------------------------------------------------------------
    def draw(self):
        self.delete("all")
        mid_y = self.h // 2

        # full track
        self.create_line(self.pad, mid_y, self.w - self.pad, mid_y,
                         fill=TRACK, width=6, capstyle=tk.ROUND)

        x1, x2 = self._val_to_x(self.low), self._val_to_x(self.high)
        # active range highlight
        self.create_line(x1, mid_y, x2, mid_y, fill=ACCENT, width=6,
                         capstyle=tk.ROUND)

        r = 9
        self.create_oval(x1 - r, mid_y - r, x1 + r, mid_y + r,
                         fill=TEXT, outline=ACCENT, width=2, tags="low")
        self.create_oval(x2 - r, mid_y - r, x2 + r, mid_y + r,
                         fill=TEXT, outline=ACCENT, width=2, tags="high")

        self.create_text(x1, mid_y - 20, text=f"{self.low:.2f}s",
                         fill=TEXT, font=("Segoe UI", 9, "bold"))
        self.create_text(x2, mid_y - 20, text=f"{self.high:.2f}s",
                         fill=TEXT, font=("Segoe UI", 9, "bold"))

    # -- interaction -----------------------------------------------------------
    def _on_press(self, event):
        x_low = self._val_to_x(self.low)
        x_high = self._val_to_x(self.high)
        # grab whichever handle is closer
        self._drag = "low" if abs(event.x - x_low) <= abs(event.x - x_high) else "high"
        self._on_drag(event)

    def _on_drag(self, event):
        if not self._drag:
            return
        val = self._x_to_val(event.x)
        if self._drag == "low":
            self.low = min(val, self.high)
        else:
            self.high = max(val, self.low)
        self.draw()
        if self.on_change:
            self.on_change(self.low, self.high)

    def get(self):
        return self.low, self.high


def make_tray_image():
    """Generate a simple circular icon in-memory (no icon file needed)."""
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((4, 4, size - 4, size - 4), fill=(124, 92, 255, 255))
    d.ellipse((size // 2 - 8, size // 2 - 8, size // 2 + 8, size // 2 + 8),
              fill=(255, 255, 255, 255))
    return img


class KeyPresserApp:
    def __init__(self, root):
        self.root = root
        self.running = False
        self.press_count = 0
        self.tray_icon = None
        self.stop_event = threading.Event()
        self._worker = None
        self._hotkey_registered = False
        self._count_lock = threading.Lock()

        self._build_ui()
        self._register_hotkey()
        self.root.protocol("WM_DELETE_WINDOW", self.minimize_to_tray)

    # -- UI ---------------------------------------------------------------
    def _build_ui(self):
        r = self.root
        r.title("Button Presser for Windows")
        r.configure(bg=BG)
        r.geometry("400x430")
        r.resizable(False, False)

        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", background=ACCENT, foreground=TEXT,
                        font=("Segoe UI", 10, "bold"), borderwidth=0,
                        padding=8)
        style.map("TButton", background=[("active", ACCENT_DIM)])
        style.configure("TEntry", fieldbackground=PANEL, foreground=TEXT,
                        insertcolor=TEXT, borderwidth=0)

        header = tk.Frame(r, bg=BG)
        header.pack(fill="x", pady=(18, 6), padx=20)
        tk.Label(header, text="Button Presser for Windows", bg=BG, fg=TEXT,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(header, text="Scroll Lock, pressed at a random interval",
                 bg=BG, fg=SUBTEXT, font=("Segoe UI", 9)).pack(anchor="w")

        # slider panel
        panel = tk.Frame(r, bg=PANEL, bd=0)
        panel.pack(fill="x", padx=20, pady=14)
        tk.Label(panel, text="Interval range (seconds)", bg=PANEL, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(12, 0))
        self.slider = DualSlider(panel, from_=0.05, to=10.0, low=0.5, high=2.0,
                                 width=340, height=60)
        self.slider.pack(padx=14, pady=(4, 14))

        # Key selection. Lock keys also toggle their corresponding keyboard
        # indicators: Caps Lock, Num Lock, and Scroll Lock.
        key_panel = tk.Frame(r, bg=BG)
        key_panel.pack(fill="x", padx=20, pady=(0, 10))
        tk.Label(key_panel, text="Key:", bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(side="left")
        self.key_var = tk.StringVar(value=DEFAULT_KEY)
        self.key_dropdown = ttk.Combobox(
            key_panel,
            textvariable=self.key_var,
            values=KEY_OPTIONS,
            state="readonly",
            width=18,
        )
        self.key_dropdown.pack(side="left", padx=8)

        # status
        self.status_var = tk.StringVar(value="Stopped")
        self.count_var = tk.StringVar(value="Presses: 0")
        status_frame = tk.Frame(r, bg=BG)
        status_frame.pack(fill="x", padx=20, pady=(4, 6))
        self.status_label = tk.Label(status_frame, textvariable=self.status_var,
                                     bg=BG, fg=RED, font=("Segoe UI", 11, "bold"))
        self.status_label.pack(side="left")
        tk.Label(status_frame, textvariable=self.count_var, bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 9)).pack(side="right")

        # start/stop button
        self.toggle_btn = ttk.Button(r, text=f"Start ({TOGGLE_HOTKEY.upper()})",
                                     command=self.toggle)
        self.toggle_btn.pack(fill="x", padx=20, pady=(8, 4))

        tk.Label(r, text=f"Global hotkey: {TOGGLE_HOTKEY.upper()}  •  closing this window "
                         f"sends it to the tray", bg=BG, fg=SUBTEXT,
                 font=("Segoe UI", 8)).pack(pady=(10, 0))

        tray_btn = tk.Label(r, text="Minimize to tray", bg=BG, fg=ACCENT,
                            font=("Segoe UI", 9, "underline"), cursor="hand2")
        tray_btn.pack(pady=(14, 0))
        tray_btn.bind("<Button-1>", lambda e: self.minimize_to_tray())

    # -- press logic -----------------------------------------------------
    def toggle(self):
        if self.running:
            self.stop()
        else:
            self.start()

    def start(self):
        if self.running:
            return
        try:
            key = self.key_var.get().strip().lower()
        except AttributeError:
            key = ""
        if not key:
            self.status_var.set("Set a key first")
            self.status_label.configure(fg=RED)
            return
        if key not in VK_MAP:
            self.status_var.set("Unknown key name")
            self.status_label.configure(fg=RED)
            return
        self.running = True
        self.stop_event = threading.Event()
        worker_event = self.stop_event
        self.status_var.set("Running")
        self.status_label.configure(fg=GREEN)
        self.toggle_btn.configure(text=f"Stop ({TOGGLE_HOTKEY.upper()})")
        self._worker = threading.Thread(target=self._press_loop,
                                        args=(key, worker_event), daemon=True)
        try:
            self._worker.start()
        except RuntimeError as exc:
            self.running = False
            worker_event.set()
            self.status_var.set(f"Unable to start worker: {str(exc)[:35]}")
            self.status_label.configure(fg=RED)
            self.toggle_btn.configure(text=f"Start ({TOGGLE_HOTKEY.upper()})")

    def stop(self):
        self.running = False
        self.stop_event.set()
        self.status_var.set("Stopped")
        self.status_label.configure(fg=RED)
        self.toggle_btn.configure(text=f"Start ({TOGGLE_HOTKEY.upper()})")

    def _press_loop(self, key, stop_event=None):
        """Run one worker generation; the event prevents stale workers reviving."""
        stop_event = stop_event or self.stop_event
        while not stop_event.is_set():
            try:
                low, high = self.slider.get()
                delay = random.uniform(low, high)
                if stop_event.wait(delay):
                    break
                press_key(key)
            except (KeyError, OSError) as e:
                msg = "Unknown key name" if isinstance(e, KeyError) else str(e)
                self.root.after(0, self._worker_failed, msg[:60], stop_event)
                return
            except Exception as exc:  # never let an unexpected worker error be silent
                self.root.after(0, self._worker_failed,
                                f"Worker error: {type(exc).__name__}", stop_event)
                return
            with self._count_lock:
                self.press_count += 1
                count = self.press_count
            self.root.after(0, self.count_var.set, f"Presses: {count}")

    def _worker_failed(self, message, stop_event):
        if stop_event is not self.stop_event:
            return
        self.running = False
        stop_event.set()
        self.status_var.set(message or "Key press failed")
        self.status_label.configure(fg=RED)
        self.toggle_btn.configure(text=f"Start ({TOGGLE_HOTKEY.upper()})")

    # -- hotkey ---------------------------------------------------------------
    def _register_hotkey(self):
        # runs in keyboard's own listener thread; safe to call toggle()
        # since toggle() only touches tk state via simple sets
        try:
            keyboard.add_hotkey(TOGGLE_HOTKEY, lambda: self.root.after(0, self.toggle))
        except Exception as exc:
            self.status_var.set(f"Hotkey unavailable: {str(exc)[:35]}")
            self.status_label.configure(fg=RED)
            return
        self._hotkey_registered = True

    # -- tray -----------------------------------------------------------------
    def minimize_to_tray(self):
        if self.tray_icon:
            self.root.withdraw()
            return
        try:
            menu = pystray.Menu(
                pystray.MenuItem("Show", self._show_from_tray, default=True),
                pystray.MenuItem("Start/Stop", lambda: self.root.after(0, self.toggle)),
                pystray.MenuItem("Quit", self._quit),
            )
            self.tray_icon = pystray.Icon(
                "button_presser_for_windows",
                make_tray_image(),
                "Button Presser for Windows",
                menu,
            )
            threading.Thread(target=self.tray_icon.run, daemon=True).start()
        except Exception as exc:
            LOGGER.exception("Unable to create the system-tray icon")
            self.status_var.set(f"Tray unavailable: {str(exc)[:35]}")
            self.status_label.configure(fg=RED)
            return
        self.root.withdraw()

    def _show_from_tray(self, icon=None, item=None):
        self.root.after(0, self.root.deiconify)

    def _quit(self, icon=None, item=None):
        self.stop()
        if self.tray_icon:
            self.tray_icon.stop()
        if self._hotkey_registered:
            try:
                keyboard.remove_hotkey(TOGGLE_HOTKEY)
            except Exception:
                LOGGER.exception("Unable to remove the global hotkey")
            self._hotkey_registered = False
        self.root.after(0, self.root.destroy)


def main():
    root = tk.Tk()
    KeyPresserApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
