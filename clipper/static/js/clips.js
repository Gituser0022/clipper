// "My clips": a gallery with server-made thumbnails, and one lightbox player.
import { $, api, post, el, setText, setHidden, setClass, setStyle, setAttr, dur, clock, plural, toast, busy, reconcile, kick, onState,
         prettySource, store } from "./core.js";
import { loginHint } from "./download.js";
import { storage, tilde, renderStatus } from "./storage.js";
import { device, saveMessage } from "./remote.js";
// On a phone or iPad, "save" sends the file to the device and (by default) moves it off the Mac.
const onPhone = () => store.client?.local === false;
function setSaveLink(a, file) {
  const f = encodeURIComponent(file);
  if (onPhone()) {
    a.href = `/clips/${f}?save=1`;
    a.textContent = device() === "this device" ? "↓ Save to this device" : `↓ Save to ${device()}`;
    a.dataset.save = "1";
  } else {
    a.href = `/clips/${f}`; a.textContent = "↓ Download"; delete a.dataset.save;
  }
  a.title = file; a.setAttribute("aria-label", `${a.textContent.slice(2)}: ${file}`);
}

const FOLDER = { clips: "Clips", highlights: "Highlights", downloads: "Downloads" };
const deleted = new Set();  // ids removed here, so a poll already on its way can't bring them back

const KIND = { vod: ["Video", ""], live: ["● Live", "live"], auto: ["Auto", "auto"], highlight: ["★ Highlight", "hl"],
               download: ["↓ Download", "dlt"], edit: null };
const nodes = new Map();
const AUDIO = /\.(mp3|m4a|aac|opus|ogg|oga|wav|flac)$/i;
const isAudio = (j) => (j.file ? AUDIO.test(j.file) : j.kind === "download" && /· Audio only/i.test(j.label));
const isPortrait = (j) => (j.file ? j.file.endsWith("_9x16.mp4") : / · 9:16\b|^9:16 of /.test(j.label));
const WAVE = '<svg viewBox="0 0 64 32" aria-hidden="true"><path d="M2 16h4M10 10v12M16 4v24M22 12v8M28 7v18M34 2v28M40 9v14M46 13v6M52 6v20M58 11v10M62 16h0"/></svg>';

// Labels end with " · <source>" (a link or a video title). Downloads are
// "Download · <quality> · <title>": show the title, with the quality underneath.
function describe(j) {
  if (j.kind === "download") {
    const parts = j.label.split(" · ");
    return { title: parts.slice(2).join(" · ") || j.label, sub: parts[1] || "", src: "" };
  }
  const i = j.label.lastIndexOf(" · ");
  const src = i >= 0 ? j.label.slice(i + 3) : "";
  return { title: (i >= 0 ? j.label.slice(0, i) : j.label).replace(/ · 9:16$/, ""), sub: prettySource(src), src };
}

function make(j) {
  const root = $("clipTpl").content.firstElementChild.cloneNode(true), q = (s) => root.querySelector(s);
  root.dataset.id = j.id;
  const { title, sub, src } = describe(j);
  const tags = q(".tags"), k = j.kind in KIND ? KIND[j.kind] : [j.kind, ""];
  if (k) tags.appendChild(el("span", `tag ${k[1]}`, k[0]));
  if (isPortrait(j)) tags.appendChild(el("span", "tag vert", "9:16"));
  if (isAudio(j)) tags.appendChild(el("span", "tag", "Audio"));
  q(".clip-title").textContent = title;
  q(".clip-src").textContent = sub; q(".clip-src").title = src || sub; q(".clip-src").hidden = !sub;
  q(".clip-when").textContent = clock(j.created);
  q(".clip-folder").textContent = j.folder ? `${FOLDER[j.folder] || j.folder}/` : "";
  const n = { el: root, j, title, thumb: q(".thumb"), state: q(".state"), dur: q(".dur"), err: q(".clip-err"), acts: q(".clip-acts"),
              pill: q(".clip-pill"), dl: q(".dl"), vert: q('[data-act="vertical"]'), del: q('[data-act="delete"]'), folder: q(".clip-folder"), status: null, text: null, bar: null, barFill: null };
  setClass(n.thumb, "portrait", isPortrait(j));
  setClass(n.thumb, "audio", isAudio(j));
  return n;
}

