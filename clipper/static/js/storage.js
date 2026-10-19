// Storage: where Clipper saves things (~/clipper/…), how much it may use, and what
// to do when it's full. A header pill and banners follow /api/state's `storage`.
import { $, api, el, setText, setHidden, setAttr, setStyle, toast, busy, onState, store } from "./core.js";

const GB = 1024 ** 3;  // same unit as the server's messages
export function bytes(n) {
  if (n == null) return "–";
  if (n >= GB) return `${(n / GB).toFixed(n >= 100 * GB ? 0 : 1)} GB`;
  if (n >= 1024 ** 2) return `${Math.round(n / 1024 ** 2)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}
// "/Users/me/clipper/Clips" -> "~/clipper/Clips"
export const tilde = (p) => String(p || "").replace(/^\/(Users|home)\/[^/]+/, "~");
const level = (s) => (s.full ? "err" : s.percent != null && s.percent >= 0.8 ? "warn" : "ok");
const FOLDERS = [["clips", "Clips"], ["highlights", "Highlights"], ["downloads", "Downloads"]];
let latest = null;
export const storage = () => latest;

// -- header pill + "storage is full" banners ---------------------------------------------
export function renderStatus(s) {
  latest = s || null;
  const pills = document.querySelectorAll(".storage-pill");
  for (const b of document.querySelectorAll(".full-banner")) setHidden(b, !s?.full);
  for (const pill of pills) {
    setHidden(pill, !s);
    if (!s) continue;
    setText(pill, s.limit_bytes ? `${bytes(s.used.total)} / ${bytes(s.limit_bytes)}` : `${bytes(s.used.total)} used`);
    setAttr(pill, "class", `pill storage-pill ${s.limit_bytes ? level(s) : ""}`);
    setAttr(pill, "title", `Storage in ${tilde(s.folder)}${s.full ? " is full" : ""}. Click for storage settings.`);
  }
  if (!s) return;
  if ($("settingsDlg").open && !editing) renderPanel(s);
}
onState((st) => renderStatus(st.storage));
document.addEventListener("click", (e) => { if (e.target.closest("[data-open-settings]")) openSettings(); });

// -- the settings dialog -------------------------------------------------------------------
let editing = false;  // don't overwrite the form while someone is typing in it
function renderPanel(s) {
  setText($("stFolder"), tilde(s.folder)); $("stFolder").title = s.folder;
  const pct = s.percent;
  setText($("stUsed"), bytes(s.used.total));
  setText($("stOf"), s.limit_bytes ? `of ${bytes(s.limit_bytes)}` : "used · no limit");
  const meter = $("stMeter");
  setAttr(meter, "class", `meter ${s.limit_bytes ? level(s) : "none"}`);
  setStyle(meter.firstElementChild, "transform", `scaleX(${pct == null ? 0 : Math.min(1, pct).toFixed(3)})`);
  setAttr(meter, "aria-valuenow", pct == null ? null : Math.round(pct * 100));
  setAttr(meter, "aria-valuetext", s.limit_bytes ? `${bytes(s.used.total)} of ${bytes(s.limit_bytes)}` : `${bytes(s.used.total)} used, no limit`);
  const state = $("stState");
  setAttr(state, "class", `pill ${s.full ? "err" : s.limit_bytes ? level(s) : ""}`);
  setText(state, s.full ? "Full" : !s.limit_bytes ? "No limit" : pct >= 0.8 ? "Almost full" : "Plenty of room");
  setText($("stFree"), `${bytes(s.disk_free_bytes)} free on this disk`);
  // Breakdown, one row per folder.
  $("stRows").replaceChildren(...FOLDERS.map(([key, name]) => row(name, s.folders?.[key], s.used[key], key)),
                              row("Logs", s.folders?.logs, s.used.logs, "logs", true),
                              row("Live recording buffers", null, s.used.work, null, true));
  if (!editing) {
    const none = !s.limit_bytes;
    $("stNoLimit").checked = none;
    $("stLimit").disabled = none;
    $("stLimit").value = none ? "" : Math.round((s.limit_bytes / GB) * 10) / 10;
    for (const r of document.querySelectorAll('input[name="stWhen"]')) r.checked = r.value === (s.when_full || "stop");
    syncWarning();
  }
}
function row(name, path, size, key, minor = false) {
  const r = el("div", `st-row${minor ? " minor" : ""}`);
  const left = el("div", "st-row-text");
  left.append(el("span", "st-row-name", name), el("span", "st-row-path mono", path ? tilde(path) : "Temporary, cleaned up automatically"));
  if (path) left.lastChild.title = path;
  r.append(left, el("span", "st-row-size mono", bytes(size)));
  if (key) {
    const b = el("button", "btn sm mac-only", "Show in Finder"); b.type = "button"; b.dataset.folder = key;
    r.appendChild(b);
  }
  return r;
}
function syncWarning() {
  const del = document.querySelector('input[name="stWhen"]:checked')?.value === "delete_oldest";
  setHidden($("stWarn"), !del);
}
export async function openSettings() {
  editing = false;
  setText($("stMsg"), "");
  $("settingsDlg").showModal();
  document.dispatchEvent(new CustomEvent("clipper:settings-open"));
  try { renderPanel(await api("/api/storage")); }
  catch (e) { if (latest) renderPanel(latest); else setText($("stMsg"), e.message); }
}
$("settingsBtn").onclick = openSettings;
$("storagePill").onclick = openSettings;
$("stClose").onclick = () => $("settingsDlg").close();
$("settingsDlg").addEventListener("close", () => { editing = false; });
$("stNoLimit").addEventListener("change", () => {
  editing = true;
  $("stLimit").disabled = $("stNoLimit").checked;
  if (!$("stNoLimit").checked && !$("stLimit").value) $("stLimit").value = 20;
  if (!$("stNoLimit").checked) $("stLimit").focus();
});
$("stLimit").addEventListener("input", () => { editing = true; });
for (const r of document.querySelectorAll('input[name="stWhen"]')) r.addEventListener("change", () => { editing = true; syncWarning(); });
$("stRows").addEventListener("click", (e) => { const b = e.target.closest("button[data-folder]"); if (b) reveal(b.dataset.folder, b); });
$("stOpenMain").onclick = (e) => reveal(null, e.currentTarget);
async function reveal(folder, btn) {
  try { await busy(btn, null, () => api("/api/storage/open", { method: "POST", body: JSON.stringify({ folder }) })); }
  catch (e) { toast(e.message, "error"); }
}
$("stSave").onclick = async () => {
  const none = $("stNoLimit").checked, gb = Number($("stLimit").value);
  if (!none && !(gb >= 1 && gb <= 5000)) { setText($("stMsg"), "Pick a limit between 1 and 5000 GB, or turn on “No limit”."); return; }
  setText($("stMsg"), "");
  const when_full = document.querySelector('input[name="stWhen"]:checked')?.value || "stop";
  await busy($("stSave"), "Saving…", async () => {
    try {
      const s = await api("/api/settings", { method: "PUT", body: JSON.stringify({ storage_limit_gb: none ? null : gb, when_full }) });
      editing = false;
      store.storage = s; renderStatus(s); renderPanel(s);
      toast(none ? "Saved. No storage limit." : `Saved. Clipper will use up to ${gb} GB.`, "ok");
    } catch (e) { setText($("stMsg"), e.message); }
  });
};
