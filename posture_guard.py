#!/usr/bin/env python3
"""
PostureGuard – theo dõi tư thế đầu–cổ bằng webcam, nhắc khi ngồi sai hoặc ngồi quá lâu.
Chạy được trên Windows / macOS / Linux. Toàn bộ xử lý hình ảnh diễn ra trên máy, không lưu ảnh.

Cách dùng nhanh:
    python posture_guard.py --list-cameras        # xem webcam nào đang có
    python posture_guard.py --camera 1 --preview  # chạy với webcam số 1, có cửa sổ xem trước
Phím tắt (khi có cửa sổ preview): c = hiệu chỉnh lại, p = tắt/bật theo dõi, q = thoát
macOS: icon trên thanh menu; Windows: icon ở khay hệ thống (xanh/đỏ/xám) để bật/tắt, tạm dừng 30 phút,
hiệu chỉnh, chọn camera, tự chạy khi mở máy, thoát.
"""
import argparse
import csv
import json
import math
import platform
import queue
import ssl
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

import urllib.request

import certifi
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks.python import BaseOptions
from mediapipe.tasks.python.vision import (PoseLandmarker, PoseLandmarkerOptions,
                                           PoseLandmarksConnections, RunningMode)

APP = "PostureGuard"
VERSION = "1.1.0"
IS_MAC = platform.system() == "Darwin"
IS_WIN = platform.system() == "Windows"
FROZEN = getattr(sys, "frozen", False)  # đang chạy dạng app đóng gói (PyInstaller), không phải mã nguồn
CONFIG_DIR = Path.home() / ".posture_guard"
LOG_FILE = CONFIG_DIR / "posture_log.csv"
APP_LOG = CONFIG_DIR / "app.log"
MODEL_FILE = CONFIG_DIR / "pose_landmarker_lite.task"
BUNDLED_MODEL = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / "pose_landmarker_lite.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task")

def setup_output() -> None:
    """
    App đóng gói không có cửa sổ dòng lệnh: sys.stdout là None → ghi ra ~/.posture_guard/app.log.
    Cửa sổ dòng lệnh Windows mặc định không in được tiếng Việt → chuyển sang UTF-8.
    """
    if sys.stdout is None or sys.stderr is None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        log = open(APP_LOG, "a", encoding="utf-8", buffering=1)
        if sys.stdout is None:
            sys.stdout = log
        if sys.stderr is None:
            sys.stderr = log
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def helper_cmd(flag: str) -> list:
    """Lệnh chạy tiến trình con (lớp phủ / icon). App đóng gói: chính file chạy của app."""
    if FROZEN:
        return [sys.executable, flag]
    return [sys.executable, str(Path(__file__).resolve()), flag]


def popen_helper(flag: str, **kw):
    if IS_WIN:
        kw["creationflags"] = 0x08000000  # CREATE_NO_WINDOW: không bật cửa sổ đen
    return subprocess.Popen(helper_cmd(flag), text=True, encoding="utf-8", **kw)


# Chỉ số điểm mốc của MediaPipe Pose
NOSE, L_EYE, R_EYE, L_EAR, R_EAR, L_SH, R_SH = 0, 2, 5, 7, 8, 11, 12


