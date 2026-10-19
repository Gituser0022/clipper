// "Find the best moments": analyse a video's audio, draw the loudness graph, let the
// user tune how many clips and how long, then make them all at once.
import { $, api, post, el, setText, setHidden, setDisabled, setAttr, clamp, dur, plural, toast, busy,
         trackJobs, untrack, kick, smooth, isStopped, rememberTitle } from "./core.js";
import { player, playRange } from "./player.js";

let url = "", analysis = null, pollTimer = 0, moments = [], extra = [], unchecked = new Set();
let hoverT = null, cursorT = null, fetchSeq = 0, fetchTimer = 0, drawQueued = false;
const key = (m) => `${m.manual ? "m" : "a"}${m.t}`;
const settings = () => ({ count: +$("hlCount").value, before: +$("hlBefore").value, after: +$("hlAfter").value });

// -- states -----------------------------------------------------------------------------
function show(state) {  // "intro" | "working" | "error" | "done"
  setHidden($("hlIntro"), state !== "intro");
  setHidden($("hlProgress"), state !== "working");
  setHidden($("hlError"), state !== "error");
  setHidden($("hlGraphBox"), state === "intro" || state === "error");
  setHidden($("hlResults"), state !== "done");
}

// Called by make.js whenever the video link changes (or the panel opens).
export async function setVideo(newUrl, durationHint) {
  if (newUrl === url) return;
  stopPolling();
  url = newUrl; analysis = null; moments = []; extra = []; unchecked = new Set(); cursorT = hoverT = null;
  untrack($("hlResult"));
  $("hlIntroLen").textContent = durationHint ? estimate(durationHint) : "";
  show("intro");
  // Re-use an analysis of the same link if there is one.
  try {
    const list = await api("/api/highlights");
    const prev = list.find((a) => a.url === url && a.status !== "error");
    if (prev && url === newUrl) await load(prev.id);
  } catch { /* fine, start fresh */ }
}
const estimate = (d) => `This ${dur(d)} video takes about ${d > 5400 ? "a few minutes" : d > 1200 ? "a minute or two" : "a few seconds"} to check.`;

async function load(id) {
  const a = await api(`/api/highlights/${id}`);
  if (a.url !== url) return;
  analysis = a;
  rememberTitle(a.url, a.title);
  render();
  if (a.status === "analyzing") pollSoon();
  if (a.status === "done") fetchMoments(true);
}
function stopPolling() { clearTimeout(pollTimer); pollTimer = 0; }
function pollSoon() {
  stopPolling();
  pollTimer = setTimeout(async () => {
    if (isStopped() || !analysis || analysis.status !== "analyzing") return;
    if (document.hidden) return pollSoon();
    try { await load(analysis.id); } catch (e) { toast(e.message, "error"); pollSoon(); }
  }, 1000);
}

$("hlStart").onclick = () => start($("hlStart"));
$("hlRetry").onclick = () => start($("hlRetry"));
async function start(btn) {
  await busy(btn, "Starting…", async () => {
    try {
      analysis = await post("/api/highlights", { url });
      render(); pollSoon();
    } catch (e) { toast(e.message, "error"); }
  });
}
$("hlCancel").onclick = async () => {
  if (!analysis) return;
  stopPolling();
  try { await api(`/api/highlights/${analysis.id}`, { method: "DELETE" }); } catch {}
  analysis = null; show("intro");
};
$("hlAsLive").onclick = () => document.dispatchEvent(new CustomEvent("clipper:kind", { detail: "live" }));

function render() {
  const a = analysis;
  if (!a) return show("intro");
  if (a.status === "error") {
    show("error");
    setText($("hlErrorText"), a.error || "Something went wrong.");
    setHidden($("hlAsLive"), !/live stream/i.test(a.error || ""));
    return;
  }
  const D = a.duration || a.levels.length;
  if (a.status === "analyzing") {
    show("working");
    const p = a.progress || 0;
    $("hlBarFill").style.transform = `scaleX(${p.toFixed(3)})`;
    setAttr($("hlProgressBar"), "aria-valuenow", Math.round(p * 100));
    setText($("hlStatus"), a.duration ? `Listening to the audio… ${Math.round(p * 100)}%  (${dur(a.levels.length)} of ${dur(D)})`
                                      : `Listening to the audio… ${dur(a.levels.length)} so far`);
  } else show("done");
  setPill(a.status === "analyzing" ? ["warn", `Analysing · ${Math.round((a.progress || 0) * 100)}%`] : ["ok", "Done"]);
  axis(D);
  queueDraw();
}

