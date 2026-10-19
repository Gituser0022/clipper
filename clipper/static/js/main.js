// Wires the three pages together: navigation, header badges, keyboard shortcuts, Quit.
import { $, setText, setHidden, setAttr, setClass, refresh, stopPolling, onState, store, post, go } from "./core.js";
import { mark } from "./make.js";
import { resetPlayer } from "./player.js";
import { clipNewest } from "./recording.js";
import "./highlights.js";
import "./clips.js";

// -- pages (#/make, #/recording, #/clips) ------------------------------------------------
const VIEWS = ["make", "recording", "clips"];
function route() {
  const v = VIEWS.find((x) => location.hash === `#/${x}`) || "make";
  for (const x of VIEWS) {
    setHidden($(`view-${x}`), x !== v);
    setAttr(document.querySelector(`.nav a[data-view="${x}"]`), "aria-current", x === v ? "page" : null);
  }
  if (v === "clips") seenClips = store.jobs.length;
  badges();
  document.title = { make: "Clipper", recording: "Recording · Clipper", clips: "My clips · Clipper" }[v];
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
const dialogOpen = () => $("quitDlg").open || $("viewer").open;
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
