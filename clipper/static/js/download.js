// Download a whole video (or just its sound) from any site yt-dlp supports.
// `downloader(root)` builds one panel: look the link up, pick videos and a quality,
// then start the downloads. The Download page and "Make a clip" each use one.
import { $, post, el, setText, setHidden, setDisabled, setClass, setStyle, dur, plural, toast, busy, trackJobs, untrack, kick,
         rememberTitle } from "./core.js";

const BROWSER_KEY = "clipper.loginBrowser";
const BROWSERS = ["chrome", "safari", "firefox", "edge", "brave"];
let loginBrowser = (() => { try { const b = localStorage.getItem(BROWSER_KEY); return BROWSERS.includes(b) ? b : ""; } catch { return ""; } })();
const panels = [];

// yt-dlp's messages about logins, cookies, private posts or rate limits get a plain hint.
const NEEDS_LOGIN = /log ?in|sign ?in|cookie|private|rate.?limit|too many requests|\b429\b|authenticat|age.?restrict|confirm your age/i;
export const loginHint = (msg) => (NEEDS_LOGIN.test(msg || "")
  ? "This site may need you to be logged in. Try “Use my login from” under More options on the Download page." : "");

let count = 0;
export function downloader(root, { preview = true } = {}) {
  const node = $("dlTpl").content.firstElementChild.cloneNode(true), q = (s) => node.querySelector(s);
  root.replaceChildren(node);
  const group = `dlq${++count}`;
  const E = {
    loading: q(".dl-loading"), error: q(".dl-error"), errorText: q(".dl-error-text"), hint: q(".dl-hint"), retry: q(".dl-retry"),
    body: q(".dl-body"), preview: q(".dl-preview"), thumb: q(".dl-thumb"), title: q(".dl-title"), meta: q(".dl-meta"), live: q(".dl-live"),
    entries: q(".dl-entries"), count: q(".dl-count"), list: q(".dl-list"), all: q(".dl-all"), none: q(".dl-none"),
    choices: q(".dl-choices"), browser: q(".dl-browser"), actions: q(".dl-actions"), go: q(".dl-go"), summary: q(".dl-summary"),
    result: q(".dl-result"),
  };
  E.browser.value = loginBrowser;
  let url = "", data = null, seq = 0;

  function show(state) {  // "idle" | "loading" | "error" | "ready"
    setHidden(E.loading, state !== "loading");
    setHidden(E.error, state !== "error");
    setHidden(E.body, state !== "ready");
    setHidden(E.actions, state !== "ready");
  }
  async function load(u, force = false) {
    if (!u) return reset();
    if (u === url && !force) return;
    url = u; data = null;
    const my = ++seq;
    untrack(E.result);
    show("loading");
    try {
      const r = await post("/api/download/inspect", { url, browser: loginBrowser || null });
      if (my !== seq) return;
      data = r;
      rememberTitle(url, r.title);
      render(); show("ready");
    } catch (e) {
      if (my !== seq) return;
      setText(E.errorText, e.message);
      const hint = loginHint(e.message);
      setText(E.hint, hint); setHidden(E.hint, !hint);
      show("error");
    }
  }
  function reset() { url = ""; data = null; seq++; untrack(E.result); show("idle"); }

  function render() {
    const d = data;
    // Preview card (the "Make a clip" panel already shows one in step 2).
    setHidden(E.preview, !preview);
    setStyle(E.thumb, "backgroundImage", d.thumbnail ? `url("${String(d.thumbnail).replace(/["\\]/g, "")}")` : "");
    E.thumb.dataset.icon = d.thumbnail ? "" : d.choices.every((c) => c.kind === "audio") ? "♪" : "▶";
    setText(E.title, d.title || url);
    // yt-dlp says "Generic" for plain file links; the host name says more.
    const host = (() => { try { return new URL(url).hostname.replace(/^www\./, ""); } catch { return ""; } })();
    const site = d.site && !/^generic$/i.test(d.site) ? d.site : host;
    E.meta.replaceChildren(...[site && el("span", "tag", site), d.duration && el("span", "mono", dur(d.duration)),
                              d.uploader && el("span", null, d.uploader)].filter(Boolean));
    setHidden(E.live, !d.is_live);
    // Several videos behind one link: tick the ones to save.
    const entries = d.entries || [];
    setHidden(E.entries, !entries.length);
    setText(E.count, entries.length);
    E.list.replaceChildren(...entries.map((x) => {
      const row = el("label", "dl-item"), cb = el("input");
      cb.type = "checkbox"; cb.checked = true; cb.value = x.index;
      const pic = el("span", "dl-item-thumb");
      if (x.thumbnail) pic.style.backgroundImage = `url("${String(x.thumbnail).replace(/["\\]/g, "")}")`;
      else pic.textContent = String(x.index);
      const txt = el("span", "dl-item-text");
      txt.append(el("span", "dl-item-title", x.title || `Video ${x.index}`), el("span", "dl-item-meta mono", x.duration ? dur(x.duration) : `#${x.index}`));
      row.append(cb, pic, txt);
      return row;
    }));
    // Quality choices, as the server offers them.
    E.choices.replaceChildren(...d.choices.map((c) => {
      const row = el("label", "dl-choice"), r = el("input");
      r.type = "radio"; r.name = group; r.value = c.id; r.checked = !!c.recommended;
      const txt = el("span", "dl-choice-text"), top = el("span", "dl-choice-top");
      top.append(el("b", null, c.label));
      if (c.recommended) top.append(el("span", "pill ok plain", "Recommended"));
      txt.append(top, el("span", "dl-choice-note", c.note || ""));
      row.append(r, el("span", "dl-kind", c.kind === "audio" ? "♪" : "▶"), txt);
      return row;
    }));
    if (!E.choices.querySelector("input:checked")) { const first = E.choices.querySelector("input"); if (first) first.checked = true; }
    summarize();
  }
  const picked = () => [...E.list.querySelectorAll("input:checked")].map((x) => Number(x.value));
  const choice = () => data?.choices.find((c) => c.id === E.choices.querySelector("input:checked")?.value);
  function summarize() {
    if (!data) return;
    const c = choice(), multi = !!(data.entries && data.entries.length), n = multi ? picked().length : 1;
    const what = c?.kind === "audio" ? (n === 1 ? "the audio" : `audio from ${n} videos`) : n === 1 ? "the video" : `${n} videos`;
    setText(E.go, n ? `↓ Download ${what}` : "Pick at least one video");
    setDisabled(E.go, !n || !c);
    setText(E.summary, c ? `${c.label}${multi ? ` · ${n} of ${data.entries.length} selected` : ""}` : "");
  }
  E.list.addEventListener("change", summarize);
  E.choices.addEventListener("change", summarize);
  E.all.onclick = () => { for (const x of E.list.querySelectorAll("input")) x.checked = true; summarize(); };
  E.none.onclick = () => { for (const x of E.list.querySelectorAll("input")) x.checked = false; summarize(); };
  E.retry.onclick = () => load(url, true);
  E.browser.addEventListener("change", () => {
    setLoginBrowser(E.browser.value);
    if (url && !data) load(url, true);  // it failed before: try again with the login
  });
  E.go.onclick = () => {
    const c = choice(); if (!c || !data) return;
    const items = data.entries && data.entries.length ? picked() : null;
    busy(E.go, "Starting…", async () => {
      try {
        const r = await post("/api/download", { url, choice: c.id, items, browser: loginBrowser || null, title: (data.title || "").slice(0, 300) || null });
        trackJobs(E.result, r.jobs);
        toast(`Downloading ${plural(r.jobs.length, "file")}. They appear in My clips.`, "ok");
        kick();
      } catch (e) {
        const hint = loginHint(e.message);
        toast(e.message + (hint ? `\n${hint}` : ""), "error");
      }
    }).then(summarize);
  };
  show("idle");
  const panel = { load, reset, get url() { return url; }, browser: E.browser };
  panels.push(panel);
  return panel;
}

