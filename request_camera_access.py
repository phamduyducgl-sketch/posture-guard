"""Xin quyền Camera của macOS và chờ người dùng trả lời.

OpenCV chỉ gửi yêu cầu rồi báo lỗi ngay, tiến trình thoát trước khi hộp thoại kịp hiện.
App bọc (install_autostart.sh --app) chạy file này trước posture_guard.py để hộp thoại
"PostureGuard muốn truy cập camera" ở lại cho tới khi bấm Cho phép / Từ chối.
Thoát với mã 0 nếu đã có quyền, 1 nếu bị từ chối hoặc hết giờ chờ.
"""
import sys
import time

import AVFoundation as AV
from Foundation import NSDate, NSRunLoop

STATUS = {0: "chưa hỏi", 1: "bị hạn chế", 2: "bị từ chối", 3: "đã cho phép"}
TIMEOUT = 120  # giây chờ người dùng bấm


def main():
    status = AV.AVCaptureDevice.authorizationStatusForMediaType_(AV.AVMediaTypeVideo)
    print(f"Quyền Camera: {STATUS.get(status, status)}", file=sys.stderr, flush=True)
    if status == 3:
        return 0
    if status != 0:
        print("Bật lại trong System Settings → Privacy & Security → Camera.", file=sys.stderr)
        return 1

    answer = []
    AV.AVCaptureDevice.requestAccessForMediaType_completionHandler_(
        AV.AVMediaTypeVideo, lambda granted: answer.append(bool(granted)))
    deadline = time.time() + TIMEOUT
    while not answer and time.time() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.2))
    granted = bool(answer and answer[0])
    print("Đã được cấp quyền Camera." if granted else "Chưa được cấp quyền Camera.",
          file=sys.stderr, flush=True)
    return 0 if granted else 1


if __name__ == "__main__":
    sys.exit(main())