# ----------------------------------------------------------------------------- bộ nhận diện
class PoseDetector:
    """Bọc MediaPipe PoseLandmarker (Tasks API, mediapipe >= 0.10.30). Trả về list 33 điểm mốc hoặc None."""

    def __init__(self):
        if BUNDLED_MODEL.exists():
            model = BUNDLED_MODEL.read_bytes()
        else:
            model = self._download()
        opts = PoseLandmarkerOptions(
            # truyền nội dung thay vì đường dẫn: MediaPipe trên Windows lỗi với thư mục có dấu (C:\Users\Đức)
            base_options=BaseOptions(model_asset_buffer=model),
            running_mode=RunningMode.VIDEO,
            num_poses=1,
            min_pose_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self.lm = PoseLandmarker.create_from_options(opts)
        self.last_ts = 0

    @staticmethod
    def _download() -> bytes:
        if not MODEL_FILE.exists():
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            print("Lần đầu chạy: đang tải mô hình nhận diện tư thế (~5 MB)...")
            # Python cài từ python.org trên macOS không có sẵn chứng chỉ gốc → dùng bộ chứng chỉ của certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
            with urllib.request.urlopen(MODEL_URL, context=ctx) as r:
                data = r.read()
            MODEL_FILE.write_bytes(data)
        return MODEL_FILE.read_bytes()

    def process(self, frame_bgr):
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        ts = max(int(time.monotonic() * 1000), self.last_ts + 1)  # timestamp phải tăng dần
        self.last_ts = ts
        res = self.lm.detect_for_video(img, ts)
        return res.pose_landmarks[0] if res.pose_landmarks else None

    def close(self):
        self.lm.close()


def draw_skeleton(frame, landmarks):
    h, w = frame.shape[:2]
    pts = [(int(p.x * w), int(p.y * h)) for p in landmarks]
    for c in PoseLandmarksConnections.POSE_LANDMARKS:
        if c.start < 25 and c.end < 25:  # chỉ vẽ phần thân trên
            cv2.line(frame, pts[c.start], pts[c.end], (255, 200, 0), 2)
    for p in pts[:25]:
        cv2.circle(frame, p, 3, (0, 255, 255), -1)


# ----------------------------------------------------------------------------- thông báo
def notify(title: str, msg: str) -> None:
    """Hiện thông báo hệ thống + tiếng bíp, tùy hệ điều hành."""
    system = platform.system()
    try:
        if system == "Darwin":
            safe = msg.replace('"', "'")
            subprocess.run(
                ["osascript", "-e", f'display notification "{safe}" with title "{title}" sound name "Glass"'],
                check=False,
            )
        elif system == "Windows":
            try:
                from plyer import notification
                notification.notify(title=title, message=msg, app_name=APP, timeout=6)
            except Exception:
                pass
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            subprocess.run(["notify-send", title, msg], check=False)
    except Exception as e:  # không để lỗi thông báo làm dừng chương trình
        print(f"[notify] {e}")
    print(f"[{datetime.now():%H:%M:%S}] {title}: {msg}", flush=True)


# ----------------------------------------------------------------------------- làm mờ màn hình (kiểu Dorso)
def run_overlay_helper() -> None:
    """
    Tiến trình con (macOS): phủ lên mọi màn hình một lớp làm mờ + tối, không chặn chuột/bàn phím.
    Đọc lệnh từ stdin, mỗi dòng một lệnh; stdin đóng (tiến trình chính thoát) thì tự thoát:
      <số 0..1>               độ mờ mục tiêu
      target <tên điểm|none> [mac]  hiện chấm đỏ ở điểm cần nhìn khi hiệu chỉnh (center, tl, tr, bl, br);
                                    "mac" = đặt trên màn hình MacBook (đang dùng camera MacBook)
    Chạy tách riêng để vòng lặp sự kiện Cocoa không tranh chấp với cửa sổ preview của OpenCV.
    """
    import threading
    import AppKit
    from PyObjCTools import AppHelper

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # không hiện icon trên Dock

    behavior = (AppKit.NSWindowCollectionBehaviorCanJoinAllSpaces
                | AppKit.NSWindowCollectionBehaviorStationary
                | AppKit.NSWindowCollectionBehaviorFullScreenAuxiliary
                | AppKit.NSWindowCollectionBehaviorIgnoresCycle)

    def make_window(rect, level):
        win = AppKit.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
            rect, AppKit.NSWindowStyleMaskBorderless, AppKit.NSBackingStoreBuffered, False)
        win.setOpaque_(False)
        win.setBackgroundColor_(AppKit.NSColor.clearColor())
        win.setIgnoresMouseEvents_(True)
        win.setHasShadow_(False)
        win.setReleasedWhenClosed_(False)
        win.setLevel_(level)
        win.setCollectionBehavior_(behavior)
        return win

    def screens_signature():
        return tuple((f.origin.x, f.origin.y, f.size.width, f.size.height)
                     for f in (sc.frame() for sc in AppKit.NSScreen.screens()))

    windows = []

    def build_blur_windows():
        """Một lớp mờ cho mỗi màn hình. Gọi lại khi cắm/rút màn hình ngoài hoặc đổi độ phân giải."""
        for w in windows:
            w.orderOut_(None)
            w.close()
        windows.clear()
        for screen in AppKit.NSScreen.screens():
            frame = screen.frame()
            win = make_window(frame, AppKit.NSScreenSaverWindowLevel)
            bounds = ((0, 0), (frame.size.width, frame.size.height))
            blur = AppKit.NSVisualEffectView.alloc().initWithFrame_(bounds)
            blur.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
            blur.setMaterial_(AppKit.NSVisualEffectMaterialFullScreenUI)
            blur.setState_(AppKit.NSVisualEffectStateActive)
            tint = AppKit.NSBox.alloc().initWithFrame_(bounds)  # lớp tối phủ trên lớp mờ
            tint.setBoxType_(AppKit.NSBoxCustom)
            tint.setBorderWidth_(0)
            tint.setFillColor_(AppKit.NSColor.colorWithWhite_alpha_(0.0, 0.35))
            blur.addSubview_(tint)
            win.setContentView_(blur)
            win.setAlphaValue_(state["cur"])
            win.orderFrontRegardless()
            windows.append(win)

    # chấm đích khi hiệu chỉnh
    size, margin = 120, 24
    dot_win = make_window(((0, 0), (size, size)), AppKit.NSScreenSaverWindowLevel + 1)
    ring = AppKit.NSBox.alloc().initWithFrame_(((0, 0), (size, size)))
    ring.setBoxType_(AppKit.NSBoxCustom)
    ring.setCornerRadius_(size / 2)
    ring.setBorderWidth_(6)
    ring.setBorderColor_(AppKit.NSColor.whiteColor())
    ring.setFillColor_(AppKit.NSColor.colorWithRed_green_blue_alpha_(0.9, 0.1, 0.1, 0.9))
    dot_win.setContentView_(ring)

    def target_screen(where):
        """
        where = "mac": đang dùng camera MacBook → chấm hiện trên màn hình MacBook (nếu đang bật).
        Ngược lại (webcam) → màn hình lớn nhất, thường là màn hình rộng gắn webcam.
        """
        screens = list(AppKit.NSScreen.screens())
        if where == "mac":
            try:
                import Quartz
                builtin = [sc for sc in screens
                           if Quartz.CGDisplayIsBuiltin(sc.deviceDescription()["NSScreenNumber"])]
                if builtin:
                    return builtin[0]
            except Exception:
                pass
        return max(screens, key=lambda sc: sc.frame().size.width * sc.frame().size.height)

    def show_target(name, where):
        """Tính vị trí theo màn hình hiện tại ngay lúc hiện (màn hình có thể đã đổi từ lúc khởi động)."""
        if name not in ("center", "tl", "tr", "bl", "br"):
            dot_win.orderOut_(None)
            return
        vf = target_screen(where).visibleFrame()  # chừa thanh menu và Dock
        left, right = vf.origin.x + margin, vf.origin.x + vf.size.width - size - margin
        bottom, top = vf.origin.y + margin, vf.origin.y + vf.size.height - size - margin
        x = {"center": (left + right) / 2, "tl": left, "bl": left, "tr": right, "br": right}[name]
        y = {"center": (bottom + top) / 2, "tl": top, "tr": top, "bl": bottom, "br": bottom}[name]
        dot_win.setFrameOrigin_((x, y))
        dot_win.orderFrontRegardless()

    state = {"target": 0.0, "cur": 0.0, "t": 0, "screens": screens_signature()}
    build_blur_windows()

    def read_stdin():
        for line in sys.stdin:
            if line.startswith("target "):
                parts = line.split()
                AppHelper.callAfter(show_target, parts[1], parts[2] if len(parts) > 2 else "")
                continue
            try:
                state["target"] = min(max(float(line), 0.0), 1.0)
            except ValueError:
                pass
        AppHelper.callAfter(app.terminate_, None)

    def tick(_timer):
        state["t"] += 1
        if state["t"] % 30 == 0:  # mỗi giây: màn hình thay đổi thì dựng lại lớp mờ cho khớp
            sig = screens_signature()
            if sig != state["screens"]:
                state["screens"] = sig
                build_blur_windows()
        if dot_win.isVisible():  # chấm đích nhấp nháy nhẹ cho dễ thấy
            dot_win.setAlphaValue_(0.65 + 0.35 * math.sin(state["t"] / 5))
        cur, tgt = state["cur"], state["target"]
        step = 0.02 if tgt > cur else 0.08  # mờ dần chậm, trong lại nhanh
        cur = min(tgt, cur + step) if tgt > cur else max(tgt, cur - step)
        if cur != state["cur"]:
            state["cur"] = cur
            for w in windows:
                w.setAlphaValue_(cur)

    threading.Thread(target=read_stdin, daemon=True).start()
    AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(1 / 30, True, tick)
    AppHelper.runEventLoop(installInterrupt=True)


