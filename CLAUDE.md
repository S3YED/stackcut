# CLAUDE.md: how to edit a video with stackcut

You are the editor. The human recorded their screen and, at the same time, their face on a phone. Your job is a 35-50 second vertical short that a stranger watches to the end. stackcut does the rendering. You make the edit decisions in `edit.yaml`.

## 0. Check the inputs
- Two files: a screen recording and a face take. Ask if it's unclear which is which.
- Run `stackcut doctor` once.
- Both recordings need audio for automatic sync. If the screen recording is silent, ask roughly when the two started and set `offset:` by hand (face time = screen time + offset).

## 1. First draft
```bash
stackcut draft screen.mp4 face.mov -o edit.yaml
stackcut lines face.mov             # numbered sentences with face-take times
stackcut frames screen.mp4 --every 2
```
Read the sentences. `frames` writes `screen.frames.jpg` (one frame every 2 s, left to right). Open it so you know what the screen shows when.

## 2. Build the story (this is the edit)
Short-form demo structure that works:
1. **Hook, under 3.5 s.** The boldest line, in its shortest clean take. People often say it 2-4 times; pick the cleanest, not the first. It may come from another part of the take, even one recorded before the screen recorder started. Then give it a `screen_at` cutaway to the most striking screen moment.
2. **Name it.** "It's called X." Show the whole app: `zoom: full`.
3. **Three to five concrete things it does.** One line each, each shown on screen with a zoom on exactly that thing.
4. **The twist.** "And here's the most important thing..." followed by the reveal.
5. **Payoff.** Restate the name or the result. A zoom back out to `full` reads as an ending.
6. **Follow line,** if they said one ("follow me for more..."). Default: keep it.

Cut:
- **Retakes:** keep the one with the most conviction, not just the last. Run `stackcut lines face.mov --energy`: louder, more pitch movement, a steady pace and no long pause win. Then check the face at those moments (`stackcut frames face.mov`): eyes on the lens, awake. Drop yawns, slow blinks, sighs and flat takes.
- **Stumbles:** "I I am not using" becomes a line that starts at the clean "I am", so set `from` there.
- **Warm-up lines:** "have a look", "but I want to share this with you".
- **Repeats of a point already made.**
- **Anything the human can't back up.** If a claim (a price, "free", a number) isn't something they clearly stand behind, leave it out and tell them.

Target 35-50 s. If the draft runs long, drop the weakest feature line first.

For a tired or slow take: `style: {tightness: tight, speed: 1.07, punch: true, face_grade: {brightness: 0.05, warmth: 0.03, eyes: 0.08}}`, and `beat: 0.3` on the hook line. Keep `voice: denoise` (the default): it takes the room out and leaves the voice alone. Don't use `isolate` for a good mic (DJI, lavalier): it reshapes the voice. A music bed: instrumental, no drums or vocals, `music_lufs` about -32, `music_mid_cut_db: 6` so it never clashes with the voice. Never ship a generated track in the repo.

## 3. Pick what the screen shows
For every line, set `zoom`:
- **`full`** for establishing shots, the name reveal and the ending.
- **`[cx, cy, z]`** for the thing being talked about. `cx`, `cy` are the centre as fractions of the screen (0-1) and `z` is the zoom: 1.3-1.6 for a region, 2.0-2.5 for a single panel or number. Read the positions off the frames sheet.
- **`auto`** when you can't tell: it frames whatever moved on screen during that line.
- **`screen_at: <seconds>`** when the synced moment doesn't show what's being said (they talk about a feature before or after clicking to it). The screen then rolls on from there.
- **Browser in the recording?** Set `style.top_crop` (about 0.1) so tabs and the address bar never show in full view, and keep `cy` low enough that zoomed views stay below them.

## 4. Privacy (do not skip)
Find every client name, phone number, email, message text, avatar and invoice on screen.
1. Find the layout changes: pages switching, a panel refreshing, a box opening. Look at the frames sheet, then check single frames around the changes:
   ```bash
   ffmpeg -ss 41 -i screen.mp4 -frames:v 1 f41.png
   ```
2. Add `privacy:` boxes `[x, y, w, h]` in screen pixels. Give each a `from`/`to` window (screen seconds) per layout, and overlap the windows by about 0.15 s at every change. A box that ends one frame early leaks.
3. After rendering, check full-resolution frames of every `full` line and every zoom over a private area. Thumbnails hide leaks.

## 5. The hook sticker
`hook:` is one line, about 28 characters, plus one emoji. Use one sentence: a full stop in the middle splits it into two lines. Make it blunt, a little provocative, and true to what is said. Examples:
- "AI rebuilt my site overnight 😳"
- "Meta might ban me for this 😬"
- "Meta bant me hiervoor 😬"

## 6. Render and review
```bash
stackcut render edit.yaml --preview   # fast, small file
stackcut render edit.yaml             # final
```
Then check:
- **`edit.sheet.jpg`** (a frame every 0.5 s): framing, zooms, nothing private, a strong first frame, a clean last frame.
- **`.edit.stackcut/words.json`** (the captions):
  - Is the first word of every line there?
  - Are there doubled words?
  - Did whisper mishear anything? Fix it in `fix:` (for example `{stream: screen}`).
- **Loudness:** the printed value should be about -14 LUFS.
- **A clipped first word in the audio** (a soft "I" or "If"): give that line `pad_in: 0.2`.
- **One sentence split over two lines:** give the second line `join: true`.

## 7. Hand it over
Show the human the preview and say what you cut and why. Never post or schedule anything without their explicit OK.

## A worked example
`examples/homepage.yaml` and `examples/whatsapp-nl.yaml` are the real edits behind the first two videos. [docs/how-it-was-made.md](docs/how-it-was-made.md) walks through every decision.
