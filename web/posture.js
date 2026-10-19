// PostureGuard Kids – phần đo tư thế và quyết định khi nào nhắc.
// Không phụ thuộc trình duyệt (chạy được cả trong Node để kiểm thử).

// Chỉ số điểm mốc của MediaPipe Pose
const NOSE = 0, L_EYE = 2, R_EYE = 5, L_EAR = 7, R_EAR = 8, L_SH = 11, R_SH = 12;

export const ISSUE_LABELS = {
  slump: "Gục xuống bàn",
  close: "Cúi quá sát vở",
  flex: "Cúi cổ quá sâu",
  tilt: "Nghiêng đầu",
  shoulder: "Lệch vai",
};

// Mức nhạy: khi viết bài, cúi đầu là bình thường, nên chỉ báo khi cúi sâu hơn hẳn tư thế chuẩn
export const SENSITIVITY = {
  nhe:  { drop: 0.35, flex: 25, roll: 16, sh: 12 },
  vua:  { drop: 0.25, flex: 18, roll: 13, sh: 10 },
  chat: { drop: 0.17, flex: 12, roll: 10, sh: 8 },
};

const deg = (r) => (r * 180) / Math.PI;
const vis = (p) => (p.visibility ?? 1);

/**
 * Chỉ số tư thế từ 33 điểm mốc (toạ độ chuẩn hoá 0..1), hoặc null nếu không thấy rõ.
 * mode "front": điện thoại thẳng trước mặt; "side": đặt chéo 45° hoặc bên cạnh.
 */
export function computeMetrics(lm, width, height, mode) {
  if (!lm || lm.length < 13) return null;
  const pt = (i) => ({ x: lm[i].x * width, y: lm[i].y * height });
  const nose = pt(NOSE);
  if (vis(lm[NOSE]) < 0.5) return null;

  if (mode === "side") {
    // chọn bên nhìn rõ hơn (bên gần camera)
    const left = (vis(lm[L_EAR]) + vis(lm[L_SH])) / 2;
    const right = (vis(lm[R_EAR]) + vis(lm[R_SH])) / 2;
    const [ei, si] = left >= right ? [L_EAR, L_SH] : [R_EAR, R_SH];
    if (Math.min(vis(lm[ei]), vis(lm[si])) < 0.5) return null;
    const ear = pt(ei), sh = pt(si);
    const neckLen = Math.hypot(ear.x - sh.x, ear.y - sh.y);
    if (neckLen < 10) return null;
    return {
      mode,
      // góc cổ so với phương thẳng đứng: 0° = tai ở ngay trên vai, tăng khi cúi về phía trước
      flex: deg(Math.atan2(Math.abs(ear.x - sh.x), sh.y - ear.y)),
      // độ cao mũi so với vai (điểm ảnh); chia cho độ dài cổ lúc hiệu chỉnh khi đánh giá
      noseH: sh.y - nose.y,
      neckLen,
    };
  }

  if (Math.min(vis(lm[L_SH]), vis(lm[R_SH])) < 0.5) return null;
  const ls = pt(L_SH), rs = pt(R_SH), le = pt(L_EYE), re = pt(R_EYE);
  const sw = Math.hypot(ls.x - rs.x, ls.y - rs.y);
  if (sw < 20) return null;
  const shMidY = (ls.y + rs.y) / 2;
  // so góc theo hướng trái → phải trên ảnh để không phụ thuộc camera có lật ảnh hay không
  const [a1, a2] = le.x <= re.x ? [le, re] : [re, le];
  const [s1, s2] = ls.x <= rs.x ? [ls, rs] : [rs, ls];
  return {
    mode,
    neck: (shMidY - nose.y) / sw,              // giảm khi cúi đầu xuống vở
    roll: deg(Math.atan2(a2.y - a1.y, a2.x - a1.x)),  // nghiêng đầu
    shTilt: deg(Math.atan2(s2.y - s1.y, s2.x - s1.x)), // lệch vai
  };
}

/** Trung vị từng chỉ số của các mẫu lúc hiệu chỉnh → tư thế chuẩn. */
export function makeBaseline(samples) {
  if (!samples.length) return null;
  const median = (arr) => {
    const s = [...arr].sort((a, b) => a - b);
    const m = s.length >> 1;
    return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
  };
  const base = { mode: samples[0].mode };
  for (const k of Object.keys(samples[0])) {
    if (k !== "mode") base[k] = median(samples.map((s) => s[k]));
  }
  return base;
}