class ScreenBlur:
    """Điều khiển tiến trình làm mờ màn hình (macOS: mờ + tối; Windows: tối). Hệ khác thì không làm gì."""
    LOG = CONFIG_DIR / "overlay.log"

    def __init__(self, enabled: bool):
        self.proc, self.level, self.restarts, self.enabled = None, 0.0, 0, enabled
        if not (IS_MAC or IS_WIN):
            if enabled:
                print("Làm mờ màn hình chỉ hỗ trợ macOS và Windows – bỏ qua.")
            return
        self._start()  # vẫn chạy khi tắt làm mờ: còn dùng để hiện chấm đích lúc hiệu chỉnh
        if enabled:
            print("Làm mờ màn hình khi sai tư thế: BẬT (tắt bằng --no-blur)")

    def _start(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.proc = popen_helper("--overlay-helper", stdin=subprocess.PIPE,
                                 stdout=subprocess.DEVNULL, stderr=self.LOG.open("w"))
        self.level = 0.0

    def _alive(self) -> bool:
        """Lớp phủ bị tắt ngoài ý muốn → báo lỗi, khởi động lại (tối đa 3 lần), sau đó dùng thông báo thay thế."""
        if self.proc and self.proc.poll() is not None:
            print(f"[blur] lớp làm mờ đã dừng (mã {self.proc.returncode}), chi tiết: {self.LOG}", flush=True)
            self.proc = None
            if self.restarts < 3:
                self.restarts += 1
                self._start()
        return self.proc is not None

    @property
    def active(self) -> bool:
        return self.enabled and self._alive()

    def _write(self, line: str) -> bool:
        if not self._alive():
            return False
        try:
            self.proc.stdin.write(line + "\n")
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, OSError):
            self.proc.kill()  # lần gọi sau _alive() sẽ phát hiện và khởi động lại
            return False

    def set(self, level: float) -> None:
        if not self.enabled or level == self.level or (level and abs(level - self.level) < 0.01):
            return
        if self._write(f"{level:.3f}"):
            self.level = level

    def target(self, name: str = "none", where: str = "") -> bool:
        """
        Hiện chấm đỏ ở điểm cần nhìn khi hiệu chỉnh. where="mac": đặt trên màn hình MacBook.
        Trả về False nếu không hiện được.
        """
        return self._write(f"target {name} {where}".strip())

    def close(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
            self.proc = None


# ----------------------------------------------------------------------------- icon trên thanh menu (macOS)
def run_menubar_helper() -> None:
    """
    Tiến trình con (macOS): icon PostureGuard trên thanh menu. Xanh = tư thế tốt, đỏ = sai tư thế,
    vòng xám = tạm dừng / không thấy người.
    stdin : mỗi dòng một JSON trạng thái {icon, status, session, enabled, paused_until, camera, cam_name}.
    stdout: mỗi dòng một lệnh khi bấm menu:
            toggle | pause | resume | calibrate | camera <auto|mac|webcam> | autostart | quit.
    stdin đóng (tiến trình chính thoát) thì tự thoát. Tách riêng để vòng lặp Cocoa không tranh chấp với OpenCV.
    """
    import AppKit
    from PyObjCTools import AppHelper

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)  # không hiện icon trên Dock

    def send(cmd):
        try:
            sys.stdout.write(cmd + "\n")
            sys.stdout.flush()
        except (BrokenPipeError, OSError):
            app.terminate_(None)

    class MenuTarget(AppKit.NSObject):
        def act_(self, sender):
            send(sender.representedObject())

    target = MenuTarget.alloc().init()

    def dot(r, g, b, filled=True):
        color = AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, 1.0)

        def draw(rect):
            path = AppKit.NSBezierPath.bezierPathWithOvalInRect_(AppKit.NSInsetRect(rect, 3.5, 3.5))
            if filled:
                color.setFill()
                path.fill()
            else:  # vòng rỗng: dễ phân biệt với xanh/đỏ kể cả với người mù màu
                color.setStroke()
                path.setLineWidth_(2.0)
                path.stroke()
            return True

        return AppKit.NSImage.imageWithSize_flipped_drawingHandler_((18, 18), False, draw)

    icons = {"green": dot(0.20, 0.78, 0.35), "red": dot(1.0, 0.23, 0.19), "grey": dot(0.55, 0.55, 0.55, False)}

    root = AppKit.NSMenu.alloc().init()
    root.setAutoenablesItems_(False)

    def item(title, cmd=None, menu=root):
        it = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, "act:" if cmd else None, "")
        if cmd:
            it.setTarget_(target)
            it.setRepresentedObject_(cmd)
        else:
            it.setEnabled_(False)  # dòng thông tin
        menu.addItem_(it)
        return it

    status_it = item("Đang khởi động...")
    session_it = item("")
    session_it.setHidden_(True)
    cam_it = item("Camera: ...")
    root.addItem_(AppKit.NSMenuItem.separatorItem())
    toggle_it = item("Tắt theo dõi", "toggle")
    pause_it = item("Tạm dừng 30 phút", "pause")
    item("Hiệu chỉnh lại", "calibrate")
    cam_menu = AppKit.NSMenu.alloc().init()
    cam_items = {k: item(t, f"camera {k}", cam_menu)
                 for k, t in (("auto", "Tự động"), ("mac", "MacBook"), ("webcam", "Webcam"))}
    cam_root = item("Chọn camera")
    cam_root.setSubmenu_(cam_menu)
    cam_root.setEnabled_(True)
    autostart_it = item("Tự chạy khi mở máy", "autostart")
    autostart_it.setHidden_(True)
    root.addItem_(AppKit.NSMenuItem.separatorItem())
    item("Thoát PostureGuard", "quit")

    si = AppKit.NSStatusBar.systemStatusBar().statusItemWithLength_(AppKit.NSVariableStatusItemLength)
    si.button().setImage_(icons["grey"])
    si.button().setToolTip_(APP)
    si.setMenu_(root)

    def apply(st):
        status = st.get("status", "")
        si.button().setImage_(icons.get(st.get("icon"), icons["grey"]))
        si.button().setToolTip_(f"{APP}: {status}")
        status_it.setTitle_(status)
        session_it.setTitle_(st.get("session") or "")
        session_it.setHidden_(not st.get("session"))
        cam_it.setTitle_(f"Camera: {st.get('cam_name', '')}")
        enabled = st.get("enabled", True)
        toggle_it.setTitle_("Tắt theo dõi" if enabled else "Bật theo dõi")
        if st.get("paused_until"):
            pause_it.setTitle_(f"Tiếp tục ngay (đang tạm dừng đến {st['paused_until']})")
            pause_it.setRepresentedObject_("resume")
        else:
            pause_it.setTitle_("Tạm dừng 30 phút")
            pause_it.setRepresentedObject_("pause")
        pause_it.setEnabled_(enabled)
        for k, it in cam_items.items():
            it.setState_(AppKit.NSControlStateValueOn if k == st.get("camera") else AppKit.NSControlStateValueOff)
        autostart_it.setHidden_(st.get("autostart") is None)  # None = không hỗ trợ (chạy từ mã nguồn)
        autostart_it.setState_(AppKit.NSControlStateValueOn if st.get("autostart")
                               else AppKit.NSControlStateValueOff)

    def read_stdin():
        for line in sys.stdin:
            try:
                st = json.loads(line)
            except ValueError:
                continue
            AppHelper.callAfter(apply, st)
        AppHelper.callAfter(app.terminate_, None)

    threading.Thread(target=read_stdin, daemon=True).start()
    AppHelper.runEventLoop(installInterrupt=True)


class MenuBar:
    """Điều khiển tiến trình icon (macOS: thanh menu; Windows: khay hệ thống). Hệ khác thì không làm gì."""
    LOG = CONFIG_DIR / "menubar.log"

    def __init__(self, enabled: bool):
        self.proc, self.state, self.restarts = None, None, 0
        self.cmds = queue.Queue()
        self.quit_requested = False
        if not enabled:
            return
        if not (IS_MAC or IS_WIN):
            print("Icon chỉ hỗ trợ macOS và Windows – bỏ qua.")
            return
        self._start()
        print(f"Icon trên {'thanh menu' if IS_MAC else 'khay hệ thống'}: BẬT (tắt bằng --no-menubar)")

    def _start(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        self.proc = popen_helper("--menubar-helper", stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=self.LOG.open("w"))
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()
        if self.state:
            self._send()

    def _read(self, proc) -> None:
        for line in proc.stdout:
            cmd = line.strip()
            if cmd == "quit":
                self.quit_requested = True  # để các vòng lặp chờ lâu (hiệu chỉnh) cũng thoát được
            if cmd:
                self.cmds.put(cmd)

    def _alive(self) -> bool:
        """Icon bị tắt ngoài ý muốn → báo lỗi, khởi động lại (tối đa 3 lần)."""
        if self.proc and self.proc.poll() is not None:
            print(f"[menubar] icon thanh menu đã dừng (mã {self.proc.returncode}), chi tiết: {self.LOG}", flush=True)
            self.proc = None
            if self.restarts < 3:
                self.restarts += 1
                self._start()
        return self.proc is not None

    def _send(self) -> None:
        try:
            self.proc.stdin.write(json.dumps(self.state) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError):
            self.proc.kill()  # lần gọi sau _alive() sẽ phát hiện và khởi động lại

    def poll(self) -> list:
        """Các lệnh người dùng đã bấm trên menu từ lần gọi trước."""
        out = []
        while not self.cmds.empty():
            out.append(self.cmds.get_nowait())
        return out

    def update(self, **state) -> None:
        changed = state != self.state
        self.state = state
        if self._alive() and changed:
            self._send()

    def close(self) -> None:
        if self.proc:
            try:
                self.proc.stdin.close()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
            self.proc = None


# ----------------------------------------------------------------------------- camera
def open_camera(index: int):
    if platform.system() == "Windows":
        cap = cv2.VideoCapture(index, cv2.CAP_DSHOW)  # mở nhanh hơn trên Windows
    else:
        cap = cv2.VideoCapture(index)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return cap


def camera_devices():
    """
    [(chỉ số OpenCV, tên, loại)] – chỉ có trên macOS (cần pyobjc-framework-AVFoundation).
    loại: "mac" (camera MacBook), "webcam" (webcam cắm rời), "phone" (iPhone/iPad qua Continuity Camera).
    OpenCV đánh số camera AVFoundation theo uniqueID tăng dần nên sắp xếp y như vậy để khớp chỉ số.
    Windows: xem pg_windows.camera_devices(). Trả về [] nếu không lấy được tên (Linux).
    """
    if IS_WIN:
        import pg_windows
        return pg_windows.camera_devices()
    if not IS_MAC:
        return []
    try:
        import AVFoundation as AV
        import Foundation
    except ImportError:
        return []
    # cho macOS xử lý sự kiện cắm/rút camera trước khi đọc danh sách (khi chạy ngầm không có vòng lặp sự kiện)
    Foundation.NSRunLoop.currentRunLoop().runUntilDate_(Foundation.NSDate.dateWithTimeIntervalSinceNow_(0.01))
    devs = sorted(AV.AVCaptureDevice.devicesWithMediaType_(AV.AVMediaTypeVideo), key=lambda d: str(d.uniqueID()))
    return [(i, str(d.localizedName()), camera_kind(d)) for i, d in enumerate(devs)]


def camera_kind(d) -> str:
    """
    Phân loại camera. iPhone/iPad dùng làm camera qua Continuity Camera được macOS báo là camera rời
    (AVCaptureDeviceTypeExternal), nên phải nhận ra riêng để chế độ auto không tự bật camera điện thoại.
    """
    dtype = str(d.deviceType())
    if "BuiltIn" in dtype:
        return "mac"
    model = str(d.modelID() or "")
    name = str(d.localizedName())
    if ("Continuity" in dtype or "DeskView" in dtype or model.startswith(("iPhone", "iPad"))
            or "iPhone" in name or "iPad" in name):
        return "phone"
    return "webcam"


def resolve_camera(spec: str):
    """
    spec: "auto" (có webcam rời thì dùng webcam, không thì dùng camera MacBook; không bao giờ tự chọn camera
    điện thoại), "mac", "webcam" hoặc số thứ tự.
    Trả về (chỉ số, tên hiển thị, khóa dùng để lưu tư thế chuẩn riêng cho từng camera).
    """
    devs = camera_devices()
    if spec.isdigit():
        idx = int(spec)
        name = next((n for i, n, _ in devs if i == idx), f"Camera {idx}")
    else:
        builtin = [d for d in devs if d[2] == "mac"]
        external = [d for d in devs if d[2] == "webcam"]  # không tính camera điện thoại
        if spec == "mac":
            pick = builtin[:1]
        elif spec == "webcam":
            pick = external[:1]
        else:  # auto
            pick = (external or builtin)[:1]
        if not pick:
            if devs or spec != "auto":
                return None
            pick = [(0, "Camera 0", "webcam")]  # không đọc được tên camera → dùng camera 0
        idx, name, _ = pick[0]
    key = "".join(c if c.isalnum() else "_" for c in name.lower()).strip("_")
    return idx, name, key


def baseline_file(cam_key: str) -> Path:
    return CONFIG_DIR / f"baseline_{cam_key}.json"


def list_cameras(max_index: int = 6) -> None:
    devs = camera_devices()
    if devs:
        print("Camera trên máy:")
        labels = {"mac": "camera MacBook", "webcam": "webcam rời",
                  "phone": "camera điện thoại – auto không dùng, chọn bằng số nếu muốn"}
        for i, name, kind in devs:
            print(f"  {i}: {name}  ({labels[kind]})")
        print("Chọn bằng --camera auto | mac | webcam | <số>  (mặc định auto: ưu tiên webcam rời)")
        return
    print("Đang dò webcam...")
    for i in range(max_index):
        cap = open_camera(i)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        if ok:
            h, w = frame.shape[:2]
            print(f"  Camera {i}: hoạt động ({w}x{h})")
        cap.release()
    print("Dùng --camera <số> để chọn. Webcam gắn ngoài thường là 1 hoặc 2.")


# ----------------------------------------------------------------------------- đo tư thế
def compute_metrics(landmarks, w: int, h: int):
    """
    Trả về dict chỉ số tư thế, hoặc None nếu không thấy rõ đầu + 2 vai.
      neck_ratio : (y vai – y mũi) / bề rộng vai  → giảm khi cúi đầu / đầu đưa ra trước rồi hạ xuống
      head_scale : khoảng cách 2 mắt / bề rộng vai → tăng khi đầu nhô về phía camera (forward head)
      shoulder_w : bề rộng vai (pixel)             → tăng khi cả thân đổ về phía màn hình
      roll       : góc nghiêng đầu sang bên (độ)
      sh_tilt    : góc lệch vai (độ)
    """
    lm = landmarks

    def pt(i):
        return np.array([lm[i].x * w, lm[i].y * h])

    if min(lm[NOSE].visibility, lm[L_SH].visibility, lm[R_SH].visibility) < 0.5:
        return None

    ls, rs, nose = pt(L_SH), pt(R_SH), pt(NOSE)
    le, re = pt(L_EYE), pt(R_EYE)
    shoulder_w = float(np.linalg.norm(ls - rs))
    if shoulder_w < 20:
        return None
    sh_mid = (ls + rs) / 2
    eye_vec = le - re
    sh_vec = ls - rs
    return {
        "neck_ratio": float((sh_mid[1] - nose[1]) / shoulder_w),
        "head_scale": float(np.linalg.norm(eye_vec) / shoulder_w),
        "shoulder_w": shoulder_w,
        "roll": math.degrees(math.atan2(eye_vec[1], eye_vec[0])),
        "sh_tilt": math.degrees(math.atan2(sh_vec[1], sh_vec[0])),
    }


def base_range(base: dict):
    """
    Tư thế chuẩn → (lo, hi): giá trị nhỏ nhất / lớn nhất của từng chỉ số trong các điểm đã nhìn khi hiệu chỉnh.
    Tư thế chuẩn kiểu cũ (chỉ nhìn giữa màn hình) có lo = hi.
    """
    if "lo" in base:
        return base["lo"], base["hi"]
    return base, base


def assess(m: dict, base: dict, sens: dict):
    """
    So sánh với tư thế chuẩn đã hiệu chỉnh → danh sách vấn đề (rỗng = tư thế tốt).
    Chỉ báo sai khi chỉ số vượt ra ngoài khoảng đo được lúc nhìn giữa và 4 góc màn hình (cộng thêm ngưỡng),
    nên liếc xuống góc màn hình rộng không bị tính là cúi đầu.
    """
    lo, hi = base_range(base)
    issues = []
    if m["neck_ratio"] < lo["neck_ratio"] * (1 - sens["drop"]):
        issues.append("Cúi đầu / đầu hạ thấp")
    if m["head_scale"] > hi["head_scale"] * (1 + sens["forward"]):
        issues.append("Đầu đưa ra trước")
    if m["shoulder_w"] > hi["shoulder_w"] * (1 + sens["lean"]):
        issues.append("Ngồi quá sát màn hình")
    if not lo["roll"] - sens["tilt_deg"] <= m["roll"] <= hi["roll"] + sens["tilt_deg"]:
        issues.append("Nghiêng đầu")
    if not lo["sh_tilt"] - sens["tilt_deg"] <= m["sh_tilt"] <= hi["sh_tilt"] + sens["tilt_deg"]:
        issues.append("Lệch vai")
    return issues


class EMA:
    """Làm mượt số liệu để tránh báo động giả khi quay đầu thoáng qua."""

    def __init__(self, alpha=0.3):
        self.alpha, self.v = alpha, None

    def __call__(self, m):
        if self.v is None:
            self.v = dict(m)
        else:
            for k in m:
                self.v[k] = self.alpha * m[k] + (1 - self.alpha) * self.v[k]
        return dict(self.v)

    def reset(self):
        self.v = None


# ----------------------------------------------------------------------------- hiệu chỉnh
CALIB_POINTS = [  # (mã, lời nhắc, chữ trên preview)
    ("center", "giữa màn hình", "GIUA man hinh"),
    ("tl", "góc TRÊN bên TRÁI", "goc TREN - TRAI"),
    ("tr", "góc TRÊN bên PHẢI", "goc TREN - PHAI"),
    ("bl", "góc DƯỚI bên TRÁI", "goc DUOI - TRAI"),
    ("br", "góc DƯỚI bên PHẢI", "goc DUOI - PHAI"),
]


def calibrate(cap, pose, path: Path, preview=False, stop=lambda: False, marker=None, corners=True,
              settle=1.5, seconds=2.5):
    """
    Ngồi thẳng, lần lượt nhìn vào giữa và 4 góc màn hình (chỉ đưa mắt/đầu, không cúi người).
    Mỗi điểm: chờ `settle` giây cho kịp nhìn sang, rồi đo `seconds` giây.
    Lưu trung vị của từng điểm và khoảng [lo, hi] của từng chỉ số.
    marker(name): hiện chấm đích trên màn hình, trả về False nếu không hiện được.
    stop(): trả về True để hủy giữa chừng (vd. bấm Thoát trên thanh menu).
    """
    points = CALIB_POINTS if corners else CALIB_POINTS[:1]
    if corners:
        notify(APP, "Hiệu chỉnh: ngồi thẳng, lần lượt nhìn vào chấm đỏ ở giữa và 4 góc màn hình "
                    "(khoảng 20 giây). Chỉ đưa mắt/đầu, không cúi người.")
    else:
        notify(APP, "Hiệu chỉnh: ngồi thẳng, nhìn giữa màn hình trong vài giây.")
    time.sleep(2)
    marker = marker or (lambda name: False)
    per_point = {}
    try:
        for code, label, ascii_label in points:
            if not marker(code):
                notify(APP, f"Nhìn vào {label}")  # không hiện được chấm đích → nhắc bằng thông báo
            samples, t0 = [], time.time()
            while time.time() - t0 < settle + seconds:
                if stop():
                    return None
                ok, frame = cap.read()
                if not ok:
                    continue
                if time.time() - t0 >= settle:
                    h, w = frame.shape[:2]
                    lms = pose.process(frame)
                    m = compute_metrics(lms, w, h) if lms else None
                    if m:
                        samples.append(m)
                if preview:
                    cv2.putText(frame, f"CALIBRATING - nhin {ascii_label}", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 200, 255), 2)
                    cv2.imshow(APP, frame)
                    cv2.waitKey(1)
            if len(samples) >= 3:
                per_point[code] = {k: float(np.median([s[k] for s in samples])) for k in samples[0]}
    finally:
        marker("none")
    if "center" not in per_point:
        notify(APP, "Hiệu chỉnh thất bại: không thấy rõ đầu và 2 vai. Chỉnh lại camera rồi thử lại.")
        return None
    keys = per_point["center"].keys()
    base = {
        "center": per_point["center"],
        "lo": {k: min(p[k] for p in per_point.values()) for k in keys},
        "hi": {k: max(p[k] for p in per_point.values()) for k in keys},
        "points": per_point,
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(base, indent=2))
    missed = [label for code, label, _ in points if code not in per_point]
    notify(APP, "Đã lưu tư thế chuẩn." + (f" Không đo được: {', '.join(missed)}." if missed else ""))
    return base


