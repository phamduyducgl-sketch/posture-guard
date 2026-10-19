"""
Phần riêng cho Windows của PostureGuard: lớp làm tối màn hình + chấm đích hiệu chỉnh (tkinter),
icon ở khay hệ thống (pystray), tên camera (pygrabber/DirectShow), nguồn điện, tự chạy khi mở máy.
Giao tiếp với tiến trình chính giống hệt bản macOS (xem run_overlay_helper / run_menubar_helper
trong posture_guard.py), nên vòng lặp chính không cần biết đang chạy trên hệ điều hành nào.
"""
import ctypes
import json
import queue
import sys
import threading
from ctypes import wintypes

APP = "PostureGuard"


# ----------------------------------------------------------------------------- màn hình
def _user32():
    u = ctypes.windll.user32
    try:  # tọa độ theo điểm ảnh thật, kể cả khi Windows phóng to giao diện 125%/150%
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            u.SetProcessDPIAware()
        except Exception:
            pass
    return u


def _monitors(user32):
    """[(left, top, right, bottom, work_left, work_top, work_right, work_bottom, là màn hình chính?)]"""
    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    out = []
    proc_type = ctypes.WINFUNCTYPE(ctypes.c_int, wintypes.HMONITOR, wintypes.HDC,
                                   ctypes.POINTER(wintypes.RECT), wintypes.LPARAM)

    def cb(hmon, _hdc, _rect, _data):
        mi = MONITORINFO()
        mi.cbSize = ctypes.sizeof(MONITORINFO)
        user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
        m, w = mi.rcMonitor, mi.rcWork
        out.append((m.left, m.top, m.right, m.bottom, w.left, w.top, w.right, w.bottom, bool(mi.dwFlags & 1)))
        return 1

    user32.EnumDisplayMonitors(None, None, proc_type(cb), 0)
    return out


def _click_through(user32, widget):
    """Cửa sổ không nhận chuột/bàn phím, không hiện trên thanh tác vụ, không chiếm focus."""
    hwnd = user32.GetParent(widget.winfo_id()) or widget.winfo_id()
    GWL_EXSTYLE = -20
    ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    ex |= 0x80000 | 0x20 | 0x80 | 0x08000000  # LAYERED | TRANSPARENT | TOOLWINDOW | NOACTIVATE
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)


