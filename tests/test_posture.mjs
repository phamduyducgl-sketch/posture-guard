import { computeMetrics, makeBaseline, assess, Coach, Smoother } from "../web/posture.js";
import assert from "node:assert/strict";
const W = 640, H = 480;
// tạo 33 điểm mốc giả: đầu ở (cx, headY), vai ở y=shY
function lm({ noseY = 0.35, shY = 0.6, roll = 0, earX = 0.5, earY = 0.32, shX = 0.5 } = {}) {
  const p = Array.from({ length: 33 }, () => ({ x: 0.5, y: 0.5, visibility: 0.99 }));
  p[0] = { x: 0.5, y: noseY, visibility: 0.99 };
  const r = (roll * Math.PI) / 180;
  p[2] = { x: 0.46, y: noseY - 0.04 - 0.04 * Math.tan(r), visibility: 0.99 };
  p[5] = { x: 0.54, y: noseY - 0.04 + 0.04 * Math.tan(r), visibility: 0.99 };
  p[7] = { x: earX, y: earY, visibility: 0.99 }; p[8] = { x: earX, y: earY, visibility: 0.2 };
  p[11] = { x: 0.35, y: shY, visibility: 0.99 }; p[12] = { x: 0.65, y: shY, visibility: 0.99 };
  if (shX !== 0.5) p[11] = { x: shX, y: shY, visibility: 0.99 };
  return p;
}
// --- trước mặt
const good = computeMetrics(lm(), W, H, "front");
const base = makeBaseline([good, good, good]);
assert.deepEqual(assess(good, base), [], "ngồi đúng");
assert.deepEqual(assess(computeMetrics(lm({ noseY: 0.40 }), W, H, "front"), base), [], "cúi nhẹ khi viết: không báo");
assert.deepEqual(assess(computeMetrics(lm({ noseY: 0.47 }), W, H, "front"), base), ["close"], "cúi sát vở");
assert.deepEqual(assess(computeMetrics(lm({ noseY: 0.62 }), W, H, "front"), base), ["slump"], "gục");
assert.ok(assess(computeMetrics(lm({ roll: 20 }), W, H, "front"), base).includes("tilt"), "nghiêng đầu");
// mức nhạy chặt bắt được cúi vừa
assert.deepEqual(assess(computeMetrics(lm({ noseY: 0.43 }), W, H, "front"), base, "chat"), ["close"]);
assert.deepEqual(assess(computeMetrics(lm({ noseY: 0.43 }), W, H, "front"), base, "nhe"), []);
// không thấy vai → null
const noSh = lm(); noSh[11].visibility = 0.1;
assert.equal(computeMetrics(noSh, W, H, "front"), null);
// --- chéo 45° / bên cạnh: vai (0.5, 0.6), tai thẳng trên vai
const sGood = computeMetrics(lm({ shX: 0.5, earX: 0.5, earY: 0.38, noseY: 0.40 }), W, H, "side");
const sBase = makeBaseline([sGood]);
assert.deepEqual(assess(sGood, sBase), []);
const sFlex = computeMetrics(lm({ shX: 0.5, earX: 0.62, earY: 0.45, noseY: 0.44 }), W, H, "side");
console.log("góc cổ chuẩn %.1f°, khi cúi %.1f°", sBase.flex, sFlex.flex);
assert.ok(assess(sFlex, sBase).length === 1, "cúi cổ sâu bị báo: " + assess(sFlex, sBase));
assert.deepEqual(assess(computeMetrics(lm({ shX: 0.5, earX: 0.6, earY: 0.62, noseY: 0.68 }), W, H, "side"), sBase), ["slump"]);
// --- Coach: nhắc đúng nhịp
const c = new Coach();
const evs = [];
for (let t = 0; t <= 120; t += 0.5) for (const e of c.update(t, ["close"])) evs.push([t, e]);
console.log("sự kiện khi sai liên tục 2 phút:", JSON.stringify(evs));
assert.deepEqual(evs.map((e) => e[1]), ["ting", "remind", "remind", "remind"]);
assert.equal(evs[0][0], 5); assert.equal(evs[1][0], 15); assert.equal(evs[2][0], 60);
// ngồi đúng lại rồi sai lại: ting lại được, nhưng câu nhắc vẫn giữ khoảng cách 45s
const c2 = new Coach(); const e2 = [];
const seq = [[0, 20, ["close"]], [20, 30, []], [30, 50, ["close"]]];
for (const [a, b, iss] of seq) for (let t = a; t < b; t += 0.5) for (const e of c2.update(t, iss)) e2.push([t, e]);
console.log("sai – đúng – sai:", JSON.stringify(e2));
assert.deepEqual(e2.map((e) => e[1]), ["ting", "remind", "ting"]);
// khen sau 10 phút ngồi đúng, nhắc nghỉ sau 30 phút
const c3 = new Coach(); const e3 = [];
for (let t = 0; t <= 1810; t += 1) for (const e of c3.update(t, [])) e3.push([t, e]);
console.log("ngồi đúng 30 phút:", JSON.stringify(e3));
assert.deepEqual(e3.map((e) => e[1]), ["praise", "praise", "praise", "break"]);
assert.equal(c3.stats.stars, 3);
// rời chỗ > 60s thì tính là đã nghỉ
const c4 = new Coach(); let br = 0;
for (let t = 0; t < 1500; t++) c4.update(t, []);
for (let t = 1500; t < 1600; t++) c4.update(t, null);
for (let t = 1600; t < 3000; t++) br += c4.update(t, []).filter((e) => e === "break").length;
assert.equal(br, 0, "đã nghỉ giữa chừng nên chưa nhắc nghỉ");
// thống kê
assert.ok(Math.abs(c3.stats.goodSec - 1810) < 2);
const sm = new Smoother(0.5); sm.push({ mode: "front", neck: 1 }); assert.equal(sm.push({ mode: "front", neck: 0 }).neck, 0.5);
console.log("TẤT CẢ ĐỀU QUA");
