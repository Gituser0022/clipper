// "Recording": one monitor per live recorder, with big clip buttons, the rewind
// buffer drawn as a timeline (auto-clip triggers marked on it) and a stable embed.
import { $, api, post, el, setText, setHidden, setDisabled, setAttr, setClass, setStyle, clamp, dur, tc, clock, plural,
         toast, busy, reconcile, kick, store, onState, smooth, isStopped } from "./core.js";
import { autoSettings, whenRecordingStarts } from "./make.js";

const STATUS = { recording: "Recording", ended: "Stream ended", error: "Stopped by an error", stopped: "Stopped" };
const nodes = new Map();
let reveal = null;
whenRecordingStarts((rec) => { reveal = rec.id; });

// One "vertical" setting for manual live clips, mirrored on every monitor.
export function setLiveVertical(v) {
  $("liveVertical").checked = v;
  for (const n of nodes.values()) n.vertical.checked = v;
}
$("liveVertical").addEventListener("change", (e) => setLiveVertical(e.target.checked));

function make(r) {
  const root = $("recTpl").content.firstElementChild.cloneNode(true), q = (s) => root.querySelector(s);
  root.dataset.id = r.id;
  const n = {
    el: root, r, evKey: "", fillVal: -1,
    tally: q(".tally"), state: q(".mon-state-text"), pill: q(".mon-state"), tc: q(".mon-tc"), url: q(".mon-url"), bufTxt: q(".buf-txt"), fill: q(".buf-fill"),
    sel: q(".buf-sel"), marks: q(".buf-marks"), t0: q(".t0"), t1: q(".t1"), err: q(".mon-err"), watch: q(".watch"),
    clips: [...root.querySelectorAll('[data-act="clip"]')], vertical: q(".rec-vertical"), wait: q(".mon-wait"),
    auto: q(".auto"), mode: q(".auto-mode"), vol: q(".auto-vol"), volBar: q(".vu i"), volTxt: q(".auto-vol b"),
    chat: q(".auto-chat"), chatTxt: q(".auto-chat b"), count: q(".auto-count b"), pending: q(".auto-pending"), events: q(".events"),
    watchBtn: q('[data-act="watch"]'), autoBtn: q('[data-act="auto"]'), stopBtn: q('[data-act="stop"]'), delBtn: q('[data-act="del"]'),
  };
  n.vertical.checked = $("liveVertical").checked;
  // Hovering a clip button shows the part of the buffer it would save.
  for (const b of n.clips) {
    const show = () => {
      const cap = n.r.buffer_seconds || 1;
      setStyle(n.sel, "width", `${clamp(Math.min(+b.dataset.s, n.r.buffered) / cap, 0, 1) * 100}%`);
      n.sel.classList.add("show");
    };
    const hide = () => n.sel.classList.remove("show");
    b.addEventListener("pointerenter", show); b.addEventListener("focus", show);
    b.addEventListener("pointerleave", hide); b.addEventListener("blur", hide);
  }
  return n;
}

function tick(n) {  // the parts that move with the clock
  const r = n.r, now = Date.now() / 1000, cap = r.buffer_seconds || 1;
  if (r.status === "recording" && r.started_at) setText(n.tc, tc(now - r.started_at));
  for (const m of n.marks.children) {
    const x = 100 - ((now - Number(m.dataset.t)) / cap) * 100;
    setHidden(m, x < 0);
    setStyle(m, "left", `${clamp(x, 0, 100)}%`);
  }
}

