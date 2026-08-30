"""GestureControl — cross-platform hand-gesture control (Windows + macOS).

Windows notes (tested profile: HP laptop, Intel Core i5, integrated webcam):
  - Uses DirectShow capture at 640x480 and MediaPipe's lite hand model
    (model_complexity=0) to keep CPU usage low on i5-class machines.
  - Tray icon via pystray, brightness via screen-brightness-control.

Gestures (hold the pose in front of the camera):
  1 finger  (hold 3s)   -> brightness up
  2 fingers (hold 1.5s) -> new browser tab   (Ctrl+T / Cmd+T)
  3 fingers (hold 1.5s) -> new window        (Ctrl+N / Cmd+N)
  5 fingers (hold 1.5s) -> play/pause        (Space)
  open palm then fist (hold 1.5s) -> put the computer to sleep
"""

import json
import os
import platform
import subprocess
import sys
import threading
import time

# Quiet harmless TensorFlow Lite / MediaPipe startup log noise
# (must be set before cv2/mediapipe are imported).
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")
os.environ.setdefault("GLOG_minloglevel", "2")

import cv2
import mediapipe as mp
import pyautogui

# Keyboard-only automation; the corner "failsafe" would otherwise kill the
# engine thread whenever the mouse sits in a screen corner as a gesture fires.
pyautogui.FAILSAFE = False

IS_WINDOWS = platform.system() == "Windows"
IS_MAC = platform.system() == "Darwin"

# ===============================
# PERSISTENT STATE
# ===============================
if IS_WINDOWS:
    APP_DIR = os.path.join(os.getenv("APPDATA", os.path.expanduser("~")), "GestureControl")
elif IS_MAC:
    APP_DIR = os.path.expanduser("~/Library/Application Support/GestureControl")
else:
    APP_DIR = os.path.expanduser("~/.config/GestureControl")
STATE_FILE = os.path.join(APP_DIR, "state.json")


def load_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f).get("enabled", False)
    except Exception:
        return False


