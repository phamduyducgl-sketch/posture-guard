"""Kiểm thử bộ theo dõi rời đi / quay lại (chạy: python tests/test_presence.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from posture_guard import PresenceTracker  # noqa: E402


def run(tr, segments, step=0.5, t0=0.0):
    """segments: [(số giây, có thấy người?)] → (danh sách sự kiện, thời điểm cuối)."""
    t, out = t0, []
    for dur, seen in segments:
        end = t + dur
        while t < end - 1e-9:
            out += tr.update(t, seen)
            t += step
    return out, t


def approx(a, b, tol=0.6):
    return abs(a - b) <= tol


# 1. ngồi 20 phút liên tục
tr = PresenceTracker()
ev, t = run(tr, [(1200, True)])
assert [e["type"] for e in ev] == ["arrived"], ev
assert approx(tr.sit_min * 60, 1200), tr.sit_min

# 2. mất dấu ngắn (5 giây: quay người, cúi nhặt đồ) → vẫn tính là ngồi, không có sự kiện rời đi
tr = PresenceTracker()
ev, t = run(tr, [(600, True), (5, False), (600, True)])
assert [e["type"] for e in ev] == ["arrived"], ev
assert approx(tr.sit_min * 60, 1205, 1.1), tr.sit_min * 60

# 3. đi lấy nước 2 phút → đồng hồ dừng lúc vắng mặt, quay lại chạy tiếp, không xoá
tr = PresenceTracker()
ev, t = run(tr, [(1200, True), (120, False), (600, True)])
types = [e["type"] for e in ev]
assert types == ["arrived", "left", "back"], types
back = ev[-1]
assert not back["is_break"] and approx(back["away_min"] * 60, 120, 1.1), back
assert approx(back["sat_min"] * 60, 1200, 1.1), back
assert approx(tr.sit_min * 60, 1800, 1.6), tr.sit_min * 60  # 20 + 10 phút, không cộng 2 phút vắng
assert approx(ev[1]["at"], 1199.5), ev[1]                  # giờ rời đi = lần cuối còn thấy, không trễ 10 giây

# 4. nghỉ 6 phút → tính là đã nghỉ, đồng hồ về 0
tr = PresenceTracker(break_min=5)
ev, t = run(tr, [(1500, True), (360, False), (300, True)])
back = ev[-1]
assert back["type"] == "back" and back["is_break"], back
assert approx(back["away_min"] * 60, 360, 1.1), back
assert approx(tr.sit_min * 60, 300, 1.1), tr.sit_min * 60
assert tr.last_break is not None

# 5. người đi ngang qua camera 2 giây lúc đang vắng → chưa tính là quay lại
tr = PresenceTracker()
ev, t = run(tr, [(600, True), (100, False), (2, True), (100, False), (60, True)])
types = [e["type"] for e in ev]
assert types == ["arrived", "left", "back"], types
assert approx(ev[-1]["away_min"] * 60, 202, 1.1), ev[-1]   # vắng tính từ lúc rời tới lúc thực sự quay lại

# 6. máy ngủ 30 phút (không có cập nhật nào) rồi thấy người ngay → vắng 30 phút, tính là đã nghỉ
tr = PresenceTracker()
ev, t = run(tr, [(1200, True)])
ev2, _ = run(tr, [(10, True)], t0=t + 1800)
assert [e["type"] for e in ev2] == ["left", "back"], ev2
assert ev2[-1]["is_break"] and approx(ev2[-1]["away_min"], 30.0, 0.05), ev2[-1]
assert tr.sit_sec < 15, tr.sit_sec

# 7. tạm dừng theo dõi 20 phút (họp) → không tính là ngồi, cũng không tính là nghỉ
tr = PresenceTracker()
ev, t = run(tr, [(600, True)])
tr.suspend(t)
ev2, _ = run(tr, [(300, True)], t0=t + 1200)
assert ev2 == [], ev2
assert approx(tr.sit_min * 60, 900, 1.1), tr.sit_min * 60

# 8. vắng đúng ngưỡng nghỉ: 4 phút 50 chưa đủ, 5 phút 10 là đủ
for away, expect in ((290, False), (310, True)):
    tr = PresenceTracker(break_min=5)
    ev, _ = run(tr, [(600, True), (away, False), (30, True)])
    assert ev[-1]["is_break"] is expect, (away, ev[-1])

print("TẤT CẢ ĐỀU QUA")
