# -*- mode: python ; coding: utf-8 -*-
# Đóng gói PostureGuard bằng PyInstaller (chạy từ thư mục gốc của dự án):
#     pyinstaller packaging/PostureGuard.spec
# Cần có sẵn pose_landmarker_lite.task ở thư mục gốc (GitHub Actions tự tải về).
import sys
from PyInstaller.utils.hooks import collect_all

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform == "win32"
ROOT = SPECPATH + "/.."

datas, binaries, hiddenimports = [(ROOT + "/pose_landmarker_lite.task", ".")], [], ["pg_windows"]
packages = ["mediapipe"]
if IS_MAC:
    packages += ["objc", "Foundation", "AppKit", "AVFoundation", "Quartz", "PyObjCTools"]
if IS_WIN:
    packages += ["pystray", "pygrabber", "plyer", "comtypes"]
    hiddenimports += ["plyer.platforms.win.notification", "tkinter", "PIL._tkinter_finder"]
for pkg in packages:
    d, b, h = collect_all(pkg)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    [ROOT + "/posture_guard.py"],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pandas", "scipy", "IPython", "jax", "torch", "tensorflow"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="PostureGuard",
    console=False,  # không bật cửa sổ dòng lệnh; nhật ký ghi ở ~/.posture_guard/app.log
    icon=ROOT + "/packaging/icon.ico" if IS_WIN else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="PostureGuard")

if IS_MAC:
    app = BUNDLE(
        coll,
        name="PostureGuard.app",
        icon=ROOT + "/packaging/icon.icns",
        bundle_identifier="io.github.phamduyducgl-sketch.postureguard",
        info_plist={
            "CFBundleDisplayName": "PostureGuard",
            "CFBundleShortVersionString": "1.0.0",
            "LSUIElement": True,  # chỉ có icon trên thanh menu, không có icon trên Dock
            "LSMinimumSystemVersion": "12.0",
            "NSCameraUsageDescription": "PostureGuard dùng camera để theo dõi tư thế đầu–cổ. "
                                        "Hình ảnh chỉ được xử lý trên máy, không lưu và không gửi đi đâu.",
        },
    )