def run_overlay_helper() -> None:
    """
    Tiến trình con: lớp tối phủ toàn bộ các màn hình + chấm đỏ khi hiệu chỉnh.
    stdin: "<số 0..1>" = độ mờ; "target <center|tl|tr|bl|br|none> [mac]" = chấm đích
    ("mac" = camera tích hợp của laptop → chấm hiện trên màn hình chính, ngược lại màn hình lớn nhất).
    Windows không có hiệu ứng làm mờ hệ thống như macOS, nên dùng lớp tối bán trong suốt.
    """
    import math
    import tkinter as tk

    user32 = _user32()
    root = tk.Tk()
    root.withdraw()

    def virtual_screen():
        return tuple(user32.GetSystemMetrics(i) for i in (76, 77, 78, 79))  # x, y, rộng, cao

    dim = tk.Toplevel(root)
    dim.overrideredirect(True)
    dim.configure(bg="black")
    dim.attributes("-topmost", True)
    dim.attributes("-alpha", 0.0)
    vs = virtual_screen()
    dim.geometry(f"{vs[2]}x{vs[3]}+{vs[0]}+{vs[1]}")
    dim.update_idletasks()
    _click_through(user32, dim)
    dim.withdraw()  # chỉ hiện khi cần mờ

    size = 120
    key = "#ff00ff"  # màu trong suốt của cửa sổ chấm đích
    dot = tk.Toplevel(root)
    dot.overrideredirect(True)
    dot.configure(bg=key)
    dot.attributes("-topmost", True)
    dot.attributes("-transparentcolor", key)
    canvas = tk.Canvas(dot, width=size, height=size, bg=key, highlightthickness=0)
    canvas.create_oval(4, 4, size - 4, size - 4, fill="#e61a1a", outline="white", width=6)
    canvas.pack()
    dot.update_idletasks()
    _click_through(user32, dot)
    dot.withdraw()

    st = {"target": 0.0, "cur": 0.0, "t": 0, "vs": vs, "dot": False}
    cmds = queue.Queue()

    def read_stdin():
        for line in sys.stdin:
            cmds.put(line.strip())
        cmds.put(None)

    def show_target(name, where):
        if name not in ("center", "tl", "tr", "bl", "br"):
            dot.withdraw()
            st["dot"] = False
            return
        mons = _monitors(user32)
        if not mons:
            return
        if where == "mac":
            mon = next((m for m in mons if m[8]), mons[0])
        else:
            mon = max(mons, key=lambda m: (m[2] - m[0]) * (m[3] - m[1]))
        wl, wt, wr, wb = mon[4:8]  # vùng làm việc: chừa thanh tác vụ
        margin = 24
        left, right = wl + margin, wr - size - margin
        top, bottom = wt + margin, wb - size - margin
        x = {"center": (left + right) // 2, "tl": left, "bl": left, "tr": right, "br": right}[name]
        y = {"center": (top + bottom) // 2, "tl": top, "tr": top, "bl": bottom, "br": bottom}[name]
        dot.geometry(f"{size}x{size}+{x}+{y}")
        dot.deiconify()
        dot.lift()
        st["dot"] = True

    def tick():
        while not cmds.empty():
            line = cmds.get_nowait()
            if line is None:
                root.quit()
                return
            if line.startswith("target "):
                parts = line.split()
                show_target(parts[1], parts[2] if len(parts) > 2 else "")
                continue
            try:
                st["target"] = min(max(float(line), 0.0), 1.0)
            except ValueError:
                pass
        st["t"] += 1
        if st["t"] % 30 == 0:  # mỗi giây: đổi màn hình → phủ lại cho khớp; giữ lớp phủ luôn ở trên cùng
            vs = virtual_screen()
            if vs != st["vs"]:
                st["vs"] = vs
                dim.geometry(f"{vs[2]}x{vs[3]}+{vs[0]}+{vs[1]}")
            if st["cur"] > 0:
                dim.lift()
        if st["dot"]:
            dot.attributes("-alpha", 0.65 + 0.35 * math.sin(st["t"] / 5))
        cur, tgt = st["cur"], st["target"]
        step = 0.02 if tgt > cur else 0.08  # tối dần chậm, sáng lại nhanh
        cur = min(tgt, cur + step) if tgt > cur else max(tgt, cur - step)
        if cur != st["cur"]:
            if st["cur"] == 0 and cur > 0:
                dim.deiconify()
                dim.lift()
            st["cur"] = cur
            dim.attributes("-alpha", 0.75 * cur)  # tối tối đa 75%: vẫn lờ mờ thấy màn hình
            if cur == 0:
                dim.withdraw()
        root.after(33, tick)

    threading.Thread(target=read_stdin, daemon=True).start()
    root.after(33, tick)
    root.mainloop()


# ----------------------------------------------------------------------------- khay hệ thống
def run_tray_helper() -> None:
    """
    Tiến trình con: icon PostureGuard ở khay hệ thống (góc dưới bên phải).
    stdin : mỗi dòng một JSON trạng thái (giống icon thanh menu macOS).
    stdout: mỗi dòng một lệnh khi bấm menu: toggle | pause | resume | calibrate | camera <...> | autostart | quit.
    """
    import pystray
    from PIL import Image, ImageDraw

    def make_icon(rgb, filled=True):
        img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if filled:
            d.ellipse((6, 6, 58, 58), fill=rgb + (255,), outline=(255, 255, 255, 255), width=3)
        else:  # vòng rỗng: dễ phân biệt với xanh/đỏ kể cả với người mù màu
            d.ellipse((8, 8, 56, 56), outline=rgb + (255,), width=8)
        return img

    icons = {"green": make_icon((51, 199, 89)), "red": make_icon((255, 59, 48)),
             "grey": make_icon((140, 140, 140), False)}
    st = {"status": "Đang khởi động...", "enabled": True}

    def send(cmd):
        try:
            sys.stdout.write(cmd + "\n")
            sys.stdout.flush()
        except (OSError, ValueError):
            tray.stop()

    def _noop(_icon, _item):  # dòng thông tin, không bấm được
        pass

    def act(cmd):
        return lambda _icon, _item: send(cmd)

    def cam_item(key, label):
        return pystray.MenuItem(label, act(f"camera {key}"), checked=lambda _i: st.get("camera") == key,
                                radio=True)

    menu = pystray.Menu(
        pystray.MenuItem(lambda _i: st.get("status", ""), _noop, enabled=False),
        pystray.MenuItem(lambda _i: st.get("session") or "", _noop, enabled=False,
                         visible=lambda _i: bool(st.get("session"))),
        pystray.MenuItem(lambda _i: f"Camera: {st.get('cam_name', '')}", _noop, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(lambda _i: "Tắt theo dõi" if st.get("enabled", True) else "Bật theo dõi", act("toggle")),
        pystray.MenuItem(lambda _i: (f"Tiếp tục ngay (đang tạm dừng đến {st['paused_until']})"
                                     if st.get("paused_until") else "Tạm dừng 30 phút"),
                         lambda _icon, _item: send("resume" if st.get("paused_until") else "pause"),
                         enabled=lambda _i: st.get("enabled", True)),
        pystray.MenuItem("Hiệu chỉnh lại", act("calibrate")),
        pystray.MenuItem("Chọn camera", pystray.Menu(
            cam_item("auto", "Tự động"), cam_item("mac", "Camera laptop"), cam_item("webcam", "Webcam rời"))),
        pystray.MenuItem("Tự chạy khi mở máy", act("autostart"), checked=lambda _i: bool(st.get("autostart")),
                         visible=lambda _i: st.get("autostart") is not None),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Thoát PostureGuard", act("quit")),
    )
    tray = pystray.Icon(APP, icons["grey"], APP, menu)

    def read_stdin():
        for line in sys.stdin:
            try:
                st.update(json.loads(line))
            except ValueError:
                continue
            tray.icon = icons.get(st.get("icon"), icons["grey"])
            tray.title = f"{APP}: {st.get('status', '')}"[:127]  # Windows giới hạn chú thích 127 ký tự
            tray.update_menu()
        tray.stop()

    def setup(icon):
        icon.visible = True
        threading.Thread(target=read_stdin, daemon=True).start()

    tray.run(setup)


# ----------------------------------------------------------------------------- camera
_PHONE = ("iphone", "ipad", "droidcam", "iriun", "camo", "epoccam", "phone", "android", "ivcam")
_VIRTUAL = ("obs", "virtual", "snap camera", "xsplit", "manycam", "broadcast", "splitcam", "screen")
_IR = (" ir ", "ir camera", "infrared", "hello")
_BUILTIN = ("integrated", "built-in", "builtin", "internal", "truevision", "user facing", "front")


def camera_devices():
    """
    [(chỉ số OpenCV, tên, loại)] theo thứ tự DirectShow (cùng thứ tự với cv2.CAP_DSHOW).
    loại: "mac" (camera tích hợp của laptop), "webcam", "phone" (camera điện thoại / ảo / hồng ngoại:
    chế độ auto không tự chọn). Trả về [] nếu không đọc được tên camera.
    """
    try:
        from pygrabber.dshow_graph import FilterGraph
        names = FilterGraph().get_input_devices()
    except Exception:
        return []
    kinds = []
    for n in names:
        low = f" {n.lower()} "
        if any(k in low for k in _PHONE + _VIRTUAL + _IR):
            kinds.append("phone")
        elif any(k in low for k in _BUILTIN):
            kinds.append("mac")
        else:
            kinds.append("webcam")
    real = [i for i, k in enumerate(kinds) if k != "phone"]
    # Nhiều laptop đặt tên camera tích hợp chung chung ("HD Webcam"): nếu có từ 2 camera thật mà không
    # nhận ra cái nào là tích hợp, coi camera thật đầu tiên (Windows liệt kê camera tích hợp trước) là tích hợp.
    if len(real) >= 2 and not any(kinds[i] == "mac" for i in real):
        kinds[real[0]] = "mac"
    return [(i, n, k) for i, (n, k) in enumerate(zip(names, kinds))]


# ----------------------------------------------------------------------------- nguồn điện
def on_battery() -> bool:
    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [("ACLineStatus", ctypes.c_ubyte), ("BatteryFlag", ctypes.c_ubyte),
                    ("BatteryLifePercent", ctypes.c_ubyte), ("SystemStatusFlag", ctypes.c_ubyte),
                    ("BatteryLifeTime", wintypes.DWORD), ("BatteryFullLifeTime", wintypes.DWORD)]

    s = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(s)):
        return False
    return s.ACLineStatus == 0  # 0 = đang dùng pin, 1 = cắm sạc, 255 = không rõ (máy bàn)


# ----------------------------------------------------------------------------- tự chạy khi mở máy
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_enabled() -> bool:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on: bool, command: str) -> None:
    """Ghi/xóa mục PostureGuard trong HKCU\\...\\Run (cùng mục mà bộ cài đặt tạo ra)."""
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass
