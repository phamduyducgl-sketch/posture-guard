// Ngồi Ngay Ngắn – web app theo dõi tư thế học bài bằng camera điện thoại.
// Nhận diện bằng MediaPipe Pose Landmarker chạy ngay trong trình duyệt: hình ảnh không rời khỏi máy.
import { computeMetrics, makeBaseline, assess, Smoother, Coach, ISSUE_LABELS } from "./posture.js";

const MP_VERSION = "0.10.14";
const MP_BASE = `https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@${MP_VERSION}`;
const MODEL_URL = "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task";
const FPS = 3; // khung hình xử lý mỗi giây: đủ để bắt tư thế, đỡ tốn pin và đỡ nóng máy

const $ = (s) => document.querySelector(s);
const store = {
  get(k, d) { try { const v = localStorage.getItem("ngn:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("ngn:" + k, JSON.stringify(v)); } catch { /* bộ nhớ đầy / chế độ riêng tư */ } },
  del(k) { try { localStorage.removeItem("ngn:" + k); } catch { /* bỏ qua */ } },
};
const DEFAULTS = { level: "vua", voiceDelay: 15, cooldown: 45, praiseMinutes: 10, breakMinutes: 30, showCam: false, mode: "side" };
const settings = () => ({ ...DEFAULTS, ...store.get("settings", {}) });

// ----------------------------------------------------------------------------- điều hướng
function go(id) {
  document.querySelectorAll(".screen").forEach((s) => s.classList.toggle("active", s.id === id));
  document.body.classList.toggle("monitoring", id === "monitor");
  window.scrollTo(0, 0);
  if (id !== "calibrate" && id !== "monitor") stopCamera();
  if (id === "home") renderHome();
  if (id === "voice") renderVoice();
  if (id === "report") renderReport();
  if (id === "settings") renderSettings();
}
document.querySelectorAll("[data-go]").forEach((b) => b.addEventListener("click", () => go(b.dataset.go)));

let toastTimer;
function toast(msg, ms = 3500) {
  const t = $("#toast");
  t.textContent = msg; t.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, ms);
}

// ----------------------------------------------------------------------------- âm thanh
// iOS chỉ cho phát âm thanh sau khi người dùng chạm vào màn hình: mọi nút "Bắt đầu" đều gọi unlockAudio().
let ctx = null;
function unlockAudio() {
  try { if (navigator.audioSession) navigator.audioSession.type = "playback"; } catch { /* Safari cũ */ }
  if (!ctx) ctx = new (window.AudioContext || window.webkitAudioContext)();
  if (ctx.state === "suspended") ctx.resume();
  if ("speechSynthesis" in window) {
    const u = new SpeechSynthesisUtterance(" ");
    u.volume = 0;
    speechSynthesis.speak(u);
  }
}

function tone(freq, start, dur, gain = 0.25, type = "sine") {
  const o = ctx.createOscillator(), g = ctx.createGain();
  o.type = type; o.frequency.value = freq;
  g.gain.setValueAtTime(0, ctx.currentTime + start);
  g.gain.linearRampToValueAtTime(gain, ctx.currentTime + start + 0.02);
  g.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + start + dur);
  o.connect(g).connect(ctx.destination);
  o.start(ctx.currentTime + start); o.stop(ctx.currentTime + start + dur + 0.05);
}
const sounds = {
  ting() { tone(1318.5, 0, 0.6, 0.22); tone(1760, 0.12, 0.7, 0.16); },               // tiếng chuông nhẹ
  praise() { [523.25, 659.25, 783.99, 1046.5].forEach((f, i) => tone(f, i * 0.12, 0.35, 0.2, "triangle")); },
};

const VOICE_SLOTS = [
  { key: "remind", title: "Nhắc ngồi thẳng", count: 3,
    samples: ["Con ngồi thẳng lưng lên nhé", "Con ơi, ngẩng đầu lên một chút nào", "Mắt xa vở ra một gang tay nhé con"] },
  { key: "praise", title: "Khen khi ngồi đúng", count: 2,
    samples: ["Giỏi quá, con ngồi rất đẹp", "Tuyệt vời, con được thêm một ngôi sao"] },
  { key: "break", title: "Nhắc nghỉ mắt", count: 1,
    samples: ["Con nghỉ mắt một chút, nhìn ra xa nhé"] },
];

// Bản ghi âm lưu trong IndexedDB của trình duyệt (chỉ trên máy này)
const db = {
  open() {
    if (this._p) return this._p;
    this._p = new Promise((res, rej) => {
      const r = indexedDB.open("ngoi-ngay-ngan", 1);
      r.onupgradeneeded = () => r.result.createObjectStore("voice");
      r.onsuccess = () => res(r.result);
      r.onerror = () => rej(r.error);
    });
    return this._p;
  },
  async op(mode, fn) {
    const d = await this.open();
    return new Promise((res, rej) => {
      const tx = d.transaction("voice", mode);
      const req = fn(tx.objectStore("voice"));
      tx.oncomplete = () => res(req && req.result);
      tx.onerror = () => rej(tx.error);
    });
  },
  get(k) { return this.op("readonly", (s) => s.get(k)); },
  put(k, v) { return this.op("readwrite", (s) => s.put(v, k)); },
  del(k) { return this.op("readwrite", (s) => s.delete(k)); },
  keys() { return this.op("readonly", (s) => s.getAllKeys()); },
};

const decoded = new Map();
async function playRecording(id) {
  let buf = decoded.get(id);
  if (!buf) {
    const blob = await db.get(id);
    if (!blob) return false;
    buf = await ctx.decodeAudioData(await blob.arrayBuffer());
    decoded.set(id, buf);
  }
  const src = ctx.createBufferSource();
  const g = ctx.createGain(); g.gain.value = 1.4;
  src.buffer = buf; src.connect(g).connect(ctx.destination); src.start();
  return true;
}

function speak(text) {
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "vi-VN"; u.rate = 0.95;
  const vi = speechSynthesis.getVoices().find((v) => v.lang && v.lang.toLowerCase().startsWith("vi"));
  if (vi) u.voice = vi;
  speechSynthesis.speak(u);
}

// Câu mẫu của giọng máy theo đúng lỗi đang gặp (bản ghi âm của bố mẹ thì dùng chung cho mọi lỗi)
const REMIND_BY_ISSUE = {
  slump: "Con ngồi thẳng lưng lên nhé, đừng gục xuống bàn",
  close: "Mắt xa vở ra một gang tay nhé con",
  flex: "Con ngẩng đầu lên một chút nào",
  tilt: "Con để đầu thẳng lại nhé",
  shoulder: "Con ngồi cho hai vai ngang nhau nhé",
};

/** Phát câu nhắc: ưu tiên bản bố mẹ ghi âm (chọn ngẫu nhiên), không có thì dùng giọng máy đọc câu mẫu. */
async function say(slotKey, issues = []) {
  const slot = VOICE_SLOTS.find((s) => s.key === slotKey);
  try {
    const keys = (await db.keys()).filter((k) => k.startsWith(slotKey + "-"));
    if (keys.length && await playRecording(keys[Math.floor(Math.random() * keys.length)])) return;
  } catch { /* IndexedDB không dùng được: dùng giọng máy */ }
  const byIssue = slotKey === "remind" && issues.length && REMIND_BY_ISSUE[issues[0]];
  speak(byIssue || slot.samples[Math.floor(Math.random() * slot.samples.length)]);
}

// ----------------------------------------------------------------------------- nhận diện
let landmarker = null, loading = null;
async function loadModel(statusEl) {
  if (landmarker) return landmarker;
  if (loading) return loading;
  loading = (async () => {
    statusEl.textContent = "Đang tải bộ nhận diện (khoảng 6 MB, chỉ lần đầu)…";
    const { FilesetResolver, PoseLandmarker } = await import(`${MP_BASE}/vision_bundle.mjs`);
    const files = await FilesetResolver.forVisionTasks(`${MP_BASE}/wasm`);
    const opts = (delegate) => ({
      baseOptions: { modelAssetPath: MODEL_URL, delegate },
      runningMode: "VIDEO", numPoses: 1,
      minPoseDetectionConfidence: 0.5, minTrackingConfidence: 0.5,
    });
    try {
      landmarker = await PoseLandmarker.createFromOptions(files, opts("GPU"));
    } catch {
      landmarker = await PoseLandmarker.createFromOptions(files, opts("CPU"));
    }
    return landmarker;
  })();
  try { return await loading; } finally { loading = null; }
}

let stream = null;
async function startCamera(video) {
  if (stream) { video.srcObject = stream; await video.play().catch(() => {}); return; }
  stream = await navigator.mediaDevices.getUserMedia({
    video: { facingMode: "user", width: { ideal: 640 }, height: { ideal: 480 } }, audio: false,
  });
  video.srcObject = stream;
  await video.play().catch(() => {});
  await new Promise((r) => (video.readyState >= 2 ? r() : video.addEventListener("loadeddata", r, { once: true })));
}
function stopCamera() {
  if (stream) stream.getTracks().forEach((t) => t.stop());
  stream = null;
}
function cameraError(e, statusEl) {
  const msg = e && e.name === "NotAllowedError"
    ? "Chưa được phép dùng camera. Vào Cài đặt → Safari → Camera → Cho phép, rồi tải lại trang."
    : !window.isSecureContext
      ? "Cần mở trang qua https:// để dùng camera."
      : "Không mở được camera: " + (e && e.message ? e.message : e);
  statusEl.textContent = msg;
  statusEl.classList.remove("ok");
}

let lastTs = 0;
function detect(video) {
  const ts = Math.max(performance.now(), lastTs + 1);
  lastTs = ts;
  const res = landmarker.detectForVideo(video, ts);
  return res.landmarks && res.landmarks[0] ? res.landmarks[0] : null;
}

function drawPose(canvas, video, lm, color) {
  const w = (canvas.width = video.videoWidth), h = (canvas.height = video.videoHeight);
  const g = canvas.getContext("2d");
  g.clearRect(0, 0, w, h);
  if (!lm) return;
  g.lineWidth = Math.max(3, w / 160); g.strokeStyle = color; g.fillStyle = color;
  const P = (i) => [lm[i].x * w, lm[i].y * h];
  const line = (a, b) => { g.beginPath(); g.moveTo(...P(a)); g.lineTo(...P(b)); g.stroke(); };
  line(11, 12); line(7, 2); line(2, 5); line(5, 8); line(0, 2); line(0, 5);
  for (const i of [0, 2, 5, 7, 8, 11, 12]) {
    if ((lm[i].visibility ?? 1) < 0.5) continue;
    g.beginPath(); g.arc(...P(i), g.lineWidth * 1.6, 0, Math.PI * 2); g.fill();
  }
}

// ----------------------------------------------------------------------------- màn hình sáng
let wakeLock = null;
async function keepAwake(on) {
  try {
    if (on && "wakeLock" in navigator) {
      wakeLock = await navigator.wakeLock.request("screen");
    } else if (!on && wakeLock) {
      await wakeLock.release(); wakeLock = null;
    }
  } catch { /* trình duyệt cũ: hướng dẫn tắt Tự động khoá */ }
  return !!wakeLock || !on;
}

// ----------------------------------------------------------------------------- trang chủ
function renderHome() {
  const base = store.get("baseline", null);
  $("#btn-start").disabled = !base;
  $("#btn-calibrate").textContent = base ? "Hiệu chỉnh lại tư thế chuẩn" : "Thiết lập tư thế chuẩn (làm trước)";
  const ios = /iPhone|iPad|iPod/.test(navigator.userAgent);
  const standalone = window.navigator.standalone || matchMedia("(display-mode: standalone)").matches;
  $("#install-hint").hidden = !(ios && !standalone);
}
$("#btn-calibrate").addEventListener("click", () => { unlockAudio(); go("calibrate"); openCalibrate(); });
$("#btn-voice").addEventListener("click", () => go("voice"));
$("#btn-report").addEventListener("click", () => go("report"));
$("#btn-settings").addEventListener("click", () => go("settings"));
$("#btn-start").addEventListener("click", () => { unlockAudio(); go("monitor"); startSession(); });

// ----------------------------------------------------------------------------- hiệu chỉnh
const MODE_HINTS = {
  side: "Đặt điện thoại trên giá, phía trước và lệch sang bên tay KHÔNG cầm bút khoảng 45°, cao ngang mặt con. Camera thấy được đầu, cổ và vai. Cách này đo độ cúi đầu và khoảng cách mắt–vở chính xác nhất.",
  front: "Đặt điện thoại trên giá ngay trước mặt con, phía bên kia quyển vở, cao ngang hoặc hơi thấp hơn mặt con. Camera thấy rõ mặt và hai vai.",
};
let calibLoop = null;
function setMode(mode) {
  const s = store.get("settings", {});
  store.set("settings", { ...s, mode });
  document.querySelectorAll(".seg-btn").forEach((b) => b.setAttribute("aria-checked", String(b.dataset.mode === mode)));
  $("#mode-hint").textContent = MODE_HINTS[mode];
}
document.querySelectorAll(".seg-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

async function openCalibrate() {
  setMode(settings().mode);
  const video = $("#video"), canvas = $("#overlay"), status = $("#cam-status"), btn = $("#btn-save-base");
  btn.disabled = true;
  status.textContent = "Đang mở camera…"; status.classList.remove("ok");
  try {
    await startCamera(video);
    await loadModel(status);
  } catch (e) { cameraError(e, status); return; }
  clearInterval(calibLoop);
  calibLoop = setInterval(() => {
    if (!$("#calibrate").classList.contains("active")) { clearInterval(calibLoop); return; }
    if (sampling) return;
    const lm = detect(video);
    const m = lm && computeMetrics(lm, video.videoWidth, video.videoHeight, settings().mode);
    drawPose(canvas, video, lm, m ? "#35d07f" : "#ffcc00");
    if (m) {
      status.textContent = "✓ Thấy rõ đầu và vai";
      status.classList.add("ok");
    } else {
      status.textContent = lm
        ? (settings().mode === "side" ? "Chưa thấy rõ tai và vai phía gần camera" : "Chưa thấy rõ cả hai vai – lùi điện thoại ra xa một chút")
        : "Chưa thấy con – chỉnh lại giá điện thoại";
      status.classList.remove("ok");
    }
    btn.disabled = !m;
  }, 1000 / FPS);
}

let sampling = false;
$("#btn-save-base").addEventListener("click", async () => {
  const video = $("#video"), canvas = $("#overlay"), status = $("#cam-status"), btn = $("#btn-save-base");
  const mode = settings().mode;
  sampling = true; btn.disabled = true;
  const samples = [];
  const t0 = performance.now();
  while (performance.now() - t0 < 5000) {
    const left = Math.ceil((5000 - (performance.now() - t0)) / 1000);
    status.textContent = `Giữ nguyên tư thế… ${left}`;
    const lm = detect(video);
    const m = lm && computeMetrics(lm, video.videoWidth, video.videoHeight, mode);
    drawPose(canvas, video, lm, "#35d07f");
    if (m) samples.push(m);
    await new Promise((r) => setTimeout(r, 1000 / 6));
  }
  sampling = false;
  if (samples.length < 8) {
    status.textContent = "Chưa đo được – camera chưa thấy rõ. Chỉnh lại rồi thử lần nữa.";
    btn.disabled = false;
    return;
  }
  store.set("baseline", makeBaseline(samples));
  sounds.praise();
  toast("Đã lưu tư thế chuẩn. Bấm “Bắt đầu buổi học” để theo dõi.");
  go("home");
});

// ----------------------------------------------------------------------------- buổi học
let session = null;
function startSession() {
  const s = settings();
  session = {
    start: Date.now(), paused: false, timer: null, mode: s.mode,
    smoother: new Smoother(),
    coach: new Coach({ voiceDelay: +s.voiceDelay, cooldown: +s.cooldown, praiseMinutes: +s.praiseMinutes,
                       breakMinutes: +s.breakMinutes || 1e9 }),
    level: s.level,
    lastSave: 0,
  };
  const cam = $("#mon-cam");
  cam.hidden = !s.showCam;
  cam.innerHTML = "";
  const video = document.createElement("video");
  video.playsInline = true; video.muted = true; video.autoplay = true;
  video.setAttribute("playsinline", "");
  cam.appendChild(video);
  session.video = video;
  setDot("grey", "Đang mở camera…");
  (async () => {
    try {
      await startCamera(video);
      await loadModel($("#mon-status"));
    } catch (e) { cameraError(e, $("#mon-status")); return; }
    const awake = await keepAwake(true);
    if (!awake) toast("Để màn hình không tự tắt: Cài đặt → Màn hình & Độ sáng → Tự động khoá → Không bao giờ.", 7000);
    setDot("grey", "Đang tìm con…");
    session.timer = setInterval(tick, 1000 / FPS);
  })();
}

function setDot(color, text) {
  $("#mon-dot").className = "dot " + color;
  $("#mon-status").textContent = text;
}

function fmt(sec) {
  const m = Math.floor(sec / 60), h = Math.floor(m / 60);
  return h ? `${h}:${String(m % 60).padStart(2, "0")}:${String(Math.floor(sec % 60)).padStart(2, "0")}`
           : `${m}:${String(Math.floor(sec % 60)).padStart(2, "0")}`;
}

function tick() {
  if (!session || session.paused || !landmarker) return;
  const { video, coach } = session;
  if (video.readyState < 2) return;
  const t = performance.now() / 1000;
  const base = store.get("baseline", null);
  const lm = detect(video);
  const raw = lm && computeMetrics(lm, video.videoWidth, video.videoHeight, base.mode);
  let issues = null;
  if (raw) issues = assess(session.smoother.push(raw), base, session.level);
  else session.smoother.reset();

  for (const ev of coach.update(t, issues)) {
    if (ev === "ting") sounds.ting();
    if (ev === "remind") { sounds.ting(); setTimeout(() => say("remind", issues), 700); }
    if (ev === "praise") { sounds.praise(); setTimeout(() => say("praise"), 900); }
    if (ev === "break") say("break");
  }

  if (!issues) setDot("grey", "Không thấy con");
  else if (issues.length) setDot("red", issues.map((i) => ISSUE_LABELS[i]).join(" · "));
  else setDot("green", "Ngồi đẹp lắm!");
  $("#mon-time").textContent = fmt(coach.stats.presentSec);
  $("#mon-stars").textContent = `⭐ ${coach.stats.stars}`;

  if (t - session.lastSave > 30) { saveSession(false); session.lastSave = t; }  // lỡ tắt app vẫn còn số liệu
}

function sessionRecord() {
  const st = session.coach.stats;
  return {
    id: session.start, start: session.start, end: Date.now(),
    minutes: +(st.presentSec / 60).toFixed(1),
    goodPct: st.presentSec ? Math.round((100 * st.goodSec) / st.presentSec) : 0,
    reminders: st.reminders, stars: st.stars, top: session.coach.topIssue(),
  };
}
function saveSession() {
  if (!session || session.coach.stats.presentSec < 30) return;
  const rec = sessionRecord();
  const all = store.get("sessions", []).filter((s) => s.id !== rec.id);
  all.push(rec);
  store.set("sessions", all.slice(-400));
}

$("#btn-pause").addEventListener("click", () => {
  if (!session) return;
  session.paused = !session.paused;
  session.coach.update(performance.now() / 1000, null);
  $("#btn-pause").textContent = session.paused ? "Tiếp tục" : "Tạm dừng";
  if (session.paused) setDot("grey", "Đang tạm dừng");
});
$("#btn-end").addEventListener("click", () => endSession(true));

function endSession(showReport) {
  if (!session) return;
  clearInterval(session.timer);
  saveSession();
  const rec = sessionRecord();
  session = null;
  keepAwake(false);
  stopCamera();
  if (showReport) {
    go("report");
    if (rec.minutes >= 0.5) toast(`Buổi học ${Math.round(rec.minutes)} phút · ngồi đúng ${rec.goodPct}% · ⭐ ${rec.stars}`, 6000);
  }
}

// Ra khỏi app (iOS tắt camera khi Safari chạy nền): quay lại thì mở lại camera và giữ màn hình sáng
document.addEventListener("visibilitychange", async () => {
  if (document.visibilityState !== "visible" || !session) return;
  try {
    stopCamera();
    await startCamera(session.video);
    await keepAwake(true);
    if (ctx && ctx.state === "suspended") ctx.resume();
  } catch (e) { cameraError(e, $("#mon-status")); }
});

// ----------------------------------------------------------------------------- ghi âm
let recorder = null;
async function renderVoice() {
  const list = $("#voice-list");
  let keys = [];
  try { keys = await db.keys(); } catch { /* bỏ qua */ }
  list.innerHTML = "";
  for (const slot of VOICE_SLOTS) {
    const box = document.createElement("div");
    box.className = "voice-group";
    box.innerHTML = `<h3>${slot.title}</h3>`;
    for (let i = 0; i < slot.count; i++) {
      const id = `${slot.key}-${i}`;
      const has = keys.includes(id);
      const row = document.createElement("div");
      row.className = "voice-item";
      row.innerHTML = `<div class="label">Câu ${i + 1}${has ? " ✓" : ""}<small>Gợi ý: “${slot.samples[i % slot.samples.length]}”</small></div>`;
      const play = document.createElement("button");
      play.className = "ghost"; play.textContent = has ? "▶︎ Nghe" : "▶︎ Mẫu";
      play.addEventListener("click", async () => {
        unlockAudio();
        if (!(has && await playRecording(id))) speak(slot.samples[i % slot.samples.length]);
      });
      const rec = document.createElement("button");
      rec.className = "rec"; rec.textContent = has ? "Ghi lại" : "● Ghi";
      rec.addEventListener("click", () => toggleRecord(id, rec));
      row.append(play, rec);
      box.appendChild(row);
    }
    list.appendChild(box);
  }
}

async function toggleRecord(id, btn) {
  if (recorder && recorder.state === "recording") { recorder.stop(); return; }
  unlockAudio();
  let mic;
  try {
    mic = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch {
    toast("Chưa được phép dùng micro. Vào Cài đặt → Safari → Micro → Cho phép.", 6000);
    return;
  }
  const type = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t));
  recorder = new MediaRecorder(mic, type ? { mimeType: type } : undefined);
  const chunks = [];
  recorder.ondataavailable = (e) => e.data.size && chunks.push(e.data);
  recorder.onstop = async () => {
    mic.getTracks().forEach((t) => t.stop());
    clearTimeout(stopTimer);
    const blob = new Blob(chunks, { type: recorder.mimeType || type || "audio/mp4" });
    recorder = null;
    if (blob.size < 2000) { toast("Bản ghi quá ngắn, thử lại nhé."); renderVoice(); return; }
    await db.put(id, blob);
    decoded.delete(id);
    toast("Đã lưu câu nhắc.");
    renderVoice();
    playRecording(id);
  };
  recorder.start();
  btn.classList.add("on"); btn.textContent = "■ Dừng";
  const stopTimer = setTimeout(() => recorder && recorder.state === "recording" && recorder.stop(), 6000);
}

// ----------------------------------------------------------------------------- báo cáo
function renderReport() {
  const all = store.get("sessions", []);
  const body = $("#report-body");
  if (!all.length) {
    body.innerHTML = `<p class="hint pad">Chưa có buổi học nào. Báo cáo sẽ hiện ở đây sau buổi học đầu tiên.</p>`;
    return;
  }
  const dayKey = (ms) => { const d = new Date(ms); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; };
  const today = all.filter((s) => dayKey(s.start) === dayKey(Date.now()));
  const sum = (arr, k) => arr.reduce((a, s) => a + s[k], 0);
  const minsToday = sum(today, "minutes");
  const goodToday = minsToday ? Math.round(today.reduce((a, s) => a + s.goodPct * s.minutes, 0) / minsToday) : 0;

  // 7 ngày gần nhất: cột = số phút học, phần màu đậm = số phút ngồi đúng
  const days = [];
  for (let i = 6; i >= 0; i--) {
    const d = new Date(); d.setHours(0, 0, 0, 0); d.setDate(d.getDate() - i);
    const ss = all.filter((s) => dayKey(s.start) === dayKey(d.getTime()));
    const mins = sum(ss, "minutes");
    days.push({ label: i === 0 ? "Nay" : ["CN", "T2", "T3", "T4", "T5", "T6", "T7"][d.getDay()],
                mins, good: ss.reduce((a, s) => a + (s.goodPct / 100) * s.minutes, 0) });
  }
  const maxM = Math.max(30, ...days.map((d) => d.mins));
  const W = 320, H = 150, bw = 30, gap = (W - 7 * bw) / 6;
  const bars = days.map((d, i) => {
    const x = i * (bw + gap), h = (d.mins / maxM) * (H - 34), hg = (d.good / maxM) * (H - 34);
    return `<rect x="${x}" y="${H - 20 - h}" width="${bw}" height="${h}" rx="5" fill="var(--line)"/>
            <rect x="${x}" y="${H - 20 - hg}" width="${bw}" height="${hg}" rx="5" fill="var(--brand)"/>
            <text x="${x + bw / 2}" y="${H - 4}" text-anchor="middle" font-size="11" fill="var(--muted)">${d.label}</text>
            ${d.mins ? `<text x="${x + bw / 2}" y="${H - 24 - h}" text-anchor="middle" font-size="11" fill="var(--text)">${Math.round(d.mins)}′</text>` : ""}`;
  }).join("");

  const recent = [...all].reverse().slice(0, 20).map((s) => {
    const d = new Date(s.start);
    return `<li><b>${d.toLocaleDateString("vi-VN", { weekday: "short", day: "numeric", month: "numeric" })} ${d.toLocaleTimeString("vi-VN", { hour: "2-digit", minute: "2-digit" })}</b>
      · ${Math.round(s.minutes)} phút · ngồi đúng ${s.goodPct}% · ⭐ ${s.stars}<br>
      <small>Nhắc ${s.reminders} lần${s.top ? ` · hay gặp: ${ISSUE_LABELS[s.top]}` : ""}</small></li>`;
  }).join("");

  body.innerHTML = `
    <div class="stat-grid">
      <div class="stat"><b>${Math.round(minsToday)}′</b><span>Học hôm nay</span></div>
      <div class="stat"><b>${goodToday}%</b><span>Ngồi đúng hôm nay</span></div>
      <div class="stat"><b>${sum(today, "reminders")}</b><span>Lần nhắc hôm nay</span></div>
      <div class="stat"><b>⭐ ${sum(today, "stars")}</b><span>Sao hôm nay</span></div>
    </div>
    <div class="chart">
      <svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Số phút học và ngồi đúng 7 ngày gần nhất">${bars}</svg>
      <div class="legend"><span><i class="sw" style="background:var(--brand)"></i>Ngồi đúng</span><span><i class="sw" style="background:var(--line)"></i>Tổng thời gian học</span></div>
    </div>
    <ul class="sessions">${recent}</ul>`;
}

// ----------------------------------------------------------------------------- cài đặt
function renderSettings() {
  const s = settings(), f = $("#settings-form");
  for (const el of f.elements) {
    if (!el.name) continue;
    if (el.type === "checkbox") el.checked = !!s[el.name];
    else el.value = String(s[el.name]);
  }
}
$("#settings-form").addEventListener("change", (e) => {
  const el = e.target;
  const s = store.get("settings", {});
  s[el.name] = el.type === "checkbox" ? el.checked : (isNaN(+el.value) ? el.value : +el.value);
  store.set("settings", s);
});
$("#btn-test-sound").addEventListener("click", () => {
  unlockAudio();
  sounds.ting();
  setTimeout(() => say("remind"), 900);
});
$("#btn-clear").addEventListener("click", () => {
  if (!confirm("Xoá tư thế chuẩn và toàn bộ báo cáo? (Giữ lại các câu đã ghi âm)")) return;
  store.del("baseline"); store.del("sessions");
  toast("Đã xoá.");
  go("home");
});

// ----------------------------------------------------------------------------- khởi động
if ("serviceWorker" in navigator) navigator.serviceWorker.register("sw.js").catch(() => {});
if ("speechSynthesis" in window) speechSynthesis.getVoices(); // nạp sẵn danh sách giọng đọc
window.addEventListener("pagehide", () => saveSession());
renderHome();

// Móc kiểm thử: chỉ dùng khi mở trang với ?test (giả lập điểm mốc thay cho camera)
if (new URLSearchParams(location.search).has("test")) {
  window.__ngn = { computeMetrics, assess, makeBaseline, store, go, sounds, say, Coach };
}
