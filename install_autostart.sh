#!/bin/bash
# Cài LaunchAgent để PostureGuard tự chạy ngầm mỗi khi đăng nhập macOS.
#
#   ./install_autostart.sh            chạy qua app bọc ~/Applications/PostureGuard.app (mặc định,
#                                     để macOS hỏi quyền Camera cho "PostureGuard")
#   ./install_autostart.sh --direct   launchd chạy thẳng .venv/bin/python, không có app bọc
#                                     (chỉ dùng khi Python đã có quyền Camera, xem README mục 4)
#   ./install_autostart.sh --dry-run  chỉ in plist ra màn hình, không cài gì
#
# Gỡ: ./uninstall_autostart.sh
set -euo pipefail

LABEL="com.posture-guard"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
SCRIPT="$PROJECT_DIR/posture_guard.py"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG_DIR="$HOME/.posture_guard"
OUT_LOG="$LOG_DIR/launchd.out.log"
ERR_LOG="$LOG_DIR/launchd.err.log"
APP="$HOME/Applications/PostureGuard.app"
APP_ARGS=(--camera auto)

MODE="app"
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --app) MODE="app" ;;
        --direct) MODE="python" ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '2,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Tham số không hợp lệ: $arg (xem --help)" >&2; exit 2 ;;
    esac
done

[ "$(uname)" = "Darwin" ] || { echo "Script này chỉ dùng cho macOS." >&2; exit 1; }
[ -x "$PYTHON" ] || { echo "Không thấy $PYTHON. Tạo venv trước (README mục 1)." >&2; exit 1; }
[ -f "$SCRIPT" ] || { echo "Không thấy $SCRIPT." >&2; exit 1; }
[ "$MODE" = "python" ] || [ -f "$PROJECT_DIR/request_camera_access.py" ] \
    || { echo "Không thấy request_camera_access.py (cần cho app bọc)." >&2; exit 1; }

xml_escape() { sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g' <<<"$1"; }

# Phần chạy chương trình: chạy thẳng Python, hoặc `open -W` app bọc (open chờ app thoát
# để launchd vẫn theo dõi được tiến trình).
if [ "$MODE" = "app" ]; then
    PROGRAM_ARGS=(/usr/bin/open -W -g -a "$APP")
else
    PROGRAM_ARGS=("$PYTHON" "$SCRIPT" "${APP_ARGS[@]}")
fi

render_plist() {
    cat <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
EOF
    for a in "${PROGRAM_ARGS[@]}"; do
        echo "        <string>$(xml_escape "$a")</string>"
    done
    cat <<EOF
    </array>
    <key>WorkingDirectory</key>
    <string>$(xml_escape "$PROJECT_DIR")</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
    <!-- chạy ngay khi đăng nhập -->
    <key>RunAtLoad</key>
    <true/>
    <!-- chỉ chạy lại khi thoát bất thường (lỗi, crash); bấm "Thoát PostureGuard" thì không -->
    <key>KeepAlive</key>
    <dict>
        <key>SuccessfulExit</key>
        <false/>
    </dict>
    <!-- chờ ít nhất 60 giây giữa 2 lần chạy lại, tránh lặp liên tục khi camera bị chặn -->
    <key>ThrottleInterval</key>
    <integer>60</integer>
    <!-- cần phiên đăng nhập có giao diện (thanh menu, lớp làm mờ, quyền Camera) -->
    <key>LimitLoadToSessionType</key>
    <string>Aqua</string>
    <key>ProcessType</key>
    <string>Interactive</string>
    <key>StandardOutPath</key>
    <string>$(xml_escape "$OUT_LOG")</string>
    <key>StandardErrorPath</key>
    <string>$(xml_escape "$ERR_LOG")</string>
</dict>
</plist>
EOF
}

if [ "$DRY_RUN" = 1 ]; then
    render_plist
    exit 0
fi

# App bọc: applet AppleScript chạy Python ở tiền cảnh. macOS tính quyền Camera cho app này
# (tên "PostureGuard") thay vì cho Python, nên hộp thoại xin quyền hiện ra đúng cách.
build_app() {
    local q_dir q_cmd tmp
    q_dir="'${PROJECT_DIR//\'/\'\\\'\'}'"
    local logs=">> \"\$HOME/.posture_guard/launchd.out.log\" 2>> \"\$HOME/.posture_guard/launchd.err.log\""
    # xin quyền Camera và chờ trả lời trước (OpenCV không chờ), rồi mới chạy app
    q_cmd="cd $q_dir && ./.venv/bin/python request_camera_access.py $logs; exec ./.venv/bin/python posture_guard.py ${APP_ARGS[*]} $logs"
    # escape cho chuỗi AppleScript
    q_cmd="${q_cmd//\\/\\\\}"
    q_cmd="${q_cmd//\"/\\\"}"
    tmp="$(mktemp -t posture_guard_app).applescript"
    cat >"$tmp" <<EOF
on run
    try
        do shell script "$q_cmd"
    end try
end run
EOF
    mkdir -p "$(dirname "$APP")"
    rm -rf "$APP"
    osacompile -o "$APP" "$tmp"
    rm -f "$tmp"
    local info="$APP/Contents/Info.plist"
    # ẩn khỏi Dock, mô tả lý do xin quyền Camera
    /usr/libexec/PlistBuddy -c "Add :LSUIElement bool true" "$info" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Set :LSUIElement true" "$info"
    local cam_desc="PostureGuard dùng camera để theo dõi tư thế, xử lý hoàn toàn trên máy."
    /usr/libexec/PlistBuddy -c "Set :NSCameraUsageDescription $cam_desc" "$info" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :NSCameraUsageDescription string $cam_desc" "$info"
    /usr/libexec/PlistBuddy -c "Set :CFBundleIdentifier $LABEL.launcher" "$info" 2>/dev/null \
        || /usr/libexec/PlistBuddy -c "Add :CFBundleIdentifier string $LABEL.launcher" "$info"
    /usr/libexec/PlistBuddy -c "Set :CFBundleName PostureGuard" "$info" 2>/dev/null || true
    codesign --force --deep --sign - "$APP" >/dev/null 2>&1 || true
    echo "Đã tạo app bọc: $APP"
}

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

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

DOMAIN="gui/$(id -u)"
# gỡ bản cũ (nếu có) trước khi ghi đè
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
stop_app

if [ "$MODE" = "app" ]; then
    build_app
elif [ -d "$APP" ]; then
    rm -rf "$APP"   # chuyển từ app bọc sang --direct
fi

render_plist >"$PLIST"
plutil -lint "$PLIST" >/dev/null

# PostureGuard đang chạy từ Terminal sẽ tranh camera với bản chạy ngầm
if pgrep -f "posture_guard.py" >/dev/null 2>&1; then
    echo "Lưu ý: đang có PostureGuard chạy (có thể từ Terminal). Nên thoát bản đó để tránh chạy 2 bản."
fi

launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Đã cài LaunchAgent: $PLIST (chế độ: $MODE)"
echo "PostureGuard đang khởi động ngầm và sẽ tự chạy mỗi lần đăng nhập."
echo "Nhật ký: $OUT_LOG"
echo "         $ERR_LOG"
echo
echo "Sau ~10 giây, kiểm tra icon trên thanh menu hoặc chạy:"
echo "  launchctl print $DOMAIN/$LABEL | grep -E 'state|last exit'"
echo "  tail -n 20 \"$ERR_LOG\""
