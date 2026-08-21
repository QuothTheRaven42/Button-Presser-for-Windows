# Button Presser for Windows

A small Windows desktop utility that presses a selected key at a random interval. It is intended for keeping a Windows session active with ordinary OS-level keyboard input.

## Features

- Selectable keys including left/right Ctrl and Shift, Tab, Caps Lock, Num Lock, Scroll Lock, navigation keys, and function keys.
- Caps Lock, Num Lock, and Scroll Lock can toggle their corresponding keyboard indicators.
- Adjustable minimum and maximum interval from 0.05 to 10 seconds.
- Uniformly random delay between each key press.
- Global F6 start/stop hotkey.
- System-tray support.
- Windows `SendInput` integration with explicit error reporting.
- No external image assets are required for the tray icon.

## Requirements

- Windows 10 or later.
- Python 3.10 or later.
- The dependencies in [`requirements.txt`](requirements.txt).

Install the runtime dependencies from the `Clicker` directory:

```powershell
python -m pip install -r requirements.txt
```

## Run from source

```powershell
python Clicker.py
```

The application starts stopped. Choose a key and interval range, then press **Start** or F6. Closing the window minimizes it to the system tray; use the tray menu to quit.

## Build a Windows executable

Install the development dependencies and run PyInstaller:

```powershell
python -m pip install -r requirements-dev.txt
pyinstaller --onefile --windowed --name Button-Presser-for-Windows Clicker.py
```

The executable will be placed in `dist/`.

Input injection can be affected by Windows privilege boundaries. If input is not received by a focused application, run this utility with the same or greater privileges as that application. Use it only on systems and applications where you are authorized to generate input.

## Test

The tests use fakes for the GUI, keyboard hook, and Windows input boundary, so they can run on non-Windows systems:

```powershell
python -m pip install -r requirements-dev.txt
python -m pytest
```

## Project layout

| File | Purpose |
| --- | --- |
| `Clicker.py` | Application and GUI implementation |
| `test_clicker.py` | Unit and failure-path tests |
| `requirements.txt` | Runtime dependencies |
| `requirements-dev.txt` | Test and build dependencies |
| `pyproject.toml` | Pytest and tool configuration |

## License

MIT License

Copyright (c) 2026 David

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
