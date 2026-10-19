// Phone & iPad: the Mac does the work; other devices on the Wi-Fi use this same page
// through a private link. On the Mac, Settings shows the QR code; on a phone, the page
// hides Mac-only controls and says whether it can reach the Mac.
import { $, api, setText, setHidden, setAttr, toast, busy, confirmBox, onState, store } from "./core.js";

// What happens to the Mac's copy after a phone saves a clip: "bin" | "delete" | "keep".
let afterSave = "bin";
let info = null;  // the Mac's /api/remote answer
export const afterPhoneSave = () => afterSave;
export const device = () => {
  const ua = navigator.userAgent;
  if (/iPhone|iPod/.test(ua)) return "iPhone";
  if (/iPad/.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1)) return "iPad";
  return "this device";
};
// Said the moment a phone starts saving a clip (the server moves the Mac's copy once it's sent).
export function saveMessage() {
  const where = device() === "this device" ? "this device" : `this ${device()}`;
  const then = { bin: "When it's done, it moves off your Mac (to the Mac's Bin).",
                 delete: "When it's done, it's deleted from your Mac.",
                 keep: "It stays on your Mac too." }[afterSave] || "";
  return `Saving to ${where}. ${then}\nTo put it in Photos: Files → Share → Save Video.`;
}
function renderAfterSave() {
  for (const r of document.querySelectorAll('input[name="afterSave"]')) r.checked = r.value === afterSave;
}
async function loadAfterSave() {
  try { const r = await api("/api/remote"); if (r.after_phone_save) afterSave = r.after_phone_save; renderAfterSave(); } catch { /* keep the default */ }
}
for (const r of document.querySelectorAll('input[name="afterSave"]')) r.addEventListener("change", async (e) => {
  const want = e.target.value, before = afterSave;
  setText($("asMsg"), "");
  try {
    const res = await api("/api/remote/after-save", { method: "PUT", body: JSON.stringify({ after_phone_save: want }) });
    afterSave = res?.after_phone_save || want;
    toast({ bin: "Saved clips will move to the Mac's Bin.", delete: "Saved clips will be deleted from the Mac.", keep: "Saved clips will stay on the Mac too." }[afterSave], "ok");
  } catch (err) { afterSave = before; setText($("asMsg"), err.message); }
  renderAfterSave();
});

// -- on a phone or iPad -----------------------------------------------------------------
let knownRemote = false;
const narrow = matchMedia("(max-width: 520px)");
onState((s) => {
  const remote = s.client?.local === false;
  document.body.classList.toggle("remote", remote);
  setHidden($("afterSaveSection"), remote ? false : !info?.enabled);
  if (remote && !knownRemote) { knownRemote = true; loadAfterSave(); }
  const pill = $("remotePill");
  setHidden(pill, !remote);
  if (!remote) return;
  setAttr(pill, "class", `pill remote-pill ${s.offline ? "err" : "ok"}`);
  const full = s.offline ? "Can't reach your Mac" : "Connected to your Mac";
  setText(pill, narrow.matches ? (s.offline ? "Can't reach Mac" : "Connected") : full);  // phones have little header room
  setAttr(pill, "title", full); setAttr(pill, "aria-label", full);
});

// -- on the Mac: Settings → Phone & iPad --------------------------------------------------
function render(r) {
  info = r;
  $("rmOn").checked = !!r.enabled;
  setHidden($("rmBody"), !r.enabled);
  setHidden($("rmUnreachable"), !(r.enabled && r.reachable === false));
  if (r.after_phone_save) { afterSave = r.after_phone_save; renderAfterSave(); }
  setHidden($("afterSaveSection"), store.client?.local === false ? false : !r.enabled);
  if (!r.enabled) return;
  const urls = r.urls || [], main = urls[0] || "", alt = urls.find((u) => u !== main && /\.local[:/]/.test(u)) || "";
  setText($("rmUrl"), main);
  setText($("rmAlt"), alt); setHidden($("rmAltRow"), !alt);
  // The SVG comes from our own server; as an <img> it can't run anything, and the
  // white box behind it keeps it scannable in dark mode.
  if (r.qr_svg) $("rmQr").src = `data:image/svg+xml;charset=utf-8,${encodeURIComponent(r.qr_svg)}`;
  setHidden($("rmQr").parentElement, !r.qr_svg);
}
document.addEventListener("clipper:settings-open", async () => {
  setText($("rmMsg"), ""); setText($("asMsg"), "");
  if (store.client?.local === false) return loadAfterSave();  // a phone: only the after-save choice
  try { render(await api("/api/remote")); }
  catch (e) { setText($("rmMsg"), e.message); }
});
$("rmOn").addEventListener("change", async (e) => {
  const want = e.target.checked;
  setText($("rmMsg"), "");
  try { render(await api("/api/remote", { method: "PUT", body: JSON.stringify({ enabled: want }) })); }
  catch (err) { e.target.checked = !want; setText($("rmMsg"), err.message); }
});
$("rmNew").onclick = async () => {
  const sure = await confirmBox({ title: "Make a new link?", ok: "New link",
    text: "Phones and iPads using the old link will need to scan again." });
  if (!sure) return;
  await busy($("rmNew"), "Making…", async () => {
    try { render(await api("/api/remote/new-link", { method: "POST", body: "{}" })); toast("New link ready. Scan the new QR code on each device.", "ok"); }
    catch (e) { setText($("rmMsg"), e.message); }
  });
};
async function copy(text, btn) {
  try { await navigator.clipboard.writeText(text); }
  catch {  // older browsers / insecure origins
    const t = document.createElement("textarea"); t.value = text; document.body.appendChild(t); t.select();
    document.execCommand("copy"); t.remove();
  }
  const old = btn.textContent; btn.textContent = "Copied"; setTimeout(() => { btn.textContent = old; }, 1500);
}
$("rmCopy").onclick = (e) => copy($("rmUrl").textContent, e.currentTarget);
$("rmCopyAlt").onclick = (e) => copy($("rmAlt").textContent, e.currentTarget);
export const remoteInfo = () => info;
