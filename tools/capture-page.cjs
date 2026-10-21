// Smooth b-roll of a web page's animation, for a clip angle (`clip: true`) in layout: multicam.
// A screen recorder drops frames while a page animates (Cap recorded 25 fps with gaps up to 290 ms
// during a particle hero); a capture of the page itself plays clean.
//
//   node tools/capture-page.cjs URL OUT.mp4 SECONDS [--mode clock|realtime] [--size 1920x1112]
//
// --mode clock (default): the page's clock (requestAnimationFrame, timers, Date, performance) is
//   stepped one frame at a time and a screenshot is taken after each step: frame-exact at 30 fps,
//   however slow the machine. Right for canvas and JavaScript animations.
// --mode realtime: every compositor frame with its timestamp (Chrome screencast), resampled to 30 fps.
//   For animations that run on wall time (CSS on the compositor, workers), which the clock can't step.
// Needs Node, Playwright (PW=/path/to/node_modules/playwright, or `npm i playwright`) and ffmpeg.
const { chromium } = require(process.env.PW || "playwright");
const { execFileSync } = require("child_process");
const fs = require("fs"), os = require("os"), path = require("path");

const args = process.argv.slice(2);
const opt = (name, dflt) => { const i = args.indexOf(name); return i >= 0 ? args.splice(i, 2)[1] : dflt; };
const mode = opt("--mode", "clock"), [w, h] = opt("--size", "1920x1112").split("x").map(Number);
const [url, out, secs] = args;
if (!url || !out || !secs) { console.error("usage: capture-page.cjs URL OUT.mp4 SECONDS [--mode clock|realtime] [--size WxH]"); process.exit(2); }
const FPS = 30, n = Math.round(+secs * FPS);
const dir = fs.mkdtempSync(path.join(os.tmpdir(), "capture-"));
const frame = (i) => path.join(dir, `f${String(i).padStart(5, "0")}.jpg`);

(async () => {
  const browser = await chromium.launch({ args: ["--hide-scrollbars"] });
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  const first = 0;
  if (mode === "clock") {
    await ctx.clock.install({ time: new Date() });
    const page = await ctx.newPage();
    await page.goto(url, { waitUntil: "load", timeout: 60000 });
    await page.evaluate(() => document.fonts && document.fonts.ready);
    for (let i = 0; i < n; i++) {
      await page.clock.runFor(1000 / FPS);
      await page.screenshot({ path: frame(i), type: "jpeg", quality: 92 });
    }
  } else {
    const page = await ctx.newPage();
    const cdp = await ctx.newCDPSession(page);
    const frames = [];
    cdp.on("Page.screencastFrame", (f) => {
      frames.push({ t: f.metadata.timestamp, data: f.data });
      cdp.send("Page.screencastFrameAck", { sessionId: f.sessionId }).catch(() => {});
    });
    await cdp.send("Page.startScreencast", { format: "jpeg", quality: 92, maxWidth: w, maxHeight: h, everyNthFrame: 1 });
    await page.goto(url, { waitUntil: "commit" });
    await new Promise((r) => setTimeout(r, +secs * 1000 + 4000));   // the clip starts at the first paint
    await cdp.send("Page.stopScreencast");
    const painted = frames.findIndex((f) => f.data.length !== frames[0].data.length);
    const t0 = frames[Math.max(0, painted)].t;
    let k = Math.max(0, painted);
    for (let i = 0; i < n; i++) {
      const t = t0 + i / FPS;
      while (k + 1 < frames.length && frames[k + 1].t <= t) k++;
      fs.writeFileSync(frame(i), Buffer.from(frames[k].data, "base64"));
    }
  }
  await browser.close();
  execFileSync("ffmpeg", ["-v", "error", "-y", "-framerate", String(FPS), "-start_number", String(first),
    "-i", path.join(dir, "f%05d.jpg"), "-c:v", "libx264", "-preset", "medium", "-crf", "16", "-pix_fmt", "yuv420p",
    "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-movflags", "+faststart", out]);
  fs.rmSync(dir, { recursive: true, force: true });
  console.log(`${out}: ${((n - first) / FPS).toFixed(1)} s at ${FPS} fps (${mode})`);
})().catch((e) => { console.error(e); process.exit(1); });