function update(n, r) {
  n.r = r;
  const live = r.status === "recording";
  setClass(n.el, "is-rec", live);
  setAttr(n.tally, "class", `tally${live ? " on" : r.status === "error" ? " err" : ""}`);
  setText(n.state, STATUS[r.status] || r.status);
  setAttr(n.pill, "class", `pill mon-state ${live ? "live" : r.status === "error" ? "err" : ""}`);
  setText(n.url, r.url); setAttr(n.url, "title", r.url);
  setAttr(n.url, "href", /^https?:\/\//i.test(r.url) ? r.url : null);
  if (!r.started_at) setText(n.tc, "--:--:--");
  setText(n.bufTxt, `${dur(r.buffered)} saved of ${dur(r.buffer_seconds)}`);
  setText(n.t0, `${dur(r.buffer_seconds)} ago`); setText(n.t1, `${dur(r.buffer_seconds / 2)} ago`);
  const fill = Math.round(Math.min(1, r.buffer_seconds ? r.buffered / r.buffer_seconds : 0) * 1000) / 1000;
  if (fill !== n.fillVal) { n.fillVal = fill; n.fill.style.transform = `scaleX(${fill})`; }
  setText(n.err, r.error); setHidden(n.err, !r.error);
  for (const b of n.clips) setDisabled(b, !r.buffered);
  setHidden(n.wait, r.buffered || !live);
  setHidden(n.watchBtn, !r.youtube_id);
  setText(n.watchBtn, n.watch.firstChild ? "Hide video" : "▶ Watch here");
  setHidden(n.autoBtn, !live);
  setAttr(n.autoBtn, "aria-pressed", String(!!r.auto));
  setText(n.autoBtn, r.auto ? "Auto-clip: on" : "Auto-clip: off");
  setHidden(n.stopBtn, !live); setHidden(n.delBtn, live);

  const a = r.auto, events = a ? a.events : [];
  setHidden(n.auto, !a);
  const evKey = events.map((e) => e.time).join();
  if (evKey !== n.evKey) {
    n.evKey = evKey;
    const label = (e) => `${e.kind === "loud" ? "Loud moment" : "Chat spike"} (${e.detail})`;
    n.marks.replaceChildren(...events.map((e) => {
      const m = el("span", `mark ${e.kind}`, e.kind === "loud" ? "🔊" : "💬");
      m.dataset.t = e.time; m.title = `${clock(e.time)} · ${label(e)}`; m.setAttribute("aria-hidden", "true");
      return m;
    }));
    n.events.replaceChildren(...events.slice().reverse().map((e) => {
      const li = el("li"); li.append(el("time", "mono", clock(e.time)), label(e));
      return li;
    }));
  }
  tick(n);
  if (!a) return;
  const what = [a.audio && "loud moments", a.chat && "busy chat"].filter(Boolean).join(" and ");
  setText(n.mode, `Auto-clipping ${what} · ${a.before}s before, ${a.after}s after${a.vertical ? " · 9:16" : ""}`);
  setHidden(n.vol, !a.audio);
  setText(n.volTxt, a.level_db == null ? "–" : `${a.level_db} dB`);
  setStyle(n.volBar, "transform", `scaleX(${a.level_db == null ? 0 : clamp((a.level_db + 60) / 60, 0, 1).toFixed(3)})`);
  setHidden(n.chat, !a.chat);
  const chatOk = a.chat_status === "connected";
  setText(n.chatTxt, `${a.chat_status}${chatOk ? ` · ${a.chat_rate} msgs/10s` : ""}`);
  setAttr(n.chat, "title", `Chat ${a.chat_status}`);
  const chatBusy = /^(connecting|starting)/.test(a.chat_status);
  setAttr(n.chatTxt, "class", `pill ${chatOk ? "ok" : chatBusy ? "warn" : "err"}`);
  setText(n.count, plural(a.clips, "auto clip"));
  setHidden(n.pending, !a.pending);
}

onState((s) => {
  reconcile($("recorders"), nodes, s.recs.slice().reverse(), make, update);
  setHidden($("recEmpty"), s.recs.length);
  setHidden($("recHint"), !s.recording);
  if (reveal && nodes.has(reveal)) {
    nodes.get(reveal).el.scrollIntoView({ block: "start", behavior: smooth() });
    reveal = null;
  }
});
// Timecodes and trigger markers move every second while something is recording.
setInterval(() => {
  if (isStopped() || !store.recording || document.hidden) return;
  for (const n of nodes.values()) if (n.r.status === "recording") tick(n);
}, 1000);

export function clipLive(r, before, after = 0) {
  const what = `the last ${before}s${after ? ` and the next ${after}s` : ""}`;
  return post(`/api/live/${r.id}/clip`, { seconds_before: before, seconds_after: after, vertical: $("liveVertical").checked })
    .then(() => { toast(`Saving ${what}. It'll be in My clips in a moment.`, "cut"); kick(); });
}
// The C shortcut: newest stream that is recording and has something buffered.
export function clipNewest() {
  const r = [...store.recs].reverse().find((x) => x.status === "recording" && x.buffered);
  if (r) return clipLive(r, 30).catch((err) => toast(err.message, "error"));
  toast(store.recs.some((x) => x.status === "recording") ? "Nothing recorded yet. Give it a few seconds."
                                                         : "Nothing is recording. Paste a live stream link to start.");
}

$("recorders").addEventListener("change", (e) => {
  if (e.target.classList.contains("rec-vertical")) setLiveVertical(e.target.checked);
});
$("recorders").addEventListener("click", async (e) => {
  const b = e.target.closest("button[data-act]"); if (!b) return;
  const id = b.closest(".mon").dataset.id, r = store.recs.find((x) => x.id === id), n = nodes.get(id);
  if (!r) return;
  try {
    switch (b.dataset.act) {
      case "clip": await clipLive(r, +b.dataset.s, +(b.dataset.a || 0)); break;
      case "stop": await busy(b, "Stopping…", () => post(`/api/live/${id}/stop`)); kick(); break;
      case "del": await api(`/api/live/${id}`, { method: "DELETE" }); kick(); break;
      case "auto":
        await busy(b, null, () => post(`/api/live/${id}/auto`, r.auto ? { audio: false, chat: false }
                                                                         : { ...autoSettings(), audio: $("autoLoud").checked || !$("autoChat").checked,
                                                                             chat: $("autoChat").checked || !$("autoLoud").checked }));
        kick(); break;
      case "watch":
        if (n.watch.firstChild) n.watch.replaceChildren();
        else {
          const box = el("div", "screen"), f = document.createElement("iframe");
          f.src = `https://www.youtube.com/embed/${encodeURIComponent(r.youtube_id)}?autoplay=1&mute=1&playsinline=1`;
          f.allow = "autoplay; fullscreen; picture-in-picture"; f.title = "Live stream video";
          box.appendChild(f); n.watch.appendChild(box);
        }
        update(n, r); break;
    }
  } catch (err) { toast(err.message, "error"); }
});
