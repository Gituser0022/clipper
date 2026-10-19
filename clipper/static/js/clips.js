// "My clips": a gallery with server-made thumbnails, and one lightbox player.
import { $, post, el, setText, setHidden, setClass, dur, clock, plural, toast, busy, reconcile, kick, onState, prettySource } from "./core.js";

const KIND = { vod: ["Video", ""], live: ["● Live", "live"], auto: ["Auto", "auto"], highlight: ["★ Highlight", "hl"], edit: null };
const nodes = new Map();
const isPortrait = (j) => (j.file ? j.file.endsWith("_9x16.mp4") : / · 9:16\b|^9:16 of /.test(j.label));

function make(j) {
  const root = $("clipTpl").content.firstElementChild.cloneNode(true), q = (s) => root.querySelector(s);
  root.dataset.id = j.id;
  // Labels end with " · <source>" (a link or a video title); show that on its own line.
  const i = j.label.lastIndexOf(" · ");
  const src = i >= 0 ? j.label.slice(i + 3) : "", title = i >= 0 ? j.label.slice(0, i) : j.label;
  const tags = q(".tags"), k = j.kind in KIND ? KIND[j.kind] : [j.kind, ""];
  if (k) tags.appendChild(el("span", `tag ${k[1]}`, k[0]));
  if (isPortrait(j)) tags.appendChild(el("span", "tag vert", "9:16"));
  q(".clip-title").textContent = title.replace(/ · 9:16$/, "");
  q(".clip-src").textContent = prettySource(src); q(".clip-src").title = src; q(".clip-src").hidden = !src;
  q(".clip-when").textContent = clock(j.created);
  const n = { el: root, j, title: q(".clip-title").textContent, thumb: q(".thumb"), state: q(".state"), dur: q(".dur"),
              err: q(".clip-err"), acts: q(".clip-acts"), pill: q(".clip-pill"), dl: q(".dl"), vert: q('[data-act="vertical"]'), status: null, timer: null };
  setClass(n.thumb, "portrait", isPortrait(j));
  return n;
}

function update(n, j) {
  n.j = j;
  if (j.status === "running") {
    if (n.status !== "running") {
      n.state.replaceChildren(el("span", "spinner"), el("span", null, "Making your clip…"));
      n.state.firstChild.setAttribute("aria-hidden", "true");
      n.timer = n.state.lastChild;
    }
    const s = Math.max(0, Math.round(Date.now() / 1000 - j.created));
    setText(n.timer, s >= 3 ? `Making your clip… ${dur(s)}` : "Making your clip…");
  }
  if (n.status === j.status) return;
  n.status = j.status;
  n.el.className = `clip ${j.status}`;
  const [kind, word] = { running: ["warn", "Making"], done: ["ok", "Ready"], error: ["err", "Failed"] }[j.status] || ["", j.status];
  n.pill.className = `pill clip-pill ${kind}`; n.pill.textContent = word;
  if (j.status === "done") {
    const f = encodeURIComponent(j.file);
    setClass(n.thumb, "portrait", isPortrait(j));
    n.state.replaceChildren();
    // One cached frame per clip, made by the server; no <video> until you press play.
    for (const cls of isPortrait(j) ? ["fg", "bg"] : ["fg"]) {  // prepended, so the blurred backdrop ends up first (underneath)
      const img = el("img", cls);
      img.alt = ""; img.loading = "lazy"; img.decoding = "async"; img.src = `/clips/${f}/thumb.jpg`;
      img.onerror = () => img.remove();
      n.thumb.prepend(img);
    }
    n.thumb.disabled = false;
    n.thumb.setAttribute("aria-label", `Play ${n.title}`);
    if (j.duration) { n.dur.textContent = dur(j.duration); n.dur.hidden = false; }
    n.dl.href = `/clips/${f}`; n.dl.title = j.file; n.dl.setAttribute("aria-label", `Download ${j.file}`);
    n.vert.dataset.file = j.file; n.vert.hidden = j.file.endsWith("_9x16.mp4");
    n.acts.hidden = false; n.err.hidden = true;
  } else if (j.status === "error") {
    n.state.replaceChildren(el("span", "state-icon", "!"), el("span", null, "This clip couldn't be made"));
    n.err.textContent = j.error || "Something went wrong."; n.err.hidden = false; n.acts.hidden = true;
  }
}

onState((s) => {
  reconcile($("clips"), nodes, s.jobs, make, update);
  setHidden($("clipsEmpty"), s.jobs.length);
  const done = s.jobs.filter((j) => j.status === "done").length;
  setText($("clipCount"), s.jobs.length ? `${plural(done, "clip")} ready${s.running ? ` · ${s.running} being made` : ""}` : "");
});

async function makeVertical(file, btn) {
  try {
    await busy(btn, "Starting…", () => post(`/api/clips/${encodeURIComponent(file)}/vertical`, { focus: 0.5 }));
    toast("Making a 9:16 copy. It appears at the top of My clips.", "cut");
    kick();
  } catch (err) { toast(err.message, "error"); }
}
$("clips").addEventListener("click", (e) => {
  const v = e.target.closest('button[data-act="vertical"]');
  if (v) return makeVertical(v.dataset.file, v);
  const t = e.target.closest("button.thumb");
  if (t && !t.disabled) open(nodes.get(t.closest(".clip").dataset.id));
});

// Lightbox: a single <video>, loaded only when a clip is opened.
function open(n) {
  const j = n.j, f = encodeURIComponent(j.file), dlg = $("viewer"), v = $("viewerVideo");
  setClass(dlg, "portrait", isPortrait(j));
  setText($("viewerTitle"), n.title); $("viewerTitle").title = j.file;
  $("viewerDl").href = `/clips/${f}`;
  $("viewerVert").hidden = j.file.endsWith("_9x16.mp4"); $("viewerVert").dataset.file = j.file;
  v.poster = `/clips/${f}/thumb.jpg`; v.src = `/clips/${f}`;
  dlg.showModal();
  v.play().catch(() => {});
}
$("viewerClose").onclick = () => $("viewer").close();
$("viewer").addEventListener("click", (e) => { if (e.target === $("viewer")) $("viewer").close(); });  // backdrop
$("viewer").addEventListener("close", () => { const v = $("viewerVideo"); v.pause(); v.removeAttribute("src"); v.load(); });
$("viewerVert").onclick = (e) => makeVertical(e.currentTarget.dataset.file, e.currentTarget);
