"""The `montage view` page: one self-contained HTML document, no external requests.

Plan values reach the DOM through textContent only (an agent writes the plan; nothing in it is
markup). Polls /api/state; refetches /api/plan when the plan's sha256 or the preview changes.
"""

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>montage view</title>
<style>
:root {
  --bg: #f6f6f4; --panel: #ffffff; --ink: #1d1d1b; --muted: #6b6b66; --line: #dcdcd6;
  --video: #3d6fb6; --audio: #3f8f62; --caption: #a06a2c; --fade: rgba(0,0,0,.28);
  --ok: #2f7d4a; --warn: #a3561b; --bad: #b3261e; --sel: #f2b705;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #151514; --panel: #1f1f1d; --ink: #ecece8; --muted: #a2a29b; --line: #34342f;
          --video: #5b8fd9; --audio: #5fb383; --caption: #c98e4b; --fade: rgba(255,255,255,.28);
          --ok: #5fb383; --warn: #e0954f; --bad: #ef6b62; }
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 14px/1.45 system-ui, sans-serif; }
header { padding: 14px 16px 6px; display: flex; flex-wrap: wrap; gap: 4px 16px; align-items: baseline; }
h1 { font-size: 18px; margin: 0; }
.muted { color: var(--muted); }
main { padding: 0 16px 24px; display: grid; gap: 12px; }
section { background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 12px; min-width: 0; }
h2 { font-size: 13px; text-transform: uppercase; letter-spacing: .04em; color: var(--muted); margin: 0 0 8px; }
.badges { display: flex; flex-wrap: wrap; gap: 8px; }
.badge { border: 1px solid var(--line); border-radius: 999px; padding: 2px 10px; }
.ok { color: var(--ok); } .warn { color: var(--warn); } .bad { color: var(--bad); }
ul.reasons { margin: 6px 0 0; padding-left: 18px; }
.top { display: grid; gap: 12px; grid-template-columns: minmax(0, 1fr); }
@media (min-width: 900px) { .top { grid-template-columns: minmax(0, 3fr) minmax(0, 2fr); } }
video { width: 100%; max-height: 360px; background: #000; border-radius: 4px; }
button { font: inherit; padding: 4px 12px; border-radius: 6px; border: 1px solid var(--line);
         background: var(--panel); color: var(--ink); cursor: pointer; }
button:disabled { opacity: .5; cursor: default; }
.scroll { overflow-x: auto; }
.timeline { position: relative; min-width: 640px; }
.ruler, .lane { position: relative; height: 22px; margin-left: 96px; }
.ruler { height: 18px; border-bottom: 1px solid var(--line); }
.tick { position: absolute; top: 0; font-size: 11px; color: var(--muted); transform: translateX(-50%); }
.row { display: flex; align-items: center; margin: 6px 0; }
.row .label { width: 96px; flex: none; font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.row .lane { flex: 1; margin-left: 0; height: 26px; background: var(--bg); border-radius: 3px; cursor: crosshair; }
.clip { position: absolute; top: 2px; bottom: 2px; border-radius: 3px; border: 0; padding: 0 4px; color: #fff;
        font-size: 11px; overflow: hidden; white-space: nowrap; text-align: left; cursor: pointer; }
.clip.video { background: var(--video); } .clip.audio { background: var(--audio); } .clip.caption { background: var(--caption); }
.clip.selected { outline: 2px solid var(--sel); outline-offset: 1px; z-index: 2; }
.fade { position: absolute; top: 0; bottom: 0; pointer-events: none;
        background: repeating-linear-gradient(45deg, var(--fade) 0 3px, transparent 3px 6px); }
.playhead { position: absolute; top: 0; bottom: 0; width: 2px; background: var(--sel); pointer-events: none; }
.frames { display: grid; gap: 8px; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); }
figure { margin: 0; }
figure img { width: 100%; aspect-ratio: 16 / 9; object-fit: contain; background: #000; border-radius: 4px; display: block; }
figcaption { font-size: 12px; color: var(--muted); margin-top: 2px; }
table { border-collapse: collapse; font-size: 13px; }
td { padding: 2px 12px 2px 0; vertical-align: top; }
td:first-child { color: var(--muted); }
.words { display: flex; flex-wrap: wrap; gap: 4px; margin-top: 6px; }
.word { border: 1px solid var(--line); border-radius: 4px; padding: 0 4px; font-size: 12px; }
.errors { color: var(--bad); }
</style>
</head>
<body>
<header>
  <h1 id="title">montage view</h1>
  <span class="muted" id="sub"></span>
  <span class="muted" id="live" role="status"></span>
</header>
<main>
  <div id="errors" class="errors" hidden></div>
  <section>
    <h2>Gates</h2>
    <div class="badges" id="gates"></div>
    <ul class="reasons" id="reasons"></ul>
  </section>
  <div class="top">
    <section>
      <h2>Preview (480p, what montage preview renders)</h2>
      <video id="video" controls preload="metadata"></video>
      <p><button id="rerender">Re-render preview</button> <span id="pvstatus" class="muted" role="status"></span></p>
    </section>
    <section>
      <h2>Selected</h2>
      <div id="detail" class="muted">Click a clip, or a point on a lane to see that frame.</div>
    </section>
  </div>
  <section>
    <h2>Timeline <span class="muted" style="text-transform:none;letter-spacing:0">· block layout is approximate; frames and the preview are rendered by ffmpeg</span></h2>
    <div class="scroll"><div class="timeline" id="timeline"></div></div>
  </section>
  <section>
    <h2>Frames</h2>
    <div class="frames" id="frames"></div>
  </section>
</main>
<script>
"use strict";
let key = null, snap = null, selected = null, playhead = null, busy = false;
const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const secs = (f) => snap ? (f / snap.fps) : 0;
const stamp = (f) => { const s = secs(f); return `${Math.floor(s / 60)}:${(s % 60).toFixed(2).padStart(5, "0")}`; };

async function poll() {
  try {
    const s = await (await fetch("/api/state")).json();
    $("live").textContent = `following scene_plan.json · ${s.key.slice(0, 8)}`;
    if (s.key !== key) { key = s.key; snap = await (await fetch("/api/plan")).json(); render(); }
    busy = s.previewBusy;
    $("rerender").disabled = busy;
    $("pvstatus").textContent = busy ? "rendering…" : (s.previewError ? `failed: ${s.previewError}` : "");
    $("pvstatus").className = s.previewError ? "bad" : "muted";
  } catch (e) {
    $("live").textContent = "disconnected: is montage view still running?";
  }
  setTimeout(poll, 1500);
}

function render() {
  const errs = $("errors");
  errs.replaceChildren();
  errs.hidden = !snap.errors;
  if (snap.errors) {
    errs.append(el("strong", null, "scene_plan.json does not validate:"));
    const ul = el("ul");
    snap.errors.forEach((m) => ul.append(el("li", null, m)));
    errs.append(ul);
    return;
  }
  $("title").textContent = snap.title;
  $("sub").textContent = `${snap.workspace} · ${snap.width}x${snap.height} @ ${snap.fps} fps · ${secs(snap.durationFrames).toFixed(1)} s`;
  renderGates();
  $("video").src = snap.preview.exists ? `/preview.mp4?k=${encodeURIComponent(key)}` : "";
  renderTimeline();
  const still = selected && findClip(selected.id);
  if (still) select(still); else if (playhead != null && playhead < snap.durationFrames) showFrame(playhead); else showOverview();
}

function renderGates() {
  const g = $("gates"), r = $("reasons");
  g.replaceChildren(); r.replaceChildren();
  const sc = snap.script, sb = snap.storyboard, pv = snap.preview;
  const scriptText = { approved: ["ok", `script approved${sc.writer ? ` (${sc.writer})` : ""}`],
    draft: ["warn", `script: unapproved draft by ${sc.writer}`], changed: ["warn", "script changed since approval"],
    untracked: ["muted", "script: no gate record"], none: ["bad", "no script.json"], unknown: ["warn", "script: status unknown"] }[sc.state];
  g.append(el("span", `badge ${scriptText[0]}`, scriptText[1]));
  g.append(el("span", `badge ${sb.approved ? "ok" : "bad"}`, sb.approved ? `storyboard approved ${sb.approvedAt || ""}` : "storyboard not approved"));
  g.append(el("span", `badge ${!pv.exists ? "muted" : pv.stale.length ? "warn" : "ok"}`,
    !pv.exists ? "no preview yet" : pv.stale.length ? "preview is stale" : "preview matches the plan"));
  [...sb.problems, ...pv.stale, ...(sc.detail ? [sc.detail] : [])].forEach((m) => r.append(el("li", null, m)));
}

function pct(f) { return `${(100 * f / snap.durationFrames).toFixed(3)}%`; }

function renderTimeline() {
  const t = $("timeline");
  t.replaceChildren();
  const ruler = el("div", "ruler");
  const total = secs(snap.durationFrames), step = total > 120 ? 30 : total > 40 ? 10 : 5;
  for (let s = 0; s <= total; s += step) {
    const tick = el("span", "tick", `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);
    tick.style.left = pct(s * snap.fps); ruler.append(tick);
  }
  t.append(ruler);
  for (const track of snap.tracks) {
    const row = el("div", "row");
    row.append(el("span", "label", `${track.id} (${track.type})`));
    const lane = el("div", "lane");
    lane.setAttribute("aria-label", `${track.id} track`);
    lane.addEventListener("click", (ev) => {
      if (ev.target !== lane) return;
      const r = lane.getBoundingClientRect();
      showFrame(Math.min(snap.durationFrames - 1, Math.max(0, Math.round((ev.clientX - r.left) / r.width * snap.durationFrames))));
    });
    (track.clips || []).forEach((c) => {
      const b = el("button", `clip ${track.type}`, c.id);
      b.type = "button";
      b.style.left = pct(c.startFrame); b.style.width = pct(c.durationFrames);
      b.title = `${c.id}: frames ${c.startFrame}–${c.startFrame + c.durationFrames - 1}${c.src ? ` · ${c.src}` : ""}`;
      b.dataset.id = c.id;
      b.addEventListener("click", () => select({ ...c, track }));
      lane.append(b);
      const xf = c.transition && c.transition.durationFrames;
      if (xf) {
        const f = el("div", "fade");
        f.style.left = pct(c.startFrame + c.durationFrames - xf); f.style.width = pct(xf);
        f.title = `crossfade ${xf} frames`; lane.append(f);
      }
    });
    if (playhead != null) { const p = el("div", "playhead"); p.style.left = pct(playhead); lane.append(p); }
    row.append(lane); t.append(row);
  }
}

function findClip(id) {
  for (const track of snap.tracks) for (const c of track.clips || []) if (c.id === id) return { ...c, track };
  return null;
}

function select(c) {
  selected = c; playhead = null;
  renderTimeline();
  document.querySelectorAll(".clip").forEach((b) => b.classList.toggle("selected", b.dataset.id === c.id));
  const d = $("detail"); d.replaceChildren(); d.className = "";
  const tb = el("table");
  const row = (k, v) => { const tr = el("tr"); tr.append(el("td", null, k), el("td", null, v)); tb.append(tr); };
  const end = c.startFrame + c.durationFrames;
  row("clip", `${c.id} (${c.track.id}, ${c.track.type})`);
  if (c.src) row("source", c.src);
  row("frames", `${c.startFrame}–${end - 1} (${stamp(c.startFrame)} – ${stamp(end)}, ${c.durationFrames} frames)`);
  if (c.sourceInFrame) row("source in", `frame ${c.sourceInFrame}`);
  if (c.fit) row("fit", c.fit);
  if (c.motion) row("motion", `scale ${JSON.stringify(c.motion.scale)} ${c.motion.easing || ""}`);
  if (c.transition) row("into next", `${c.transition.type} ${c.transition.durationFrames} frames`);
  if (c.volume != null) row("volume", String(c.volume));
  d.append(tb);
  const words = captionWords(c.startFrame, end);
  if (words.length) {
    d.append(el("div", "muted", "caption words in this span:"));
    const w = el("div", "words");
    words.forEach((x) => { const s = el("span", "word", x.text); s.title = `frames ${x.startFrame}–${x.endFrame - 1}`; w.append(s); });
    d.append(w);
  }
  const fr = $("frames"); fr.replaceChildren();
  const last = Math.min(end, snap.durationFrames) - 1;
  [["first", c.startFrame], ["middle", Math.floor((c.startFrame + last) / 2)], ["last", last]]
    .forEach(([label, n]) => fr.append(frameFigure(n, `${label} · frame ${n} · ${stamp(n)}`)));
}

function captionWords(a, b) {
  const out = [];
  for (const t of snap.tracks) if (t.type === "caption")
    for (const c of t.clips || []) for (const w of c.words || []) if (w.endFrame > a && w.startFrame < b) out.push(w);
  return out;
}

function showFrame(n) {
  selected = null; playhead = n;
  renderTimeline();
  const d = $("detail"); d.replaceChildren(); d.className = "";
  d.append(el("div", null, `frame ${n} · ${stamp(n)}`));
  const words = captionWords(n, n + 1);
  if (words.length) d.append(el("div", "muted", `caption: ${words.map((w) => w.text).join(" ")}`));
  $("frames").replaceChildren(frameFigure(n, `frame ${n} · ${stamp(n)}`));
}

function showOverview() {
  const fr = $("frames"); fr.replaceChildren();
  const video = snap.tracks.find((t) => t.type === "video");
  (video ? video.clips : []).forEach((c) => {
    const mid = c.startFrame + Math.floor(c.durationFrames / 2);
    fr.append(frameFigure(mid, `${c.id} · middle · ${stamp(mid)}`));
  });
}

function frameFigure(n, caption) {
  const fig = el("figure"), img = el("img"), cap = el("figcaption", null, `${caption} · rendering…`);
  img.alt = caption;
  fig.append(img, cap);
  queueFrame(n, img, cap, caption);
  return fig;
}

// one request at a time: the server runs one ffmpeg anyway, and a burst would just queue there
const frameQueue = [];
let frameBusy = false;
// next tick: the caller appends the figure after this returns, and pump() skips detached images
function queueFrame(n, img, cap, caption) { frameQueue.push({ n, img, cap, caption, k: key }); setTimeout(pump, 0); }
async function pump() {
  if (frameBusy) return;
  const job = frameQueue.shift();
  if (!job) return;
  frameBusy = true;
  try {
    if (job.k === key && job.img.isConnected) {
      const res = await fetch(`/api/frame?n=${job.n}&k=${encodeURIComponent(snap.frameKey)}`);
      if (res.ok) { job.img.src = URL.createObjectURL(await res.blob()); job.cap.textContent = job.caption; }
      else { job.cap.textContent = `${job.caption} · not rendered: ${await res.text()}`; job.cap.className = "bad"; }
    }
  } catch (e) { job.cap.textContent = `${job.caption} · not rendered: ${e}`; }
  frameBusy = false;
  pump();
}

$("rerender").addEventListener("click", async () => {
  $("rerender").disabled = true;
  $("pvstatus").textContent = "rendering…";
  await fetch("/api/preview", { method: "POST" });
});
poll();
</script>
</body>
</html>
"""
