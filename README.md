# GestureControl

Control your laptop with hand gestures through the webcam. Runs in the
system tray (Windows) or menu bar (macOS) — toggle it on/off from the icon.

Tuned to run smoothly on mid-range hardware such as **HP laptops with an
Intel Core i5** (640×480 capture, MediaPipe lite hand model, frame skipping).

## Gestures

Hold the pose steady in front of the camera:

| Gesture | Hold | Action |
|---|---|---|
| 1 finger | 3 s | Brightness up |
| 2 fingers | 1.5 s | New browser tab (Ctrl+T / Cmd+T) |
| 3 fingers | 1.5 s | New window (Ctrl+N / Cmd+N) |
| 5 fingers (open palm) | 1.5 s | Play / pause (Space) |
| Open palm, then close to a fist | 1.5 s fist | Put the computer to sleep |

## Windows setup (HP / Intel i5)

1. Install **Python 3.11 (64-bit)** from [python.org](https://www.python.org/downloads/windows/)
   — check **"Add python.exe to PATH"** during install.
   (MediaPipe needs 64-bit Python 3.9–3.12; 3.11 is the safe choice.)
2. Open **Command Prompt** in this folder and install dependencies:
   ```
   pip install -r requirements.txt
   ```
3. Run the app:
   ```
   python gesture_control.py
   ```
   A hand icon appears in the system tray (check the hidden-icons arrow ^
   near the clock). Click it and choose **Gesture Control: ON**.
4. If Windows asks for camera permission, allow it
   (Settings → Privacy & security → Camera → let desktop apps access the camera).

### Build a standalone .exe (optional)

```
pip install pyinstaller
pyinstaller GestureControl-windows.spec
```

The exe lands in `dist\GestureControl.exe`. First launch is slower while it
unpacks; that is normal for PyInstaller one-file builds.

### Performance notes for i5 laptops

- The app already uses the MediaPipe **lite** model, 640×480 capture, and
  processes every other frame, so CPU load stays modest.
- Close other camera apps (Teams/Zoom) — only one app can use the webcam.
- Plug in the charger or set Windows power mode to "Balanced/Best
  performance"; "Battery saver" throttles the CPU and makes detection laggy.
- Brightness control uses the laptop's internal display (WMI). On external
  monitors it may not work.
- "Sleep" uses Windows' `SetSuspendState`; if hibernation is enabled on
  your machine it may hibernate instead of sleeping.

## macOS setup

```
pip3 install -r requirements.txt
python3 gesture_control.py
```

Grant Camera and Accessibility permissions when prompted
(System Settings → Privacy & Security). The original macOS-only version is
kept as `gesturecontrolll.py`; `GestureControl.spec` builds the macOS app.

## Files

- `gesture_control.py` — cross-platform app (Windows + macOS)
- `GestureControl-windows.spec` — PyInstaller spec for the Windows .exe
- `gesturecontrolll.py`, `GestureControl.spec` — original macOS version
- `requirements.txt` — dependencies for both platforms