function setLoginBrowser(b) {
  loginBrowser = BROWSERS.includes(b) ? b : "";
  try { if (loginBrowser) localStorage.setItem(BROWSER_KEY, loginBrowser); else localStorage.removeItem(BROWSER_KEY); } catch {}
  for (const p of panels) p.browser.value = loginBrowser;
}

// -- the Download page ------------------------------------------------------------------
const page = downloader($("dlPanel"));
let pageUrl = "";
function inspectPage(force = false) {
  const u = $("dlLink").value.trim();
  if (!u) { pageUrl = ""; page.reset(); setHidden($("dlStep2"), true); setClass($("dlStep1"), "done", false); return; }
  if (!/^\w[\w+.-]*:\/\/\S+$/.test(u)) { setText($("dlLinkMsg"), "That doesn't look like a link. Paste one starting with https://"); return; }
  setText($("dlLinkMsg"), "");
  if (u === pageUrl && !force) return;
  pageUrl = u;
  setHidden($("dlStep2"), false); setClass($("dlStep1"), "done", true);
  page.load(u, force);
}
$("dlForm").onsubmit = (e) => { e.preventDefault(); inspectPage(); };
$("dlLink").addEventListener("change", () => inspectPage());
$("dlLink").addEventListener("paste", () => setTimeout(() => inspectPage(), 0));
$("dlClear").onclick = () => { $("dlLink").value = ""; inspectPage(); $("dlLink").focus(); };