# ----------------------------------------------------------------------------- nguồn điện
def on_battery() -> bool:
    """True nếu máy đang chạy bằng pin (macOS: pmset). Không xác định được thì coi như đang cắm sạc."""
    if IS_WIN:
        import pg_windows
        return pg_windows.on_battery()
    if not IS_MAC:
        return False
    try:
        out = subprocess.run(["pmset", "-g", "ps"], capture_output=True, text=True, timeout=3).stdout
        return "Battery Power" in out
    except Exception:
        return False


# ----------------------------------------------------------------------------- tự chạy khi mở máy
MAC_AGENT = Path.home() / "Library" / "LaunchAgents" / "com.posture-guard.app.plist"


def mac_app_bundle():
    """Đường dẫn PostureGuard.app khi đang chạy dạng app đóng gói trên macOS, ngược lại None."""
    if not (IS_MAC and FROZEN):
        return None
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def autostart_state():
    """True/False = đang bật/tắt; None = không hỗ trợ (chạy từ mã nguồn trên macOS: dùng install_autostart.sh)."""
    if IS_WIN:
        import pg_windows
        return pg_windows.autostart_enabled()
    if mac_app_bundle():
        return MAC_AGENT.exists()
    return None


def toggle_autostart() -> None:
    on = not autostart_state()
    try:
        if IS_WIN:
            import pg_windows
            if FROZEN:
                command = f'"{sys.executable}"'
            else:  # chạy từ mã nguồn: dùng pythonw để không bật cửa sổ đen
                pyw = Path(sys.executable).with_name("pythonw.exe")
                command = f'"{pyw if pyw.exists() else sys.executable}" "{Path(__file__).resolve()}"'
            pg_windows.set_autostart(on, command)
        elif mac_app_bundle():
            if on:
                MAC_AGENT.parent.mkdir(parents=True, exist_ok=True)
                MAC_AGENT.write_text(f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.posture-guard.app</string>
    <key>ProgramArguments</key>
    <array><string>/usr/bin/open</string><string>-g</string><string>-a</string><string>{mac_app_bundle()}</string></array>
    <key>RunAtLoad</key><true/>
    <key>LimitLoadToSessionType</key><string>Aqua</string>
</dict>
</plist>
""")
            else:
                MAC_AGENT.unlink(missing_ok=True)
        notify(APP, "Đã bật tự chạy khi mở máy." if on else "Đã tắt tự chạy khi mở máy.")
    except Exception as e:
        notify(APP, f"Không đổi được chế độ tự chạy: {e}")


# ----------------------------------------------------------------------------- tự kiểm tra
def self_test() -> int:
    """
    Kiểm tra app chạy được trên máy này mà không cần camera (dùng khi đóng gói / trên máy ảo CI):
    mô hình nhận diện, đọc danh sách camera, nguồn điện, lớp làm mờ + chấm đích, icon thanh menu / khay.
    In "SELF-TEST OK" và trả về 0 nếu mọi thứ ổn.
    """
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            print(f"  {name}: {fn()}", flush=True)
        except Exception as e:
            ok = False
            print(f"  {name}: LỖI {type(e).__name__}: {e}", flush=True)

    print(f"{APP} {VERSION} – tự kiểm tra trên {platform.system()} {platform.release()} "
          f"(Python {platform.python_version()}, {'app đóng gói' if FROZEN else 'mã nguồn'})", flush=True)
    print("  tiếng Việt: Cúi đầu / Nghiêng đầu / Lệch vai", flush=True)
    check("mô hình đóng gói sẵn", lambda: BUNDLED_MODEL.exists())

    def detect():
        pose = PoseDetector()
        frame = np.full((480, 640, 3), 128, np.uint8)
        res = pose.process(frame)
        pose.close()
        return "chạy được (ảnh trống: không thấy người)" if res is None else "chạy được"
    check("nhận diện tư thế", detect)
    check("camera", lambda: camera_devices() or "không đọc được tên camera (máy không có camera?)")
    check("dùng pin", on_battery)
    check("tự chạy khi mở máy", autostart_state)
    base = {"neck_ratio": 0.8, "head_scale": 0.3, "shoulder_w": 200.0, "roll": 0.0, "sh_tilt": 0.0}
    check("đánh giá tư thế", lambda: assess(dict(base, neck_ratio=0.5), base,
                                            {"drop": 0.15, "forward": 0.12, "lean": 0.18, "tilt_deg": 10}))

    if IS_MAC or IS_WIN:
        blur, menu = ScreenBlur(enabled=True), MenuBar(enabled=True)
        time.sleep(6)  # chờ tiến trình con khởi động (app đóng gói khởi động chậm hơn)
        for name in ("center", "tl", "br", "none"):
            blur.target(name)
            time.sleep(0.3)
        blur.set(0.5)
        menu.update(icon="green", status="Tự kiểm tra", session="", enabled=True, paused_until="",
                    camera="auto", cam_name="(thử)", autostart=autostart_state())
        time.sleep(3)
        blur.set(0)
        time.sleep(1)
        for name, helper in (("lớp làm mờ", blur), ("icon", menu)):
            alive = helper.proc is not None and helper.proc.poll() is None
            if not alive:
                ok = False
                log = helper.LOG.read_text(encoding="utf-8", errors="replace")[-2000:] if helper.LOG.exists() else ""
                print(f"  {name}: LỖI – tiến trình con đã dừng\n{log}", flush=True)
            else:
                print(f"  {name}: đang chạy", flush=True)
        menu.close()
        blur.close()
    print("SELF-TEST OK" if ok else "SELF-TEST FAILED", flush=True)
    return 0 if ok else 1


# ----------------------------------------------------------------------------- rời đi / quay lại
class PresenceTracker:
    """
    Theo dõi lúc rời khỏi màn hình, lúc quay lại, và thời gian ngồi liên tục.
      - Mất dấu dưới `grace` giây (quay người, cúi nhặt đồ, nhận diện chập chờn) vẫn tính là đang ngồi.
      - Thấy người liên tục `confirm` giây mới tính là đã quay lại (tránh người đi ngang qua camera).
      - Vắng mặt từ `break_min` phút trở lên: tính là đã nghỉ, đồng hồ ngồi liên tục về 0.
        Vắng mặt ngắn hơn: đồng hồ dừng lúc vắng mặt, quay lại thì chạy tiếp (không cộng thời gian vắng).
      - Máy ngủ / gập máy (khoảng trống lớn giữa 2 lần cập nhật) được tính là vắng mặt.
      - Tạm dừng theo dõi (suspend): khoảng thời gian tạm dừng không tính là ngồi, cũng không tính là nghỉ.
    update(t, seen) trả về danh sách sự kiện:
      {"type": "left", "at", "sat_min"}                         – đã rời đi (tính từ lần cuối còn thấy)
      {"type": "back", "left_at", "at", "away_min", "is_break", "sat_min"} – đã quay lại
      {"type": "arrived", "at"}                                 – lần đầu thấy người
    """

    def __init__(self, grace=10.0, confirm=3.0, break_min=5.0):
        self.grace, self.confirm, self.break_sec = grace, confirm, break_min * 60
        self.state = "unknown"      # unknown | present | away
        self.sit_sec = 0.0          # thời gian ngồi liên tục từ lần nghỉ gần nhất
        self.last_seen = None
        self.left_at = None
        self.cand_since = None      # lúc bắt đầu thấy lại người khi đang vắng mặt
        self.suspended_at = None
        self.last_break = None      # (rời đi, quay lại) của lần nghỉ gần nhất

    def suspend(self, t) -> None:
        if self.suspended_at is None:
            self.suspended_at = t

    def _resume(self, t) -> None:
        d = t - self.suspended_at
        self.suspended_at = None
        if self.last_seen is not None:
            self.last_seen += d
        if self.left_at is not None:
            self.left_at += d
        self.cand_since = None

    def update(self, t, seen: bool) -> list:
        if self.suspended_at is not None:
            self._resume(t)
        ev = []
        # đang có mặt mà quá `grace` giây không thấy (kể cả do máy ngủ) → đã rời đi từ lần cuối còn thấy
        if self.state == "present" and t - self.last_seen >= self.grace:
            self.state, self.left_at, self.cand_since = "away", self.last_seen, None
            ev.append({"type": "left", "at": self.left_at, "sat_min": self.sit_sec / 60})

        if self.state == "present":
            if seen:
                self.sit_sec += t - self.last_seen  # gồm cả lúc mất dấu ngắn dưới `grace` giây
                self.last_seen = t
        elif seen:
            if self.cand_since is None or t - self.last_seen >= self.grace:
                self.cand_since = t
            self.last_seen = t
            if t - self.cand_since >= self.confirm:
                back = self.cand_since
                if self.state == "unknown":
                    ev.append({"type": "arrived", "at": back})
                    self.sit_sec = t - back
                else:
                    away = back - self.left_at
                    is_break = away >= self.break_sec
                    ev.append({"type": "back", "left_at": self.left_at, "at": back, "away_min": away / 60,
                               "is_break": is_break, "sat_min": self.sit_sec / 60})
                    if is_break:
                        self.sit_sec = 0.0
                        self.last_break = (self.left_at, back)
                    self.sit_sec += t - back
                self.state, self.cand_since = "present", None
        return ev

    @property
    def sit_min(self) -> float:
        return self.sit_sec / 60

    def away_min(self, t) -> float:
        return (t - self.left_at) / 60 if self.state == "away" and self.left_at is not None else 0.0


# ----------------------------------------------------------------------------- nhật ký
PRESENCE_LOG = CONFIG_DIR / "presence_log.csv"


def log_presence(ev: dict) -> None:
    """Mỗi lần rời đi rồi quay lại: một dòng (rời đi, quay lại, vắng bao lâu, có tính là nghỉ, đã ngồi bao lâu)."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    new = not PRESENCE_LOG.exists()
    with PRESENCE_LOG.open("a", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        if new:
            wr.writerow(["left", "back", "away_min", "is_break", "sat_before_min"])
        wr.writerow([datetime.fromtimestamp(ev["left_at"]).isoformat(timespec="seconds"),
                     datetime.fromtimestamp(ev["at"]).isoformat(timespec="seconds"),
                     round(ev["away_min"], 1), int(ev["is_break"]), round(ev["sat_min"], 1)])


def log_minute(stats: dict) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    new = not LOG_FILE.exists()
    with LOG_FILE.open("a", newline="", encoding="utf-8") as f:
        wr = csv.writer(f)
        if new:
            wr.writerow(["time", "present_pct", "bad_pct", "top_issue", "session_min"])
        wr.writerow([datetime.now().isoformat(timespec="seconds"), *stats.values()])


# ----------------------------------------------------------------------------- vòng lặp chính
def main():
    ap = argparse.ArgumentParser(description="Theo dõi tư thế đầu bằng webcam")
    ap.add_argument("--camera", default="auto",
                    help="auto (ưu tiên webcam rời, tự chuyển khi cắm/rút), mac, webcam, hoặc số thứ tự")
    ap.add_argument("--list-cameras", action="store_true")
    ap.add_argument("--preview", action="store_true", help="hiện cửa sổ camera có vẽ khung xương")
    ap.add_argument("--recalibrate", action="store_true", help="hiệu chỉnh lại tư thế chuẩn khi khởi động")
    ap.add_argument("--center-only", action="store_true",
                    help="hiệu chỉnh chỉ nhìn giữa màn hình (mặc định nhìn cả giữa và 4 góc)")
    ap.add_argument("--fps", type=float, default=5, help="số khung hình xử lý mỗi giây khi cắm sạc (thấp = nhẹ máy)")
    ap.add_argument("--battery-fps", type=float, default=2,
                    help="số khung hình xử lý mỗi giây khi dùng pin (macOS), để tiết kiệm pin")
    ap.add_argument("--bad-seconds", type=float, default=15, help="sai tư thế liên tục bao lâu thì nhắc")
    ap.add_argument("--cooldown", type=float, default=90, help="khoảng cách tối thiểu giữa 2 lần nhắc tư thế (giây)")
    ap.add_argument("--sit-minutes", type=float, default=45, help="ngồi liên tục bao lâu thì nhắc nghỉ")
    ap.add_argument("--break-repeat", type=float, default=5, help="nhắc lại nghỉ mỗi N phút nếu chưa đứng dậy")
    ap.add_argument("--min-break", type=float, default=5,
                    help="rời khỏi màn hình bao nhiêu phút thì tính là đã nghỉ (đồng hồ ngồi về 0)")
    ap.add_argument("--away-grace", type=float, default=10,
                    help="không thấy người bao nhiêu giây thì tính là đã rời đi (quay người, cúi nhặt đồ không tính)")
    ap.add_argument("--drop", type=float, default=0.15, help="ngưỡng cúi đầu (tỷ lệ, 0.15 = 15%%)")
    ap.add_argument("--forward", type=float, default=0.12, help="ngưỡng đầu đưa ra trước (tỷ lệ)")
    ap.add_argument("--lean", type=float, default=0.18, help="ngưỡng ngồi sát màn hình (tỷ lệ)")
    ap.add_argument("--tilt", type=float, default=10, help="ngưỡng nghiêng đầu/lệch vai (độ)")
    ap.add_argument("--no-blur", action="store_true", help="tắt làm mờ màn hình, chỉ nhắc bằng thông báo")
    ap.add_argument("--blur-delay", type=float, default=3, help="sai tư thế bao nhiêu giây thì bắt đầu làm mờ")
    ap.add_argument("--blur-ramp", type=float, default=10, help="số giây để mờ từ 0 lên mức tối đa")
    ap.add_argument("--blur-max", type=float, default=0.9, help="độ mờ tối đa (0–1)")
    ap.add_argument("--no-menubar", action="store_true", help="không hiện icon trên thanh menu macOS")
    ap.add_argument("--overlay-helper", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--menubar-helper", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--self-test", action="store_true", help="kiểm tra app chạy được trên máy này (không cần camera)")
    ap.add_argument("--version", action="version", version=f"{APP} {VERSION}")
    args = ap.parse_args()

    if args.overlay_helper:
        if IS_WIN:
            import pg_windows
            pg_windows.run_overlay_helper()
        else:
            run_overlay_helper()
        return
    if args.menubar_helper:
        if IS_WIN:
            import pg_windows
            pg_windows.run_tray_helper()
        else:
            run_menubar_helper()
        return
    if args.self_test:
        sys.exit(self_test())

    if args.list_cameras:
        list_cameras()
        return

    sens = {"drop": args.drop, "forward": args.forward, "lean": args.lean, "tilt_deg": args.tilt}
    cam_spec = args.camera
    cam = resolve_camera(cam_spec)
    if cam is None:
        notify(APP, f"Không tìm thấy camera ('{cam_spec}'). Cắm webcam hoặc kiểm tra camera rồi mở lại app.")
        sys.exit(1)
    # Lần đầu mở, hệ điều hành hỏi quyền dùng camera và lần mở đầu tiên thất bại → chờ tối đa 2 phút
    cap, t0, asked = open_camera(cam[0]), time.time(), False
    while not cap.isOpened() and time.time() - t0 < 120:
        if not asked:
            notify(APP, "Hãy cho phép PostureGuard dùng camera (bấm Cho phép / Allow). "
                        "macOS: System Settings → Privacy & Security → Camera; "
                        "Windows: Settings → Privacy → Camera.")
            asked = True
        time.sleep(3)
        cap.release()
        cap = open_camera(cam[0])
    if not cap.isOpened():
        notify(APP, f"Không mở được {cam[1]}. Kiểm tra quyền Camera hoặc app khác đang dùng camera, rồi mở lại.")
        sys.exit(1)

    pose = PoseDetector()
    blur = ScreenBlur(enabled=not args.no_blur)
    menu = MenuBar(enabled=not args.no_menubar)

    autostart = autostart_state()
    paused = False      # tắt theo dõi (menu Bật/Tắt hoặc phím p)
    pause_until = 0.0   # tạm dừng có hẹn giờ (menu Tạm dừng 30 phút)

    def show(icon, status, session=""):
        """Cập nhật icon + menu trên thanh menu (chỉ gửi khi có thay đổi)."""
        menu.update(icon=icon, status=status, session=session, enabled=not paused,
                    paused_until=f"{datetime.fromtimestamp(pause_until):%H:%M}" if pause_until else "",
                    camera=cam_spec, cam_name=cam[1], autostart=autostart)

    def calib_marker(name):
        """Chấm đích hiệu chỉnh: dùng camera MacBook thì hiện trên màn hình MacBook, webcam thì màn hình lớn nhất."""
        kind = next((k for _, n, k in camera_devices() if n == cam[1]), "")
        return blur.target(name, "mac" if kind == "mac" else "")

    def stop_requested():
        return menu.quit_requested

    def load_baseline(recalibrate=False):
        """Mỗi camera có tư thế chuẩn riêng (camera MacBook và webcam nhìn từ góc khác nhau)."""
        path = baseline_file(cam[2])
        if path.exists() and not recalibrate:
            print(f"Dùng {cam[1]} – đã nạp tư thế chuẩn từ {path}")
            return json.loads(path.read_text())
        print(f"Dùng {cam[1]} – chưa có tư thế chuẩn cho camera này, cần hiệu chỉnh.")
        presence.suspend(time.time())  # lúc hiệu chỉnh không tính là rời đi
        blur.set(0)
        show("grey", "Đang hiệu chỉnh tư thế chuẩn...")
        b = None
        while b is None:
            b = calibrate(cap, pose, path, preview=args.preview, stop=stop_requested,
                          marker=calib_marker, corners=not args.center_only)
            if stop_requested():
                raise KeyboardInterrupt
            if b is None:
                time.sleep(3)
        return b

    def read_key():
        """Phím trong cửa sổ preview → cùng lệnh với menu."""
        key = cv2.waitKey(1) & 0xFF
        return {ord("q"): ["quit"], ord("p"): ["toggle"], ord("c"): ["calibrate"]}.get(key, [])

    has_names = bool(camera_devices())  # đọc được tên camera (macOS) → tự chuyển khi cắm/rút
    last_cam_check = time.time()
    fail_since = None

    ema = EMA()
    battery = on_battery()
    period = 1.0 / (args.battery_fps if battery else args.fps)
    last_power_check = time.time()
    print(f"Nguồn điện: {'pin' if battery else 'sạc'} – xử lý {1 / period:g} khung hình/giây")
    base = None
    want_calibrate = args.recalibrate
    keys = []
    bad_since = None
    last_posture_alert = 0.0
    presence = PresenceTracker(grace=args.away_grace, break_min=args.min_break)
    last_break_alert = 0.0
    minute = {"t": time.time(), "n": 0, "present": 0, "bad": 0, "issues": {}}
    print("Đang theo dõi... (Ctrl+C hoặc menu Thoát để thoát)")

    try:
        while True:
            loop_t = time.time()
            switch_to = None

            # --- rút / cắm sạc: đổi số khung hình xử lý mỗi giây (kiểm tra mỗi 30 giây)
            if loop_t - last_power_check >= 30:
                last_power_check = loop_t
                if on_battery() != battery:
                    battery = not battery
                    period = 1.0 / (args.battery_fps if battery else args.fps)
                    print(f"[{datetime.now():%H:%M:%S}] Chuyển sang {'pin' if battery else 'sạc'} – "
                          f"xử lý {1 / period:g} khung hình/giây", flush=True)

            # --- lệnh từ thanh menu / phím tắt preview
            cmds, keys = keys + menu.poll(), []
            if "quit" in cmds:
                break
            for cmd in cmds:
                if cmd == "toggle":
                    paused, pause_until = not paused, 0.0
                    print("Đã tắt theo dõi." if paused else "Đã bật theo dõi.", flush=True)
                elif cmd == "pause":
                    pause_until = loop_t + 30 * 60
                    print(f"Tạm dừng đến {datetime.fromtimestamp(pause_until):%H:%M}.", flush=True)
                elif cmd == "resume":
                    paused, pause_until = False, 0.0
                    print("Tiếp tục theo dõi.", flush=True)
                elif cmd == "calibrate":
                    paused, pause_until, want_calibrate = False, 0.0, True
                elif cmd == "autostart":
                    toggle_autostart()
                    autostart = autostart_state()
                elif cmd.startswith("camera "):
                    spec = cmd.split(" ", 1)[1]
                    new = resolve_camera(spec)
                    if new is None:
                        notify(APP, "Không thấy webcam rời." if spec == "webcam" else "Không thấy camera MacBook.")
                    else:
                        cam_spec = spec
                        if new[2] != cam[2]:
                            switch_to = new
            if pause_until and loop_t >= pause_until:
                pause_until = 0.0
                notify(APP, "Hết giờ tạm dừng – tiếp tục theo dõi.")

            # --- cắm / rút webcam: tự chuyển camera (kiểm tra mỗi 5 giây, hoặc ngay khi camera hiện tại mất tín hiệu)
            if (has_names and not cam_spec.isdigit() and not switch_to
                    and (loop_t - last_cam_check >= 5 or (fail_since and loop_t - fail_since > 2))):
                last_cam_check = loop_t
                new = resolve_camera(cam_spec)
                if new and (new[2] != cam[2] or fail_since):
                    switch_to = new
            if switch_to:
                if cap is not None:
                    cap.release()
                    cap = None
                cam, base = switch_to, None  # tư thế chuẩn của camera mới nạp lại khi bắt đầu đọc
                notify(APP, f"Chuyển sang {cam[1]}")
                ema.reset()
                bad_since = None
                fail_since = None

            # --- tắt / tạm dừng: nhả camera (đèn camera tắt), màn hình trong lại
            if paused or pause_until:
                presence.suspend(loop_t)
                if cap is not None:
                    cap.release()
                    cap = None
                bad_since = None
                fail_since = None
                blur.set(0)
                show("grey", "Đang tắt theo dõi" if paused
                     else f"Tạm dừng đến {datetime.fromtimestamp(pause_until):%H:%M}")
                if args.preview:
                    frame = np.zeros((480, 640, 3), np.uint8)
                    cv2.putText(frame, "PAUSED - bam p de tiep tuc", (10, 30),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 2)
                    cv2.imshow(APP, frame)
                    keys = read_key()
                time.sleep(max(0.0, period - (time.time() - loop_t)))
                continue

            if cap is None:
                cap = open_camera(cam[0])
                ema.reset()
            if base is None:
                base = load_baseline(want_calibrate)
                want_calibrate = False
            elif want_calibrate:
                want_calibrate = False
                presence.suspend(time.time())  # lúc hiệu chỉnh không tính là rời đi
                blur.set(0)
                show("grey", "Đang hiệu chỉnh tư thế chuẩn...")
                new = calibrate(cap, pose, baseline_file(cam[2]), preview=args.preview, stop=stop_requested,
                                marker=calib_marker, corners=not args.center_only)
                if new:
                    base = new
                    ema.reset()
                continue

            ok, frame = cap.read()
            fail_since = None if ok else (fail_since or loop_t)
            if not ok:
                show("grey", f"Không đọc được {cam[1]}")
                time.sleep(0.5)
                continue
            now = time.time()
            h, w = frame.shape[:2]
            issues, m = [], None

            lms = pose.process(frame)
            if lms:
                m = compute_metrics(lms, w, h)
                if args.preview:
                    draw_skeleton(frame, lms)

            minute["n"] += 1
            # --- rời đi / quay lại: thấy người là đủ (kể cả quay nghiêng, chưa thấy rõ hai vai)
            for ev in presence.update(now, lms is not None):
                hm = lambda ts: datetime.fromtimestamp(ts).strftime("%H:%M")
                if ev["type"] == "left":
                    print(f"[{hm(ev['at'])}] Rời khỏi màn hình (đã ngồi liên tục {ev['sat_min']:.0f} phút)", flush=True)
                elif ev["type"] == "arrived":
                    print(f"[{hm(ev['at'])}] Bắt đầu ngồi", flush=True)
                elif ev["type"] == "back":
                    log_presence(ev)
                    ema.reset()
                    away = ev["away_min"]
                    away_txt = f"{away:.0f} phút" if away >= 1 else f"{away * 60:.0f} giây"
                    print(f"[{hm(ev['at'])}] Quay lại sau {away_txt} (rời đi lúc {hm(ev['left_at'])}) – "
                          + ("tính là đã nghỉ, đồng hồ ngồi bắt đầu lại" if ev["is_break"]
                             else f"chưa đủ {args.min_break:g} phút nên chưa tính là nghỉ, "
                                  f"đồng hồ ngồi chạy tiếp từ {presence.sit_min:.0f} phút"), flush=True)
                    if last_break_alert:  # đã được nhắc nghỉ: cho biết lần rời đi này có đủ chưa
                        if ev["is_break"]:
                            notify("Nghỉ tốt lắm", f"Bạn đã nghỉ {away_txt}. Đồng hồ ngồi bắt đầu lại.")
                        else:
                            notify("Nghỉ chưa đủ", f"Bạn mới rời đi {away_txt}; nên nghỉ ít nhất "
                                                  f"{args.min_break:g} phút. Sẽ nhắc lại sau.")
                            last_break_alert = now  # "nhắc lại sau" tính từ lúc này, không báo dồn 2 lần
                    if ev["is_break"]:
                        last_break_alert = 0.0
            if m:
                # --- có người ngồi, thấy rõ đầu và vai
                minute["present"] += 1
                issues = assess(ema(m), base, sens)

                if issues:
                    minute["bad"] += 1
                    for i in issues:
                        minute["issues"][i] = minute["issues"].get(i, 0) + 1
                    bad_since = bad_since or now
                    # đã làm mờ màn hình thì không cần thêm thông báo
                    if (not blur.active and now - bad_since >= args.bad_seconds
                            and now - last_posture_alert >= args.cooldown):
                        notify("Chỉnh tư thế", " · ".join(issues))
                        last_posture_alert = now
                else:
                    bad_since = None

            # --- ngồi quá lâu: chỉ tính thời gian thực sự ngồi trước màn hình
            if presence.state == "present":
                sat_min = presence.sit_min
                if sat_min >= args.sit_minutes and now - last_break_alert >= args.break_repeat * 60:
                    notify("Đến giờ nghỉ", f"Đã ngồi liên tục {sat_min:.0f} phút. Đứng dậy, rời khỏi màn hình "
                                           f"ít nhất {args.min_break:g} phút, vận động cổ vai.")
                    last_break_alert = now
            if not m:
                bad_since = None

            # --- ghi nhật ký mỗi phút
            if now - minute["t"] >= 60 and minute["n"]:
                top = max(minute["issues"], key=minute["issues"].get) if minute["issues"] else ""
                sess = presence.sit_min if presence.state == "present" else 0
                log_minute({
                    "present_pct": round(100 * minute["present"] / minute["n"]),
                    "bad_pct": round(100 * minute["bad"] / max(minute["present"], 1)),
                    "top_issue": top,
                    "session_min": round(sess, 1),
                })
                minute = {"t": now, "n": 0, "present": 0, "bad": 0, "issues": {}}

            # --- làm mờ màn hình dần khi sai tư thế kéo dài, trong lại ngay khi ngồi đúng / rời đi
            if bad_since:
                blur.set(args.blur_max * min(max((now - bad_since - args.blur_delay) / args.blur_ramp, 0.0), 1.0))
            else:
                blur.set(0)

            # --- icon thanh menu: xanh = tốt, đỏ = sai, xám = không thấy người
            sat = ""
            if presence.state == "present":
                sat = f"Ngồi liên tục {presence.sit_min:.0f} phút"
                if presence.last_break:
                    lb_left, lb_back = presence.last_break
                    sat += (f" · nghỉ gần nhất {datetime.fromtimestamp(lb_left):%H:%M} "
                            f"({(lb_back - lb_left) / 60:.0f} phút)")
            if presence.state == "away":
                away = presence.away_min(now)
                show("grey", f"Đã rời đi {away:.0f} phút" + (" – đã đủ giờ nghỉ" if away >= args.min_break else ""),
                     f"Rời đi lúc {datetime.fromtimestamp(presence.left_at):%H:%M}, "
                     f"trước đó ngồi {presence.sit_min:.0f} phút")
            elif not m:
                show("grey", "Không thấy rõ đầu và vai", sat)
            elif issues:
                show("red", "Sai tư thế: " + ", ".join(issues), sat)
            else:
                show("green", "Tư thế tốt", sat)

            if args.preview:
                color = (0, 0, 255) if issues else (0, 200, 0)
                status = "; ".join(issues) if issues else ("OK" if m else "Khong thay nguoi")
                cv2.putText(frame, status, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)
                if presence.state == "present":
                    cv2.putText(frame, f"Ngoi lien tuc: {presence.sit_min:.0f} phut", (10, 60),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
                cv2.imshow(APP, frame)
                keys = read_key()

            time.sleep(max(0.0, period - (time.time() - loop_t)))
    except KeyboardInterrupt:
        pass
    finally:
        menu.close()
        blur.close()
        if cap is not None:
            cap.release()
        pose.close()
        cv2.destroyAllWindows()
        print("Đã thoát. Nhật ký:", LOG_FILE)


if __name__ == "__main__":
    setup_output()
    main()