/** So với tư thế chuẩn → danh sách mã lỗi (rỗng = tốt). */
export function assess(m, base, level = "vua") {
  const t = SENSITIVITY[level] || SENSITIVITY.vua;
  const issues = [];
  if (m.mode === "side") {
    const h = m.noseH / base.neckLen, h0 = base.noseH / base.neckLen;
    if (h < -0.2) issues.push("slump");                      // mũi thấp hẳn dưới vai
    else if (h < h0 - t.drop * Math.max(Math.abs(h0), 0.5)) issues.push("close");
    if (m.flex > base.flex + t.flex && !issues.length) issues.push("flex");
    return issues;
  }
  if (m.neck < 0.05) issues.push("slump");                    // mũi ngang hoặc thấp hơn vai
  else if (m.neck < base.neck * (1 - t.drop)) issues.push("close");
  if (Math.abs(m.roll - base.roll) > t.roll) issues.push("tilt");
  if (Math.abs(m.shTilt - base.shTilt) > t.sh) issues.push("shoulder");
  return issues;
}

/** Làm mượt số liệu, tránh báo nhầm khi con quay đầu thoáng qua. */
export class Smoother {
  constructor(alpha = 0.4) { this.alpha = alpha; this.v = null; }
  push(m) {
    if (!this.v || this.v.mode !== m.mode) { this.v = { ...m }; return { ...this.v }; }
    for (const k of Object.keys(m)) {
      if (k !== "mode") this.v[k] = this.alpha * m[k] + (1 - this.alpha) * this.v[k];
    }
    return { ...this.v };
  }
  reset() { this.v = null; }
}

/**
 * Quyết định khi nào nhắc / khen / nhắc nghỉ, và cộng dồn thống kê buổi học.
 * update(tGiây, issues | null) → danh sách sự kiện: "ting" | "remind" | "praise" | "break".
 * issues = null nghĩa là không thấy con (rời chỗ).
 */
export class Coach {
  constructor(opts = {}) {
    this.o = {
      softDelay: 5,      // sai liên tục bao nhiêu giây thì kêu "ting"
      voiceDelay: 15,    // ... thì phát câu nhắc bằng giọng nói
      cooldown: 45,      // khoảng cách tối thiểu giữa 2 câu nhắc
      praiseMinutes: 10, // ngồi đúng liên tục bao lâu thì được 1 sao
      breakMinutes: 30,  // ngồi học bao lâu thì nhắc nghỉ
      awaySeconds: 60,   // rời chỗ bao lâu thì tính là đã nghỉ
      ...opts,
    };
    this.reset();
  }

  reset() {
    this.badSince = null; this.goodSince = null; this.softFired = false;
    this.lastRemind = -Infinity; this.lastSeen = null; this.sitStart = null; this.lastBreak = -Infinity;
    this.lastT = null;
    this.stats = { presentSec: 0, goodSec: 0, reminders: 0, stars: 0, issueSec: {} };
  }

  update(t, issues) {
    const ev = [];
    const dt = this.lastT == null ? 0 : Math.min(Math.max(t - this.lastT, 0), 2);
    this.lastT = t;
    const o = this.o;

    if (issues == null) {
      this.badSince = null; this.goodSince = null; this.softFired = false;
      if (this.lastSeen != null && t - this.lastSeen >= o.awaySeconds) this.sitStart = null;
      return ev;
    }

    if (this.sitStart == null || (this.lastSeen != null && t - this.lastSeen >= o.awaySeconds)) {
      this.sitStart = t;
    }
    this.lastSeen = t;
    this.stats.presentSec += dt;

    if (issues.length) {
      for (const i of issues) this.stats.issueSec[i] = (this.stats.issueSec[i] || 0) + dt;
      this.goodSince = null;
      if (this.badSince == null) this.badSince = t;
      const bad = t - this.badSince;
      if (bad >= o.voiceDelay && t - this.lastRemind >= o.cooldown) {
        ev.push("remind");
        this.lastRemind = t;
        this.stats.reminders += 1;
      } else if (bad >= o.softDelay && !this.softFired) {
        ev.push("ting");
        this.softFired = true;
      }
    } else {
      this.stats.goodSec += dt;
      this.badSince = null; this.softFired = false;
      if (this.goodSince == null) this.goodSince = t;
      if (t - this.goodSince >= o.praiseMinutes * 60) {
        ev.push("praise");
        this.stats.stars += 1;
        this.goodSince = t;
      }
    }

    if (t - this.sitStart >= o.breakMinutes * 60 && t - this.lastBreak >= 5 * 60) {
      ev.push("break");
      this.lastBreak = t;
    }
    return ev;
  }

  /** Lỗi gặp nhiều nhất trong buổi (mã lỗi) hoặc "". */
  topIssue() {
    const e = Object.entries(this.stats.issueSec).sort((a, b) => b[1] - a[1]);
    return e.length ? e[0][0] : "";
  }
}