def save_state(enabled):
    os.makedirs(APP_DIR, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump({"enabled": enabled}, f)


# ===============================
# SYSTEM ACTIONS
# ===============================
def brightness_up():
    if IS_WINDOWS:
        try:
            import screen_brightness_control as sbc
            sbc.set_brightness("+10")
        except Exception:
            pass
    elif IS_MAC:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to key code 145'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def system_sleep():
    if IS_WINDOWS:
        # Note: if hibernation is enabled on the machine this hibernates
        # instead of sleeping (Windows rundll32 limitation).
        subprocess.run(
            ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    elif IS_MAC:
        subprocess.run(
            ["osascript", "-e", 'tell application "System Events" to sleep'],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


HOTKEY_MOD = "command" if IS_MAC else "ctrl"


def safe(fn, *args):
    """Run an action without letting a failure kill the engine thread."""
    try:
        fn(*args)
    except Exception:
        pass


# ===============================
# FINGER COUNT
# ===============================
def count_fingers(hand):
    fingers = []
    for tip in [8, 12, 16, 20]:
        fingers.append(hand.landmark[tip].y < hand.landmark[tip - 2].y)
    return 5 if fingers.count(True) == 4 else fingers.count(True)


# ===============================
# GESTURE ENGINE (CAMERA ONCE)
# ===============================
class GestureEngine(threading.Thread):
    def __init__(self, preview=False):
        super().__init__(daemon=True)
        self.enabled = load_state()
        self.running = True
        self.preview = preview
        # Some Windows webcams deliver black frames on one backend but work
        # on another; we rotate through these if that happens.
        self._win_backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY]
        self._backend_i = 0

    def _open_camera(self):
        if IS_WINDOWS:
            backend = self._win_backends[self._backend_i % len(self._win_backends)]
            cap = cv2.VideoCapture(0, backend)
        elif IS_MAC:
            cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
        else:
            cap = cv2.VideoCapture(0)
        # Modest resolution keeps CPU low on i5-class laptops.
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        cap.set(cv2.CAP_PROP_FPS, 30)
        return cap

    def _show_preview(self, frame, hand, fingers, hold_start, now, action):
        if hand is not None:
            mp.solutions.drawing_utils.draw_landmarks(
                frame, hand, mp.solutions.hands.HAND_CONNECTIONS
            )
            cv2.putText(frame, f"Fingers: {fingers}", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
            if hold_start is not None:
                cv2.putText(frame, f"Hold: {now - hold_start:.1f}s", (10, 60),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
        else:
            cv2.putText(frame, "No hand detected", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
        if action:
            cv2.putText(frame, action, (10, 90),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 0, 255), 2)
        cv2.imshow("GestureControl preview (press Q to close)", frame)
        if (cv2.waitKey(1) & 0xFF) in (ord("q"), ord("Q")):
            self.preview = False
            cv2.destroyAllWindows()

    def run(self):
        hands = mp.solutions.hands.Hands(
            max_num_hands=1,
            model_complexity=0,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )

        HOLD_SHORT = 1.5
        HOLD_LONG = 3.0
        COOLDOWN = 1.5
        PROCESS_EVERY_N = 2  # skip every other frame to save CPU
        MAX_READ_FAILS = 30  # reopen the camera after this many bad reads

        DARK_FRAME_LIMIT = 75  # ~5s of black frames -> try another backend

        hold_start = None
        last_action = 0
        sleep_state = "WAIT_OPEN"
        fist_start = None
        frame_i = 0
        cap = None
        read_fails = 0
        dark_frames = 0
        fired_label = ""
        fired_at = 0.0

        def fire(label):
            nonlocal fired_label, fired_at
            fired_label, fired_at = label, time.time()
            print(f"[GestureControl] {label}", flush=True)

        def drop_camera():
            nonlocal cap, read_fails
            if cap is not None:
                cap.release()
                cap = None
            read_fails = 0

        try:
            while self.running:
                # Release the webcam while gestures are toggled off.
                if not self.enabled:
                    drop_camera()
                    time.sleep(0.2)
                    continue

                if cap is None:
                    cap = self._open_camera()
                    if not cap.isOpened():
                        drop_camera()
                        time.sleep(3.0)  # camera busy/unavailable — retry
                        continue

                ret, frame = cap.read()
                if not ret:
                    # Stale captures happen after suspend/resume on Windows;
                    # retry, then reopen the device instead of giving up.
                    read_fails += 1
                    if read_fails >= MAX_READ_FAILS:
                        drop_camera()
                    time.sleep(0.1)
                    continue
                read_fails = 0

                frame_i += 1
                if frame_i % PROCESS_EVERY_N:
                    continue

                # Some Windows camera backends open fine but deliver black
                # frames; after ~5s of darkness switch to the next backend.
                if IS_WINDOWS and frame.mean() < 5:
                    dark_frames += 1
                    if dark_frames >= DARK_FRAME_LIMIT:
                        self._backend_i += 1
                        drop_camera()
                        dark_frames = 0
                        print("[GestureControl] camera gave only black frames, "
                              "trying another backend...", flush=True)
                    continue
                dark_frames = 0

                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = hands.process(rgb)
                now = time.time()

                hand = result.multi_hand_landmarks[0] if result.multi_hand_landmarks else None
                fingers = count_fingers(hand) if hand is not None else None

                if hand is not None:
                    if fingers == 1:
                        if hold_start is None:
                            hold_start = now
                        elif now - hold_start >= HOLD_LONG and now - last_action > COOLDOWN:
                            safe(brightness_up)
                            fire("Brightness up")
                            last_action = now
                            hold_start = None

                    elif fingers in (2, 3, 5):
                        if hold_start is None:
                            hold_start = now
                        elif now - hold_start >= HOLD_SHORT and now - last_action > COOLDOWN:
                            if fingers == 2:
                                safe(pyautogui.hotkey, HOTKEY_MOD, "t")
                                fire("New tab (Ctrl+T)" if IS_WINDOWS else "New tab (Cmd+T)")
                            elif fingers == 3:
                                safe(pyautogui.hotkey, HOTKEY_MOD, "n")
                                fire("New window (Ctrl+N)" if IS_WINDOWS else "New window (Cmd+N)")
                            elif fingers == 5:
                                safe(pyautogui.press, "space")
                                fire("Play/pause (Space)")
                            last_action = now
                            hold_start = None
                    else:
                        hold_start = None

                    if sleep_state == "WAIT_OPEN":
                        if fingers == 5:
                            sleep_state = "WAIT_CLOSE"
                    elif sleep_state == "WAIT_CLOSE":
                        if fingers == 0:
                            fist_start = now
                            sleep_state = "HOLD_FIST"
                    elif sleep_state == "HOLD_FIST":
                        if fingers == 0 and now - fist_start >= HOLD_SHORT:
                            fire("Sleep")
                            safe(system_sleep)
                            # On Windows SetSuspendState returns after resume;
                            # reset gestures and reopen the (now stale) camera
                            # so the app keeps working instead of dying here.
                            sleep_state = "WAIT_OPEN"
                            fist_start = None
                            hold_start = None
                            last_action = time.time()
                            drop_camera()
                            continue
                        elif fingers != 0:
                            sleep_state = "WAIT_OPEN"
                            fist_start = None

                if self.preview:
                    action = fired_label if now - fired_at < 2.0 else None
                    self._show_preview(frame, hand, fingers, hold_start, now, action)

                time.sleep(0.01)
        finally:
            drop_camera()
            hands.close()
            if self.preview:
                safe(cv2.destroyAllWindows)


# ===============================
# TRAY / MENU BAR APP
# ===============================
def run_windows(preview=False):
    import pystray
    from PIL import Image, ImageDraw

    def make_icon(enabled):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        color = (0, 200, 90, 255) if enabled else (150, 150, 150, 255)
        # simple palm: rounded box + four finger stubs
        d.rounded_rectangle([14, 26, 50, 58], radius=10, fill=color)
        for i, x in enumerate([16, 25, 34, 43]):
            d.rounded_rectangle([x, 8 + (0 if i in (1, 2) else 4), x + 7, 34], radius=3, fill=color)
        return img

    engine = GestureEngine(preview=preview)
    engine.start()

    state = {"enabled": load_state()}
    engine.enabled = state["enabled"]

    def label(_item=None):
        return "Gesture Control: ON" if state["enabled"] else "Gesture Control: OFF"

    def toggle(icon, _item):
        state["enabled"] = not state["enabled"]
        save_state(state["enabled"])
        engine.enabled = state["enabled"]
        icon.icon = make_icon(state["enabled"])
        icon.update_menu()

    def quit_app(icon, _item):
        save_state(state["enabled"])
        engine.running = False
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem(label, toggle),
        pystray.MenuItem("Quit", quit_app),
    )
    pystray.Icon("GestureControl", make_icon(state["enabled"]), "GestureControl", menu).run()


def run_mac():
    import rumps

    class GestureMenuApp(rumps.App):
        def __init__(self):
            super().__init__("\U0001f590", quit_button=None)
            self.engine = GestureEngine()
            self.engine.start()
            self.enabled = load_state()
            self.toggle_item = rumps.MenuItem(
                "Gesture Control: ON" if self.enabled else "Gesture Control: OFF",
                callback=self.toggle,
            )
            self.menu = [self.toggle_item, rumps.MenuItem("Quit", callback=self.quit_app)]
            self.engine.enabled = self.enabled

        def toggle(self, sender):
            self.enabled = not self.enabled
            save_state(self.enabled)
            self.engine.enabled = self.enabled
            sender.title = "Gesture Control: ON" if self.enabled else "Gesture Control: OFF"

        def quit_app(self, _):
            save_state(self.enabled)
            self.engine.running = False
            rumps.quit_application()

    GestureMenuApp().run()


# ===============================
# ENTRY POINT
# ===============================
if __name__ == "__main__":
    preview = "--preview" in sys.argv or "--debug" in sys.argv
    if IS_WINDOWS:
        run_windows(preview=preview)
    elif IS_MAC:
        if preview:
            print("--preview is only supported on Windows; ignoring.", flush=True)
        run_mac()
    else:
        sys.exit("GestureControl supports Windows and macOS only.")