function update(n, j) {
  n.j = j;
  // Files found on disk at startup are "done" before their length is known; it arrives a moment later.
  if (j.status === "done" && j.duration && n.dur.hidden && n.status === "done") { n.dur.textContent = dur(j.duration); n.dur.hidden = false; }
  if (j.status === "running") {
    if (n.status !== "running") {
      n.bar = el("span", "progress"); n.barFill = el("i"); n.bar.appendChild(n.barFill);
      n.bar.setAttribute("role", "progressbar"); n.bar.setAttribute("aria-valuemin", "0"); n.bar.setAttribute("aria-valuemax", "100");
      n.state.replaceChildren(el("span", "spinner"), el("span", "state-text"), n.bar);
      n.state.firstChild.setAttribute("aria-hidden", "true");
      n.text = n.state.querySelector(".state-text");
    }
    // Downloads report progress (0..1) and a stage; other jobs show elapsed time.
    const s = Math.max(0, Math.round(Date.now() / 1000 - j.created)), p = j.progress;
    const stage = j.stage || (j.kind === "download" ? "Downloading" : "Making your clip");
    setText(n.text, p != null ? `${stage}… ${Math.round(p * 100)}%` : s >= 3 ? `${stage}… ${dur(s)}` : `${stage}…`);
    setHidden(n.bar, p == null);
    if (p != null) { setStyle(n.barFill, "transform", `scaleX(${p.toFixed(3)})`); setAttr(n.bar, "aria-valuenow", Math.round(p * 100)); }
    setHidden(n.state.firstChild, p != null);
  }
  if (n.status === j.status) return;
  n.status = j.status;
  n.el.className = `clip ${j.status}`;
  const ready = j.stage === "Already downloaded" ? "Already downloaded" : "Ready";
  const [kind, word] = { running: ["warn", j.kind === "download" ? "Downloading" : "Making"], done: ["ok", ready],
                         error: ["err", "Failed"] }[j.status] || ["", j.status];
  n.pill.className = `pill clip-pill ${kind}`; n.pill.textContent = word;
  if (j.status === "done") {
    const f = encodeURIComponent(j.file), audio = isAudio(j);
    setClass(n.thumb, "portrait", isPortrait(j));
    setClass(n.thumb, "audio", audio);
    n.state.replaceChildren();
    if (audio) {
      // Sound only: there is no picture, so show a waveform tile.
      const tile = el("span", "audio-tile"); tile.innerHTML = WAVE;
      tile.appendChild(el("span", "audio-ext", j.file.split(".").pop().toUpperCase()));
      n.thumb.prepend(tile);
    } else {
      // One cached frame per clip, made by the server; no <video> until you press play.
      for (const cls of isPortrait(j) ? ["fg", "bg"] : ["fg"]) {  // prepended, so the blurred backdrop ends up first (underneath)
        const img = el("img", cls);
        img.alt = ""; img.loading = "lazy"; img.decoding = "async"; img.src = `/clips/${f}/thumb.jpg`;
        img.onerror = () => img.remove();
        n.thumb.prepend(img);
      }
    }
    n.thumb.disabled = false;
    n.thumb.setAttribute("aria-label", `${audio ? "Listen to" : "Play"} ${n.title}`);
    if (j.duration) { n.dur.textContent = dur(j.duration); n.dur.hidden = false; }
    setSaveLink(n.dl, j.file);
    n.vert.dataset.file = j.file; n.vert.hidden = audio || j.file.endsWith("_9x16.mp4");
    setText(n.del, canTrash() ? "Move to Bin" : "Delete");
    n.acts.hidden = false; n.err.hidden = true;
  } else if (j.status === "error") {
    n.state.replaceChildren(el("span", "state-icon", "!"),
                            el("span", null, j.kind === "download" ? "This download didn't work" : "This clip couldn't be made"));
    const hint = j.kind === "download" ? loginHint(j.error) : "";
    n.err.textContent = (j.error || "Something went wrong.") + (hint ? `\n${hint}` : "");
    n.err.hidden = false; n.acts.hidden = true;
  }
}

function render(s) {
  const jobs = s.jobs.filter((j) => !deleted.has(j.id));
  reconcile($("clips"), nodes, jobs, make, update);
  setHidden($("clipsEmpty"), jobs.length);
  const done = jobs.filter((j) => j.status === "done").length;
  setText($("clipCount"), jobs.length ? `${plural(done, "file")} ready${s.running ? ` · ${s.running} in progress` : ""}` : "");
}
onState(render);

