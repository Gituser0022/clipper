// "Make a clip": 1) paste a link, 2) say what it is / how to clip it, 3) do it.
import { $, api, post, el, setText, setHidden, setClass, setStyle, setAttr, clamp, fmt, dur, parseTime, toast, busy,
         trackJobs, untrack, kick, go, smooth, isStopped, rememberTitle } from "./core.js";
import { player, onPlayerChange, loadPlayer, resetPlayer, playRange } from "./player.js";
import { setVideo } from "./highlights.js";

let linkUrl = "", info = null, kind = null, pickedKind = null, method = "best", inspectSeq = 0;
export const state = { get kind() { return kind; }, get method() { return method; } };
const looksLikeVideo = (u) => /youtube\.com\/(watch|shorts\/)|youtu\.be\/|twitch\.tv\/videos\/|^file:/i.test(u);

// -- step 1: the link -------------------------------------------------------------------
async function inspectLink(force = false) {
  const url = $("link").value.trim();
  if (url === linkUrl && !force) return;
  if (!url) return resetLink();
  if (!/^\w[\w+.-]*:\/\/\S+$/.test(url)) { setText($("linkMsg"), "That doesn't look like a link. Paste one starting with https://"); return; }
  setText($("linkMsg"), "");
  linkUrl = url; info = null; pickedKind = null; method = "best";  // every new link starts on the recommended path
  const seq = ++inspectSeq;
  resetPlayer(); $("vodStart").value = ""; $("vodEnd").value = "";
  untrack($("vodResult"));
  setKind(looksLikeVideo(url) ? "video" : null);  // a good guess shows tools straight away
  setClass($("step1"), "done", true);
  $("step2").scrollIntoView({ block: "nearest", behavior: smooth() });
  try {
    const r = await api(`/api/inspect?url=${encodeURIComponent(url)}`);
    if (seq !== inspectSeq) return;
    info = r;
    rememberTitle(url, r.title);
    if (!pickedKind) setKind(r.is_live ? "live" : "video"); else renderSource();
  } catch (e) {
    if (seq !== inspectSeq) return;
    info = { error: e.message };
    if (!kind) setKind("video"); else renderSource();
  }
  updateTimeline();
}
export function resetLink() {
  linkUrl = ""; info = null; kind = null; pickedKind = null; inspectSeq++;
  resetPlayer(); $("vodStart").value = ""; $("vodEnd").value = "";
  setText($("vodMsg"), ""); setText($("liveMsg"), ""); setText($("linkMsg"), "");
  setClass($("step1"), "done", false);
  for (const id of ["step2", "step3"]) setHidden($(id), true);
  setHidden($("explain"), false);
}
// Continue re-checks the same link only if looking it up failed.
$("linkForm").onsubmit = (e) => { e.preventDefault(); inspectLink(!!info?.error); };
$("link").addEventListener("change", () => inspectLink());
$("link").addEventListener("paste", () => setTimeout(() => inspectLink(), 0));
$("linkClear").onclick = () => { $("link").value = ""; resetLink(); $("link").focus(); };

// -- step 2: what is it, and how to clip it ----------------------------------------------
function setKind(k, byUser = false) {
  if (byUser) pickedKind = k;
  kind = k;
  setHidden($("explain"), true);
  setHidden($("step2"), !linkUrl);
  $("kindVideo").checked = k === "video"; $("kindLive").checked = k === "live";
  setHidden($("videoChoice"), k !== "video");
  setHidden($("liveSetup"), k !== "live");
  setText($("step2Title"), k === "live" ? "Record this live stream" : k === "video" ? "How do you want to clip it?" : "Checking your link…");
  setMethod(method);
  renderSource();
}
document.addEventListener("clipper:kind", (e) => { setKind(e.detail, true); $("step2").scrollIntoView({ block: "start", behavior: smooth() }); });
for (const r of document.querySelectorAll('input[name="kind"]')) r.addEventListener("change", (e) => setKind(e.target.value, true));

function setMethod(m) {
  method = m;
  $("methodBest").checked = m === "best"; $("methodManual").checked = m === "manual";
  const on = kind === "video" && !!linkUrl;
  setHidden($("step3"), !on);
  setHidden($("bestPanel"), m !== "best"); setHidden($("manualPanel"), m !== "manual");
  setText($("step3Title"), m === "best" ? "Find the best moments" : "Choose the part to keep");
  if (on) {
    loadPlayer(linkUrl);
    if (m === "best") setVideo(linkUrl, info && !info.is_live ? info.duration : 0);
    updateTimeline();
  }
}
for (const r of document.querySelectorAll('input[name="method"]')) r.addEventListener("change", (e) => setMethod(e.target.value));

