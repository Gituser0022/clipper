// Shared helpers: DOM writes, formatting, the API, toasts, and the polling loop
// that keeps `store` (recorders + clip jobs) up to date.

export const $ = (id) => document.getElementById(id);

// Write to the DOM only when a value actually changes (keeps polling cheap).
export const setText = (el, v) => { v = String(v ?? ""); if (el.textContent !== v) el.textContent = v; };
export const setHidden = (el, h) => { h = !!h; if (el.hidden !== h) el.hidden = h; };
export const setDisabled = (el, d) => { d = !!d; if (el.disabled !== d) el.disabled = d; };
export const setAttr = (el, k, v) => {
  if (v == null) { if (el.hasAttribute(k)) el.removeAttribute(k); }
  else if (el.getAttribute(k) !== String(v)) el.setAttribute(k, v);
};
export const setClass = (el, c, on) => { if (el.classList.contains(c) !== !!on) el.classList.toggle(c, !!on); };
export const setStyle = (el, k, v) => { if (el.style[k] !== v) el.style[k] = v; };
export const el = (tag, cls, text) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  return e;
};

export const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
// 83.5 -> "1:23.5" (what the time fields hold)
export const fmt = (s) => {
  s = Math.max(0, s);
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = (s % 60).toFixed(1).padStart(4, "0");
  return (h ? `${h}:${String(m).padStart(2, "0")}` : `${m}`) + `:${x}`;
};
// 83.5 -> "1:24"
export const dur = (s) => {
  s = Math.round(s);
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, x = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${x}` : `${m}:${x}`;
};
// 83 -> "00:01:23"
export const tc = (s) => {
  s = Math.max(0, Math.floor(s));
  return [s / 3600, (s / 60) % 60, s % 60].map((n) => String(Math.floor(n)).padStart(2, "0")).join(":");
};
export const clock = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
export const plural = (n, word, many = `${word}s`) => `${n} ${n === 1 ? word : many}`;

// Same rules as clipper/timeparse.py: "90", "1:30", "1:02:03.5". Returns null when invalid.
export function parseTime(text) {
  const parts = String(text).trim().split(":");
  if (!String(text).trim() || parts.length > 3) return null;
  let s = 0;
  for (const p of parts) {
    const n = Number(p.trim());
    if (p.trim() === "" || !Number.isFinite(n)) return null;
    s = s * 60 + n;
  }
  return s >= 0 ? s : null;
}

export const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)");
export const smooth = () => (reduceMotion.matches ? "auto" : "smooth");

// -- API ----------------------------------------------------------------------------
function errorMessage(detail, fallback) {
  if (Array.isArray(detail)) {
    const say = (e) => {
      const f = e.loc?.at(-1);
      return f === "url" ? "Paste a link starting with https://"
        : `${String(f ?? "").replace(/_/g, " ")}: ${e.msg}`.replace(/^: /, "");
    };
    return [...new Set(detail.map(say))].join("\n");
  }
  return (typeof detail === "string" && detail) || fallback;
}
export async function api(path, opts = {}) {
  let res;
  try { res = await fetch(path, { headers: { "Content-Type": "application/json" }, ...opts }); }
  catch { throw new Error("Can't reach Clipper. Is it still running?"); }
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(errorMessage(body.detail, res.statusText || `Error ${res.status}`));
  return body;
}
export const post = (path, data) => api(path, { method: "POST", body: JSON.stringify(data || {}) });

// -- feedback -------------------------------------------------------------------------
export function toast(msg, kind = "info", ms = kind === "error" ? 6500 : 2800) {
  const box = $("toasts"), t = el("div", `toast ${kind}`, msg);
  if (kind === "error") t.setAttribute("role", "alert");
  box.appendChild(t);
  while (box.children.length > 3) box.firstElementChild.remove();
  setTimeout(() => t.remove(), ms);
}
export async function busy(btn, label, fn) {
  const old = btn.innerHTML;
  btn.disabled = true;
  if (label) btn.textContent = label;
  try { return await fn(); } finally { btn.disabled = false; if (label) btn.innerHTML = old; }
}

// Put keyed nodes into `box` in order, creating each once and moving only the ones
// that are out of place (moving a node that holds a <video> or <iframe> would
// interrupt it). `nodes` maps id -> {el, ...}.
export function reconcile(box, nodes, items, make, update) {
  const keep = new Set(items.map((x) => x.id));
  for (const [id, n] of nodes) if (!keep.has(id)) { n.el.remove(); nodes.delete(id); }
  items.forEach((x, i) => {
    let n = nodes.get(x.id);
    if (!n) { n = make(x); nodes.set(x.id, n); }
    update(n, x);
    const at = box.children[i];
    if (at !== n.el) box.insertBefore(n.el, at || null);
  });
}

// -- navigation -------------------------------------------------------------------------
export const go = (view) => { if (location.hash !== `#/${view}`) location.hash = `#/${view}`; };

// -- polling --------------------------------------------------------------------------
// One request per tick for recorders + jobs. Fast while something is recording or
// being made, backing off to 10s when idle, paused while the tab is hidden.
export const store = { recs: [], jobs: [], recording: 0, running: 0, offline: false };
const listeners = [];
export const onState = (fn) => listeners.push(fn);

let pollTimer = 0, inflight = false, again = false, stopped = false, idle = 0, lastText = "", useState = true;

async function fetchState() {
  if (useState) {
    const res = await fetch("/api/state", { cache: "no-store" });
    if (res.status !== 404) { if (!res.ok) throw new Error(res.statusText); return res.text(); }
    useState = false;  // older server without /api/state
  }
  const [l, j] = await Promise.all(["/api/live", "/api/jobs"].map((p) => fetch(p, { cache: "no-store" }).then((r) => {
    if (!r.ok) throw new Error(r.statusText);
    return r.text();
  })));
  return `{"live":${l},"jobs":${j}}`;
}
function nextDelay() {
  if (store.offline) return Math.min(10000, 2000 * 2 ** Math.min(idle++, 3));
  if (store.running) { idle = 0; return 1000; }
  if (store.recording) { idle = 0; return 1500; }
  return [2000, 3000, 5000, 8000, 10000][Math.min(idle++, 4)];
}
function schedule(ms) {
  clearTimeout(pollTimer);
  if (!stopped && !document.hidden) pollTimer = setTimeout(refresh, ms);
}
export async function refresh() {
  if (stopped) return;
  if (inflight) { again = true; return; }
  inflight = true;
  clearTimeout(pollTimer);
  let changed = false;
  try {
    const text = await fetchState();
    if (stopped) return;
    if (store.offline) { store.offline = false; idle = 0; changed = true; }
    if (text !== lastText) {
      lastText = text;
      const s = JSON.parse(text);
      store.recs = s.live; store.jobs = s.jobs; store.storage = s.storage || null;
      store.recording = s.live.filter((r) => r.status === "recording").length;
      store.running = s.jobs.filter((j) => j.status === "running").length;
      changed = true;
    }
  } catch {
    if (!store.offline) changed = true;
    store.offline = true;
  } finally {
    inflight = false;
  }
  if (stopped) return;
  if (changed) for (const fn of listeners) fn(store);
  if (again) { again = false; return refresh(); }
  schedule(nextDelay());
}
// Something just changed: poll right away and go back to the fast pace.
export function kick() { idle = 0; refresh(); }
export function stopPolling() { stopped = true; clearTimeout(pollTimer); }
export const isStopped = () => stopped;
document.addEventListener("visibilitychange", () => { if (document.hidden) clearTimeout(pollTimer); else kick(); });

// -- "your clips are being made" banners ------------------------------------------------
// Shows live progress for a batch of jobs started from a page, e.g. "3 of 10 ready".
const trackers = new Map();  // banner element -> job ids
export function trackJobs(box, jobs) {
  trackers.set(box, jobs.map((j) => j.id));
  for (const j of jobs) if (!store.jobs.some((x) => x.id === j.id)) store.jobs.unshift(j);
  renderTracker(box);
}
function renderTracker(box) {
  const ids = trackers.get(box), js = ids.map((id) => store.jobs.find((j) => j.id === id)).filter(Boolean);
  const n = ids.length, done = js.filter((j) => j.status === "done").length, failed = js.filter((j) => j.status === "error").length;
  const working = n - done - failed;
  box.hidden = false;
  const state = working ? "working" : failed && !done ? "failed" : "ready";
  box.classList.add("result"); box.classList.remove("working", "failed", "ready"); box.classList.add(state);  // keep the box's own classes
  const text = working ? `Making ${plural(n, "clip")}… ${done} of ${n} ready`
    : failed ? `${done} of ${n} ready · ${failed} couldn't be made` : `${n === 1 ? "Your clip is" : `All ${n} clips are`} ready`;
  const key = `${state}|${text}`;
  if (box.dataset.key === key) return;
  box.dataset.key = key;
  box.replaceChildren(el("span", working ? "spinner" : "result-icon", working ? null : failed && !done ? "!" : "✓"),
                      el("span", "result-text", text));
  const a = el("a", "btn sm", "See my clips →"); a.href = "#/clips";
  box.appendChild(a);
}
onState(() => { for (const box of trackers.keys()) renderTracker(box); });
export function untrack(box) { trackers.delete(box); box.hidden = true; box.dataset.key = ""; box.classList.remove("working", "failed", "ready"); }

// -- readable names for links ----------------------------------------------------------
// Titles we learned while inspecting links, so clip cards can say "My video" rather
// than a long URL. Kept in this browser only (a convenience; safe to lose).
const TITLES_KEY = "clipper.titles";
const titles = (() => { try { return JSON.parse(localStorage.getItem(TITLES_KEY)) || {}; } catch { return {}; } })();
export function rememberTitle(url, title) {
  if (!url || !title || title === url || titles[url] === title) return;
  titles[url] = title;
  try {
    const keep = Object.entries(titles).slice(-200);
    localStorage.setItem(TITLES_KEY, JSON.stringify(Object.fromEntries(keep)));
  } catch { /* private mode etc. */ }
}
export function prettySource(src) {
  if (titles[src]) return titles[src];
  try {
    const u = new URL(src);
    if (u.protocol === "file:") return decodeURIComponent(u.pathname.split("/").pop() || src);
    if (!/^https?:$/.test(u.protocol)) return src;
    const v = u.searchParams.get("v");
    return u.hostname.replace(/^www\./, "") + (u.pathname === "/" ? "" : u.pathname) + (v ? `?v=${v}` : "");
  } catch { return src; }
}