function setPill([kind, text]) {
  const p = $("hlPill");
  setAttr(p, "class", `pill ${kind}`); setText(p, text);
}

// -- moments ------------------------------------------------------------------------
function fetchMoments(now = false) {
  clearTimeout(fetchTimer);
  fetchTimer = setTimeout(async () => {
    if (!analysis || analysis.status !== "done") return;
    const seq = ++fetchSeq, { count, before, after } = settings();
    try {
      const r = await api(`/api/highlights/${analysis.id}/moments?count=${count}&before=${before}&after=${after}`);
      if (seq !== fetchSeq) return;
      moments = r.moments;
      // Your own picks follow the current before / after settings.
      const D = analysis.duration || analysis.levels.length;
      extra = extra.map((m) => ({ ...m, start: Math.max(0, m.t - before), end: Math.min(D, m.t + after) }));
      renderMoments(); queueDraw();
    } catch (e) { toast(e.message, "error"); }
  }, now ? 0 : 160);
}
const all = () => [...moments, ...extra].sort((a, b) => a.t - b.t);
const chosen = () => all().filter((m) => !unchecked.has(key(m)));

for (const id of ["hlCount", "hlBefore", "hlAfter"]) {
  $(id).addEventListener("input", () => { syncOutputs(); fetchMoments(); });
}
function syncOutputs() {
  const { count, before, after } = settings();
  setText($("hlCountOut"), count); setText($("hlBeforeOut"), `${before}s`); setText($("hlAfterOut"), `${after}s`);
}
syncOutputs();

function renderMoments() {
  const list = all(), box = $("hlList");
  const maxScore = Math.max(1, ...moments.map((m) => m.score || 0));
  box.replaceChildren(...list.map((m, i) => {
    const k = key(m), card = el("div", `moment${unchecked.has(k) ? " off" : ""}`);
    card.dataset.key = k;
    const check = el("label", "m-check");
    const cb = el("input"); cb.type = "checkbox"; cb.checked = !unchecked.has(k);
    cb.setAttribute("aria-label", `Include moment ${i + 1} at ${dur(m.t)}`);
    check.append(cb, el("span", "m-num", String(i + 1)));
    const body = el("div", "m-body");
    const top = el("div", "m-top");
    top.append(el("span", "m-time mono", dur(m.t)),
               el("span", "m-desc", m.manual ? "Your pick" : `${Math.round(m.score)} dB louder than usual`));
    const meter = el("div", "m-meter"); const fill = el("i");
    fill.style.transform = `scaleX(${m.manual ? 0 : clamp((m.score || 0) / maxScore, 0.05, 1).toFixed(3)})`;
    meter.appendChild(fill);
    body.append(top, el("div", "m-range mono", `${dur(m.start)} → ${dur(m.end)} · ${dur(m.end - m.start)} long`), meter);
    const acts = el("div", "m-acts");
    const pv = el("button", "btn sm", "▶ Preview"); pv.type = "button"; pv.dataset.act = "preview";
    acts.appendChild(pv);
    if (m.manual) { const rm = el("button", "btn sm quiet", "Remove"); rm.type = "button"; rm.dataset.act = "remove"; acts.appendChild(rm); }
    card.append(check, body, acts);
    return card;
  }));
  setHidden($("hlNone"), list.length > 0);
  setPill(["ok", `Done · ${plural(list.length, "moment")}`]);
  summarize();
}
function summarize() {
  const c = chosen(), total = c.reduce((s, m) => s + (m.end - m.start), 0);
  setText($("hlSummary"), c.length ? `${plural(c.length, "clip")} · ${dur(total)} in total` : "No moments selected");
  setText($("hlCreate"), c.length ? `Create ${plural(c.length, "clip")}` : "Select a moment first");
  setDisabled($("hlCreate"), !c.length);
  setAttr($("hlGraph"), "aria-label", `Loudness of the whole video, with ${plural(all().length, "moment")} marked`);
}
$("hlList").addEventListener("change", (e) => {
  const card = e.target.closest(".moment"); if (!card) return;
  if (e.target.checked) unchecked.delete(card.dataset.key); else unchecked.add(card.dataset.key);
  card.classList.toggle("off", !e.target.checked);
  summarize(); queueDraw();
});
$("hlList").addEventListener("click", (e) => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const m = all().find((x) => key(x) === b.closest(".moment").dataset.key);
  if (b.dataset.act === "preview") preview(m);
  else { extra = extra.filter((x) => x !== m); renderMoments(); queueDraw(); }
});
function preview(m) {
  if (!playRange(m.start, m.end)) return toast("The preview player isn't available for this link.", "error");
  $("playerBox").scrollIntoView({ block: "nearest", behavior: smooth() });
}
$("hlAdd").onclick = () => {
  if (cursorT == null || !analysis) return;
  const { before, after } = settings(), D = analysis.duration || analysis.levels.length, t = Math.round(cursorT);
  extra.push({ t, start: Math.max(0, t - before), end: Math.min(D, t + after), manual: true, score: 0 });
  cursorT = null; setHidden($("hlAdd"), true);
  renderMoments(); queueDraw();
  toast(`Added a clip around ${dur(t)}.`, "ok");
};

