// Wires the three pages together: navigation, header badges, keyboard shortcuts, Quit.
import { $, setText, setHidden, setAttr, setClass, refresh, stopPolling, onState, store, post, go } from "./core.js";
import { mark } from "./make.js";
import { resetPlayer } from "./player.js";
import { clipNewest } from "./recording.js";
import "./highlights.js";
import "./clips.js";
import "./download.js";
import "./storage.js";
import "./remote.js";

// -- pages (#/make, #/recording, #/clips) ------------------------------------------------
const VIEWS = ["make", "download", "recording", "clips"];
function route() {
  const v = VIEWS.find((x) => location.hash === `#/${x}`) || "make";
  for (const x of VIEWS) {
    setHidden($(`view-${x}`), x !== v);
    setAttr(document.querySelector(`.nav a[data-view="${x}"]`), "aria-current", x === v ? "page" : null);
  }
  if (v === "clips") seenClips = store.jobs.length;
  badges();
  document.title = { make: "Clipper", download: "Download · Clipper", recording: "Recording · Clipper", clips: "My clips · Clipper" }[v];
}
addEventListener("hashchange", () => { route(); window.scrollTo(0, 0); });

let seenClips = 0;
function badges() {
  const rec = $("navRec"), clips = $("navClips");
  setText(rec, store.recording || ""); setHidden(rec, !store.recording);
  setClass($("navRecDot"), "on", store.recording);
  const onClips = location.hash === "#/clips";
  if (onClips) seenClips = store.jobs.length;
  const fresh = Math.max(0, store.jobs.length - seenClips);
  setText(clips, store.running ? `${store.running}…` : fresh ? `+${fresh}` : "");
  setHidden(clips, !store.running && !fresh);
  setHidden($("offline"), !store.offline);
}
let primed = false;  // clips that already exist when the page opens aren't "new"
onState((s) => { if (!primed) { primed = true; seenClips = s.jobs.length; } badges(); });

// -- keyboard -------------------------------------------------------------------------
const dialogOpen = () => ["quitDlg", "viewer", "settingsDlg", "deleteDlg", "confirmDlg"].some((id) => $(id).open);
const typing = (e) => {
  const t = e.target;
  return e.ctrlKey || e.metaKey || e.altKey || t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || dialogOpen();
};
document.addEventListener("keydown", (e) => {
  if (typing(e)) return;
  if (e.target.closest?.(".tl-handle") && e.key.startsWith("Arrow")) return;
  const k = e.key.toLowerCase();
  if (k === "/") { e.preventDefault(); go("make"); $("link").focus(); $("link").select(); }
  else if ((k === "i" || k === "o") && location.hash !== "#/recording" && location.hash !== "#/clips") mark(k === "i" ? "in" : "out");
  else if (k === "c" && !e.repeat) clipNewest();
});

// -- colour theme: automatic (follows the system), light or dark ------------------------
const ICON = (body) => `<svg viewBox="0 0 24 24" width="17" height="17" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true">${body}</svg>`;
const THEMES = {
  auto: [ICON('<circle cx="12" cy="12" r="8"/><path d="M12 4a8 8 0 0 1 0 16z" fill="currentColor"/>'), "automatic"],
  light: [ICON('<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>'), "light"],
  dark: [ICON('<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>'), "dark"],
};
function applyTheme(t) {
  if (t === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = t;
  const [icon, name] = THEMES[t], btn = $("themeBtn");
  btn.innerHTML = icon; btn.title = `Colour theme: ${name}`; btn.setAttribute("aria-label", `Colour theme: ${name}. Click to change.`);
  document.dispatchEvent(new CustomEvent("clipper:theme"));
}
let theme = document.documentElement.dataset.theme || "auto";
$("themeBtn").onclick = () => {
  theme = { auto: "light", light: "dark", dark: "auto" }[theme];
  try { if (theme === "auto") localStorage.removeItem("clipper.theme"); else localStorage.setItem("clipper.theme", theme); } catch {}
  applyTheme(theme);
};
applyTheme(theme);

// -- a phone or iPad without the private link --------------------------------------------
document.addEventListener("clipper:unauthorized", (e) => {
  for (const id of ["viewer", "settingsDlg", "deleteDlg", "confirmDlg", "quitDlg"]) if ($(id).open) $(id).close();
  resetPlayer();
  const page = $("notConnectedTpl").content.firstElementChild.cloneNode(true);
  page.querySelector(".nc-msg").textContent = e.detail;
  page.querySelector(".nc-retry").onclick = () => location.reload();
  $("app").hidden = true;
  document.body.prepend(page);
  document.title = "Clipper (not connected)";
});

// -- quit -----------------------------------------------------------------------------
$("quit").onclick = () => {
  const dlg = $("quitDlg");
  if (typeof dlg.showModal !== "function") {
    if (confirm("Quit Clipper? Any live recordings stop. Saved clips are kept.")) quit();
    return;
  }
  dlg.returnValue = ""; dlg.showModal();
};
$("quitDlg").addEventListener("close", () => { if ($("quitDlg").returnValue === "quit") quit(); });
async function quit() {
  stopPolling();
  try { await post("/api/quit"); } catch {}
  if ($("viewer").open) $("viewer").close();
  resetPlayer();
  for (const f of document.querySelectorAll("#app iframe")) f.remove();  // stop any live embeds
  $("app").hidden = true;  // hidden, not removed: nothing still running trips over missing nodes
  document.body.prepend($("stoppedTpl").content.firstElementChild.cloneNode(true));
  $("toasts").replaceChildren();
  document.title = "Clipper (stopped)";
}

route();
refresh();
