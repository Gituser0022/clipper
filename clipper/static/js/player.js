// The video preview used in "Make a clip": a YouTube embed (IFrame API) or a plain
// <video> for other sites, behind one small interface.
import { $, api, setHidden, setText } from "./core.js";

let ytReady = null, loadedFor = "", loadSeq = 0, rangeTimer = 0;
const listeners = [];
export const player = { ready: false, duration: 0, time: () => 0, seek() {}, play() {}, pause() {} };
export const onPlayerChange = (fn) => listeners.push(fn);
const changed = () => { for (const fn of listeners) fn(player); };

function loadYouTubeApi() {
  if (window.YT?.Player) return Promise.resolve();
  if (!ytReady) ytReady = new Promise((ok, fail) => {
    window.onYouTubeIframeAPIReady = ok;
    const s = document.createElement("script");
    s.src = "https://www.youtube.com/iframe_api"; s.async = true;
    s.onerror = () => { ytReady = null; s.remove(); fail(new Error("couldn't load the YouTube player")); };
    document.head.appendChild(s);
  });
  return ytReady;
}

export function resetPlayer() {
  loadedFor = ""; loadSeq++; clearInterval(rangeTimer);
  Object.assign(player, { ready: false, duration: 0, time: () => 0, seek() {}, play() {}, pause() {} });
  $("playerMount").replaceChildren();
  setHidden($("playerMsg"), false);
  setText($("playerMsg"), "Loading the video…");
  changed();
}

export async function loadPlayer(url) {
  if (!url || url === loadedFor) return;
  resetPlayer();
  loadedFor = url;
  const seq = loadSeq;  // a newer load or reset makes this one stale
  try {
    const p = await api(`/api/preview?url=${encodeURIComponent(url)}`);
    if (p.kind === "youtube") await loadYouTubeApi();
    if (seq !== loadSeq) return;
    if (p.kind === "youtube") {
      const div = document.createElement("div");
      $("playerMount").appendChild(div);
      const yt = new YT.Player(div, {
        videoId: p.id, playerVars: { playsinline: 1, rel: 0 },
        events: { onReady: () => { if (seq === loadSeq) { player.duration = yt.getDuration?.() || 0; changed(); } } },
      });
      Object.assign(player, {
        ready: true, time: () => yt.getCurrentTime?.() ?? 0, seek: (t) => yt.seekTo?.(t, true),
        play: () => yt.playVideo?.(), pause: () => yt.pauseVideo?.(),
      });
    } else {
      const v = document.createElement("video");
      v.src = p.url; v.controls = true; v.playsInline = true; v.preload = "metadata";
      v.addEventListener("loadedmetadata", () => { player.duration = Number.isFinite(v.duration) ? v.duration : 0; changed(); });
      $("playerMount").appendChild(v);
      Object.assign(player, {
        ready: true, time: () => v.currentTime, seek: (t) => { v.currentTime = t; },
        play: () => v.play().catch(() => {}), pause: () => v.pause(),
      });
    }
    setHidden($("playerMsg"), true);
    changed();
  } catch (e) {
    if (seq === loadSeq) setText($("playerMsg"), `No preview for this link (${e.message}). Everything else still works.`);
  }
}

// Play start..end, then pause.
export function playRange(start, end) {
  if (!player.ready) return false;
  clearInterval(rangeTimer);
  player.seek(start); player.play();
  rangeTimer = setInterval(() => { if (!player.ready || player.time() >= end) { player.pause(); clearInterval(rangeTimer); } }, 200);
  return true;
}