$("hlCreate").onclick = async () => {
  const c = chosen();
  if (!c.length || !analysis) return;
  await busy($("hlCreate"), "Starting…", async () => {
    try {
      const r = await post(`/api/highlights/${analysis.id}/clips`, {
        moments: c.map((m) => ({ start: m.start, end: m.end })), vertical: $("hlVertical").checked });
      trackJobs($("hlResult"), r.jobs);
      $("hlResult").scrollIntoView({ block: "nearest", behavior: smooth() });
      toast(`Making ${plural(r.jobs.length, "clip")}. They appear in My clips as they finish.`, "ok");
      kick();
    } catch (e) { toast(e.message, "error"); }
  });
  summarize();
};

// -- loudness graph -------------------------------------------------------------------
function axis(D) {
  const box = $("hlAxis");
  if (box.dataset.d === String(D)) return;
  box.dataset.d = D;
  box.replaceChildren(...[0, .25, .5, .75, 1].map((f) => el("span", null, dur(D * f))));
}
const queueDraw = () => { if (!drawQueued) { drawQueued = true; requestAnimationFrame(draw); } };
new ResizeObserver(queueDraw).observe($("hlGraph"));
// Colours come from the theme tokens in app.css, so redraw when the theme changes.
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", queueDraw);
document.addEventListener("clipper:theme", queueDraw);