function renderSource() {
  const host = (() => {
    try { const u = new URL(linkUrl); return u.protocol === "file:" ? "Local file" : u.hostname.replace(/^www\./, "") || linkUrl; }
    catch { return linkUrl; }
  })();
  const t = $("srcThumb");
  setClass(t, "loading", info === null);
  setStyle(t, "backgroundImage", info?.thumbnail ? `url("${String(info.thumbnail).replace(/["\\]/g, "")}")` : "");
  setText($("srcTitle"), info?.title || (info === null ? "Looking up your link…" : host));
  setAttr($("srcTitle"), "title", info?.title || linkUrl);
  const meta = $("srcMeta");
  meta.replaceChildren();
  if (kind === "live") meta.appendChild(el("span", "tag live", "● Live"));
  else if (kind === "video") meta.appendChild(el("span", "tag", "Video"));
  if (info?.duration && kind === "video") meta.appendChild(el("span", "mono", dur(info.duration)));
  meta.appendChild(el("span", null, host));
  const note = $("srcNote");
  if (info?.error) { note.textContent = `We couldn't look this link up (${info.error}). If it's a live stream, choose “Live stream”.`; note.className = "src-note warn"; }
  else if (info) { note.textContent = `Clipper thinks this is ${info.is_live ? "a live stream" : "a video"}${pickedKind ? " — you changed it" : ""}. Not right? Switch it here:`; note.className = "src-note"; }
  else { note.textContent = "Checking whether it's a video or a live stream…"; note.className = "src-note"; }
}

// -- step 3 (manual): timeline with draggable in / out handles ---------------------------
const tlDur = () => player.duration || (info && !info.is_live && info.duration) || 0;
function selection() {
  const d = tlDur(), a = parseTime($("vodStart").value), b = parseTime($("vodEnd").value);
  return { a, b, start: a ?? 0, end: b ?? d, d };
}
let ticksFor = -1;
export function updateTimeline() {
  const { a, b, start, end, d } = selection(), tl = $("tl");
  setClass(tl, "unknown", !d);
  setText($("vodLen"), a != null || b != null ? (end > start ? fmt(end - start) : "out is before in") : "–");
  if (!d) return;
  const pct = (t) => `${clamp(t / d, 0, 1) * 100}%`;
  setStyle($("tlIn"), "left", pct(start)); setStyle($("tlOut"), "left", pct(end));
  setStyle($("tlRange"), "left", pct(Math.min(start, end)));
  setStyle($("tlRange"), "width", `${clamp(Math.abs(end - start) / d, 0, 1) * 100}%`);
  setText($("tlInTip"), fmt(start)); setText($("tlOutTip"), fmt(end));
  for (const [h, v, label] of [[$("tlIn"), start, "start"], [$("tlOut"), end, "end"]]) {
    setAttr(h, "aria-valuemin", 0); setAttr(h, "aria-valuemax", Math.round(d));
    setAttr(h, "aria-valuenow", Math.round(v * 10) / 10); setAttr(h, "aria-valuetext", `Clip ${label} ${fmt(v)}`);
  }
  if (ticksFor !== d) {
    ticksFor = d;
    $("tlTicks").replaceChildren(...[0, .25, .5, .75, 1].map((f) => el("span", null, dur(d * f))));
  }
}
onPlayerChange(updateTimeline);
function setPoint(which, t, seek = true) {
  const d = tlDur(), { start, end } = selection();
  t = which === "in" ? clamp(t, 0, Math.max(0, end - 0.1)) : clamp(t, start + 0.1, d || Infinity);
  $(which === "in" ? "vodStart" : "vodEnd").value = fmt(t);
  updateTimeline();
  if (seek && player.ready) seekSoon(t);
}
let seekTimer = 0, seekTo = 0;
function seekSoon(t) {  // throttled: YouTube dislikes a seek per pixel of dragging
  seekTo = t;
  if (!seekTimer) seekTimer = setTimeout(() => { seekTimer = 0; player.seek(seekTo); }, 120);
}
const timeAt = (x) => { const r = $("tlTrack").getBoundingClientRect(); return clamp((x - r.left) / r.width, 0, 1) * tlDur(); };
for (const [id, which] of [["tlIn", "in"], ["tlOut", "out"]]) {
  const h = $(id);
  h.addEventListener("pointerdown", (e) => { e.preventDefault(); e.stopPropagation(); h.setPointerCapture(e.pointerId); h.focus(); h.dataset.drag = "1"; });
  h.addEventListener("pointermove", (e) => { if (h.dataset.drag) setPoint(which, timeAt(e.clientX)); });
  const stop = () => { delete h.dataset.drag; };
  h.addEventListener("pointerup", stop); h.addEventListener("pointercancel", stop);
  h.addEventListener("keydown", (e) => {
    const { start, end, d } = selection(), cur = which === "in" ? start : end, step = e.shiftKey ? 10 : 1;
    const to = { ArrowLeft: cur - step, ArrowDown: cur - step, ArrowRight: cur + step, ArrowUp: cur + step, Home: 0, End: d }[e.key];
    if (to == null) return;
    e.preventDefault(); setPoint(which, to);
  });
}
$("tlTrack").addEventListener("pointerdown", (e) => {
  if (!tlDur() || e.target.closest(".tl-handle")) return;
  const t = timeAt(e.clientX);
  if (player.ready) { player.seek(t); setStyle($("tlHead"), "left", `${(t / tlDur()) * 100}%`); }
  else { const { start, end } = selection(); setPoint(Math.abs(t - start) < Math.abs(t - end) ? "in" : "out", t, false); }
});
// Playhead: only runs while the manual panel is showing.
setInterval(() => {
  if (isStopped() || !player.ready || kind !== "video" || method !== "manual" || document.hidden) return;
  const d = tlDur();
  if (d) setStyle($("tlHead"), "left", `${clamp(player.time() / d, 0, 1) * 100}%`);
}, 250);

