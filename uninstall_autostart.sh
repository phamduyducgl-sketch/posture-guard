#!/bin/bash
# Gỡ LaunchAgent của PostureGuard: dừng bản đang chạy ngầm, xoá plist và app bọc (nếu có).
# Không xoá ~/.posture_guard (tư thế chuẩn, nhật ký).
set -euo pipefail

LABEL="com.posture-guard"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
APP="$HOME/Applications/PostureGuard.app"
DOMAIN="gui/$(id -u)"

# Dừng app bọc đang chạy: launchctl bootout chỉ dừng lệnh `open`, còn app do LaunchServices
# mở nên vẫn chạy. Gửi Ctrl+C (SIGINT) cho Python con của applet để app tự dọn dẹp rồi thoát.
stop_app() {
    local applet pids
    applet="$(pgrep -f "$APP/Contents/MacOS/applet" || true)"
    [ -n "$applet" ] || return 0
    pids="$(pgrep -P "$applet" | tr '\n' ' ' || true)"
    [ -n "$pids" ] && kill -INT $pids 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        kill -0 $applet 2>/dev/null || return 0
        sleep 1
    done
    kill -TERM $applet $pids 2>/dev/null || true
}

if launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; then
    launchctl bootout "$DOMAIN/$LABEL" || launchctl unload "$PLIST" 2>/dev/null || true
    echo "Đã dừng và gỡ $LABEL khỏi launchd."
else
    echo "$LABEL không được nạp trong launchd."
fi

if [ -f "$PLIST" ]; then
    rm -f "$PLIST"
    echo "Đã xoá $PLIST"
fi

if [ -d "$APP" ]; then
    stop_app
    rm -rf "$APP"
    echo "Đã xoá app bọc $APP"
fi

echo "PostureGuard sẽ không tự chạy khi đăng nhập nữa. Dữ liệu trong ~/.posture_guard vẫn giữ nguyên."