function draw() {
  drawQueued = false;
  const c = $("hlGraph"), a = analysis;
  if (!a || c.offsetParent === null) return;
  const w = c.clientWidth, h = c.clientHeight, dpr = window.devicePixelRatio || 1;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(h * dpr)) { c.width = Math.round(w * dpr); c.height = Math.round(h * dpr); }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  g.clearRect(0, 0, w, h);
  const L = a.levels, D = Math.max(1, a.duration || L.length), x = (t) => (t / D) * w;
  const known = L.filter((v) => v != null);
  if (!known.length && a.status !== "analyzing") return;
  const sorted = known.slice().sort((p, q) => p - q);
  const floor = Math.max(-70, (sorted[Math.floor(sorted.length * 0.02)] ?? -60) - 3), ceil = (sorted.at(-1) ?? 0) + 1;
  const top = 26, mid = top + (h - top) / 2, amp = (h - top) / 2 - 4;
  const sel = chosen(), windows = all();
  const cs = getComputedStyle(document.documentElement), css = (name) => cs.getPropertyValue(name).trim();
  const accent = css("--accent"), markerOff = css("--graph-marker-off");

  // Not analysed yet.
  if (a.status === "analyzing" && L.length < D) {
    g.fillStyle = css("--graph-pending");
    g.fillRect(x(L.length), top, w - x(L.length), h - top);
  }
  // Clip windows.
  for (const m of windows) {
    const on = !unchecked.has(key(m));
    g.fillStyle = on ? css("--graph-window") : css("--graph-window-off");
    g.fillRect(x(m.start), top, Math.max(1, x(m.end) - x(m.start)), h - top);
  }
  // Waveform: one mirrored bar per 3px, showing the loudest second in it.
  // Ink-grey bars; darker inside the parts that become clips.
  const quiet = css("--graph-bar"), hot = css("--graph-bar-on");
  for (let px = 0; px < w; px += 3) {
    const t0 = Math.floor((px / w) * D), t1 = Math.max(t0 + 1, Math.floor(((px + 3) / w) * D));
    let peak = null;
    for (let t = t0; t < t1 && t < L.length; t++) if (L[t] != null && (peak == null || L[t] > peak)) peak = L[t];
    if (peak == null) continue;
    const v = clamp((peak - floor) / (ceil - floor), 0.02, 1), bh = v * amp * 2;
    const inside = sel.some((m) => t0 >= m.start && t0 < m.end);
    g.fillStyle = inside ? hot : quiet;
    g.fillRect(px, mid - bh / 2, 2, bh);
  }
  // Markers.
  g.font = "600 11px " + (css("--f-mono") || "monospace");
  g.textAlign = "center"; g.textBaseline = "middle";
  windows.forEach((m, i) => {
    const on = !unchecked.has(key(m)), mx = x(m.t);
    g.strokeStyle = on ? accent : markerOff; g.lineWidth = 1.5;
    g.beginPath(); g.moveTo(mx, top - 2); g.lineTo(mx, h); g.stroke();
    g.fillStyle = on ? accent : markerOff;
    g.beginPath(); g.arc(mx, 12, 10, 0, Math.PI * 2); g.fill();
    g.fillStyle = on ? css("--accent-fg") : "#fff"; g.fillText(String(i + 1), mx, 12.5);
  });
  // Hover and the clicked spot.
  for (const [t, color] of [[hoverT, css("--graph-hover")], [cursorT, css("--text")]]) {
    if (t == null) continue;
    g.strokeStyle = color; g.lineWidth = 1;
    g.beginPath(); g.moveTo(x(t), top); g.lineTo(x(t), h); g.stroke();
  }
}
const graphTime = (e) => {
  const r = $("hlGraph").getBoundingClientRect(), D = analysis ? Math.max(1, analysis.duration || analysis.levels.length) : 1;
  return { t: clamp((e.clientX - r.left) / r.width, 0, 1) * D, px: e.clientX - r.left, D, w: r.width };
};
const nearMarker = (px, D, w) => all().find((m) => Math.abs((m.t / D) * w - px) < 9);
$("hlGraph").addEventListener("pointermove", (e) => {
  if (!analysis) return;
  const { t, px, D, w } = graphTime(e), m = nearMarker(px, D, w), tip = $("hlTip");
  hoverT = t;
  const db = analysis.levels[Math.floor(t)];
  tip.textContent = m ? `Moment ${all().indexOf(m) + 1} · ${dur(m.t)} — click to preview`
                      : `${dur(t)}${db != null ? ` · ${Math.round(db)} dB` : ""} — click to add a clip here`;
  tip.style.left = `${clamp(px, 60, w - 60)}px`;
  tip.hidden = false;
  queueDraw();
});
$("hlGraph").addEventListener("pointerleave", () => { hoverT = null; $("hlTip").hidden = true; queueDraw(); });
$("hlGraph").addEventListener("click", (e) => {
  if (!analysis) return;
  const { t, px, D, w } = graphTime(e), m = nearMarker(px, D, w);
  if (m) return preview(m);
  if (analysis.status !== "done") return;
  cursorT = t;
  if (player.ready) player.seek(t);
  const add = $("hlAdd");
  add.textContent = `＋ Add a clip around ${dur(t)}`;
  add.style.left = `${clamp(px, 90, w - 90)}px`;
  add.hidden = false;
  queueDraw();
});