// -- delete, with an in-page confirm ----------------------------------------------------
let toDelete = null;
// On a Mac the file goes to the Bin (and can be put back); elsewhere it is deleted for good.
const canTrash = () => !!storage()?.can_trash;
function confirmDelete(n) {
  const j = n.j, s = storage(), where = s?.folders?.[j.folder] ? tilde(s.folders[j.folder]) : `~/clipper/${FOLDER[j.folder] || "Clips"}`;
  const name = n.title.length > 60 ? `${n.title.slice(0, 57)}…` : n.title, bin = canTrash(), ok = $("delOk");
  setText($("delTitle"), bin ? `Move “${name}” to the Bin?` : `Delete “${name}”?`);
  setText($("delText"), bin ? "You can put it back from the Bin in Finder."
                            : `This removes the file from ${where} for good. It doesn't go to the Bin.`);
  setText(ok, bin ? "Move to Bin" : "Delete");
  ok.className = `btn ${bin ? "primary" : "danger-solid"}`;
  toDelete = n;
  $("deleteDlg").returnValue = "";
  $("deleteDlg").showModal();
}
$("deleteDlg").addEventListener("close", async () => {
  const n = toDelete; toDelete = null;
  if (!n || $("deleteDlg").returnValue !== "delete") return;
  try {
    const s = await api(`/api/clips/${encodeURIComponent(n.j.file)}`, { method: "DELETE" });
    // Remove just this card; every other card (and its thumbnail) stays as it is.
    deleted.add(n.j.id); nodes.delete(n.j.id); n.el.remove();
    store.jobs = store.jobs.filter((x) => x.id !== n.j.id);
    render(store);
    if (s && s.used) { store.storage = s; renderStatus(s); }
    toast(s?.trashed ? "Moved to the Bin" : `Deleted “${n.title}”.`, "ok");
    kick();
  } catch (e) { toast(e.message, "error"); }
});

async function makeVertical(file, btn) {
  try {
    await busy(btn, "Starting…", () => post(`/api/clips/${encodeURIComponent(file)}/vertical`, { focus: 0.5 }));
    toast("Making a 9:16 copy. It appears at the top of My clips.", "cut");
    kick();
  } catch (err) { toast(err.message, "error"); }
}
// Saving to a phone: say what happens to the Mac's copy right away (the download itself is the browser's).
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[data-save]");
  if (a) toast(saveMessage(), "ok", 7000);
});
$("clips").addEventListener("click", (e) => {
  const v = e.target.closest('button[data-act="vertical"]');
  if (v) return makeVertical(v.dataset.file, v);
  const d = e.target.closest('button[data-act="delete"]');
  if (d) return confirmDelete(nodes.get(d.closest(".clip").dataset.id));
  const t = e.target.closest("button.thumb");
  if (t && !t.disabled) open(nodes.get(t.closest(".clip").dataset.id));
});

// Lightbox: a single <video> (or <audio> for sound files), loaded only when opened.
function open(n) {
  const j = n.j, f = encodeURIComponent(j.file), dlg = $("viewer"), audio = isAudio(j);
  const media = audio ? $("viewerAudio") : $("viewerVideo");
  setHidden($("viewerVideo"), audio); setHidden($("viewerAudioBox"), !audio); setHidden($("viewerNote"), true);
  setClass(dlg, "portrait", isPortrait(j)); setClass(dlg, "audio", audio);
  setText($("viewerTitle"), n.title); $("viewerTitle").title = j.file;
  setSaveLink($("viewerDl"), j.file);
  $("viewerVert").hidden = audio || j.file.endsWith("_9x16.mp4"); $("viewerVert").dataset.file = j.file;
  if (!audio) media.poster = `/clips/${f}/thumb.jpg`;
  media.src = `/clips/${f}`;
  dlg.showModal();
  media.play().catch(() => {});
}
// Some downloads (WebM, MKV) don't play in every browser: say so instead of a blank player.
for (const id of ["viewerVideo", "viewerAudio"]) {
  $(id).addEventListener("error", () => {
    if (!$(id).getAttribute("src")) return;
    setHidden($("viewerNote"), false);
  });
}
$("viewerClose").onclick = () => $("viewer").close();
$("viewer").addEventListener("click", (e) => { if (e.target === $("viewer")) $("viewer").close(); });  // backdrop
$("viewer").addEventListener("close", () => {
  for (const m of [$("viewerVideo"), $("viewerAudio")]) { m.pause(); m.removeAttribute("src"); m.load(); }
});
$("viewerVert").onclick = (e) => makeVertical(e.currentTarget.dataset.file, e.currentTarget);
