import cv2
import mediapipe as mp
import time
import pyautogui
import subprocess
import threading
import rumps
import json
import os

# ===============================
# PERSISTENT STATE
# ===============================
APP_DIR = os.path.expanduser("~/Library/Application Support/GestureControl")
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
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to key code 145'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

def sleep_mac():
    subprocess.run(
        ["osascript", "-e",
         'tell application "System Events" to sleep'],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )

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
    def __init__(self):
        super().__init__(daemon=True)
        self.enabled = load_state()
        self.running = True

    def run(self):
        cap = cv2.VideoCapture(0, cv2.CAP_AVFOUNDATION)
        if not cap.isOpened():
            return

        hands = mp.solutions.hands.Hands(
            max_num_hands=1,
            min_detection_confidence=0.6,
            min_tracking_confidence=0.6
        )

        HOLD_SHORT = 1.5
        HOLD_LONG = 3.0
        COOLDOWN = 1.5

        hold_start = None
        last_action = 0
        sleep_state = "WAIT_OPEN"
        fist_start = None

        while self.running:
            ret, frame = cap.read()
            if not ret:
                break

            if not self.enabled:
                time.sleep(0.05)
                continue

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            result = hands.process(rgb)
            now = time.time()

            if result.multi_hand_landmarks:
                hand = result.multi_hand_landmarks[0]
                fingers = count_fingers(hand)

                if fingers == 1:
                    if hold_start is None:
                        hold_start = now
                    elif now - hold_start >= HOLD_LONG and now - last_action > COOLDOWN:
                        brightness_up()
                        last_action = now
                        hold_start = None

                elif fingers in (2, 3, 5):
                    if hold_start is None:
                        hold_start = now
                    elif now - hold_start >= HOLD_SHORT and now - last_action > COOLDOWN:
                        if fingers == 2:
                            pyautogui.hotkey("command", "t")
                        elif fingers == 3:
                            pyautogui.hotkey("command", "n")
                        elif fingers == 5:
                            pyautogui.press("space")
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
                        sleep_mac()
                        break

            time.sleep(0.01)

        cap.release()

# ===============================
# MENU BAR APP
# ===============================
class GestureMenuApp(rumps.App):
    def __init__(self):
        super().__init__("🖐", quit_button=None)

        self.engine = GestureEngine()
        self.engine.start()

        self.enabled = load_state()

        self.toggle_item = rumps.MenuItem(
            "Gesture Control: ON" if self.enabled else "Gesture Control: OFF",
            callback=self.toggle
        )

        self.menu = [
            self.toggle_item,
            rumps.MenuItem("Quit", callback=self.quit_app)
        ]

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

# ===============================
# ENTRY POINT
# ===============================
if __name__ == "__main__":
    GestureMenuApp().run()
    