function flash(input) { input.classList.add("flash"); setTimeout(() => input.classList.remove("flash"), 400); }
// I / O keys and the "now" buttons.
export function mark(which) {
  if (!player.ready || kind !== "video") return false;
  if (method !== "manual") setMethod("manual");
  setPoint(which, player.time(), false);
  flash($(which === "in" ? "vodStart" : "vodEnd"));
  return true;
}
$("markIn").onclick = () => mark("in");
$("markOut").onclick = () => mark("out");
$("playSel").onclick = () => {
  const { a, b, start, end } = selection();
  if ((a == null && b == null) || end <= start) return setText($("vodMsg"), "Set where the clip starts and ends first.");
  setText($("vodMsg"), "");
  if (!playRange(start, end)) setText($("vodMsg"), "The preview player isn't available for this link.");
};
$("vodStart").addEventListener("input", updateTimeline);
$("vodEnd").addEventListener("input", updateTimeline);
for (const id of ["vodStart", "vodEnd"]) $(id).addEventListener("keydown", (e) => { if (e.key === "Enter") $("vodGo").click(); });

$("vodGo").onclick = () => {
  const msg = $("vodMsg"), { a, b, start, end, d } = selection();
  const sIn = $("vodStart").value.trim(), sOut = $("vodEnd").value.trim();
  const problem = !linkUrl ? "Paste a link first."
    : !sIn && !sOut ? "Set where the clip starts and ends: drag the red handles, press I and O while it plays, or type the times."
    : sIn && a == null ? "The start isn't a time. Try 90, 1:30 or 1:02:03."
    : sOut && b == null ? "The end isn't a time. Try 90, 1:30 or 1:02:03."
    : !sOut && !d ? "Set where the clip ends too."
    : end <= start ? "The end must be after the start." : "";
  setText(msg, problem);
  if (problem) return;
  // An empty field means "from the beginning" / "to the end".
  const startStr = sIn || fmt(start), endStr = sOut || fmt(end);
  busy($("vodGo"), "Starting…", async () => {
    try {
      const job = await post("/api/vod/clip", { url: linkUrl, start: startStr, end: endStr,
                                                 precise: $("vodPrecise").checked, vertical: $("vodVertical").checked });
      trackJobs($("vodResult"), [job]);
      toast(`Making your clip (${startStr} → ${endStr}).`, "ok");
      kick();
    } catch (err) { setText(msg, err.message); }
  });
};

// -- step 2 (live): start recording ----------------------------------------------------------
export function autoSettings() {
  const n = (id, d) => { const v = clamp(Number($(id).value) || d, 5, 600); $(id).value = v; return v; };
  const on = $("autoOn").checked;
  return { audio: on && $("autoLoud").checked, chat: on && $("autoChat").checked,
           before: n("autoBefore", 60), after: n("autoAfter", 60), vertical: $("liveVertical").checked };
}
function syncBuffer() {
  const v = Number($("liveBuf").value);
  for (const c of $("bufChips").children) setAttr(c, "aria-pressed", String(v === Number(c.dataset.min)));
}
$("bufChips").addEventListener("click", (e) => { const c = e.target.closest("[data-min]"); if (c) { $("liveBuf").value = c.dataset.min; syncBuffer(); } });
$("liveBuf").addEventListener("input", syncBuffer);
syncBuffer();
const autoSummary = () => setText($("autoSummary"), `More options · auto-clips keep ${clamp(+$("autoBefore").value || 60, 5, 600)}s before and ${clamp(+$("autoAfter").value || 60, 5, 600)}s after`);
$("autoBefore").addEventListener("input", autoSummary);
$("autoAfter").addEventListener("input", autoSummary);
autoSummary();
$("autoOn").addEventListener("change", () => { for (const id of ["autoLoud", "autoChat"]) $(id).disabled = !$("autoOn").checked; });

let onStarted = () => {};
export const whenRecordingStarts = (fn) => { onStarted = fn; };
$("liveGo").onclick = () => {
  const msg = $("liveMsg");
  if (!linkUrl) return setText(msg, "Paste a link first.");
  setText(msg, "");
  // Empty or out-of-range buffer falls back to 5 minutes / clamps to 1–180.
  const minutes = clamp(Number($("liveBuf").value) || 5, 1, 180);
  $("liveBuf").value = minutes; syncBuffer();
  busy($("liveGo"), "Starting…", async () => {
    try {
      const rec = await post("/api/live/start", { url: linkUrl, buffer_seconds: Math.round(minutes * 60), auto: autoSettings() });
      toast("Recording started. Clip from here whenever something happens.", "ok");
      $("link").value = ""; resetLink();
      onStarted(rec); kick(); go("recording");
    } catch (err) { setText(msg, err.message); }
  });
};
