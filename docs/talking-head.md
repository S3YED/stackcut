# Talking head with graphics (`layout: talk`)

A 9:16 short of one face take, no screen recording. You talk; animated graphics carry the story next to you; the captions carry every word. 30-45 seconds.

It builds on the split look: same hook sticker, same captions with the keyword pill, same progress bar, same gradient and accent colour. When a graphic needs the room, the face makes it.

## The frame

| | Where | What |
|---|---|---|
| Progress bar | top edge | the accent colour, fills over the video |
| Hook sticker | top, the whole video | one line, about 28 characters plus one emoji, white sticker |
| Face | fills the frame | tracked, steadied, graded; it slides aside or down when a graphic needs room |
| Graphic | next to the head, or on top | one per line, lands on a spoken word |
| Captions | below the chin (on the seam in the split look) | word by word, the keyword in the accent pill |

The face has three places, and moves between them in 0.42 s with an ease (a hard cut with `cut: true`):

- **full:** the face fills the frame. Lines without a graphic.
- **beside (`at: right` / `at: left`):** the face slides to the other third and the graphic stands next to the head at eye height. For short things: a callout, an icon, a number, a short list.
- **top:** the split look. The face slides down into the lower panel, a gradient fills the top and carries the graphic, the captions move to the seam. For big things: a chart, a chat, an image or B-roll card, a big number.

Graphics in the same place on neighbouring lines keep the face still, so it never bounces.

## The graphics

One per line, as `graphic:` in `edit.yaml`. Each lands on the word in `word:` (the first word that starts with it), or as the line starts, and leaves just before the line's cut. `span: 2` keeps it up over the next line too.

| Kind | Example | Default place | Motion |
|---|---|---|---|
| `text` | `{text: "Zero *apps*"}` | right | the card pops, the words land one by one; `*starred*` words in the accent colour |
| `icon` | `{icon: "🤖", label: Claude}` or `{icon: logos/github.png}` | right | a white tile pops with a small turn, the label chip follows, then it floats gently |
| `count` | `{count: 22, label: businesses, prefix: "", suffix: "+"}` | top | counts up from `from` (0) in 1.1 s; digits in fixed cells so it never wobbles; `decimals:` for 1.5 |
| `list` | `{list: [Plans, Books, Bills]}` | right | chips with a tick, each landing on its own spoken word (up to 5) |
| `bars` | `{bars: [10, 24, 66], labels: [Jun, Jul, Aug], title: ...}` | top | bars grow one after another, values appear on top (`values: ["30%", ...]` to word them yourself), the last bar in the accent colour |
| `image` | `{image: shot.png, caption: free}` or `{image: broll.mp4, start: 3}` | top | a rounded card slides in from the side; a still gets a slow push-in, a clip plays |
| `chat` | `{chat: [{in: "Can you call me?"}, {out: "Booked ✅", word: booked}], title: Clark}` | top | message bubbles land one by one (up to 4); `in` grey on the left, `out` in the accent colour on the right |

`bars` and `chat` only go on top; the others go anywhere.

Two more line options shape the face:
- **`face_zoom: 1.15`**: the face is that much closer for the line. Between two lines it's a zoom cut.
- **`style.punch: true`**: the face steps in 10% on key words, at most every 2.2 s, so jump cuts read as beats.

## Sound

Every graphic lands with a soft pop (each list item and chat bubble too), an image card or a move into the split look with a soft whoosh. They sit well under the voice. The rest is the same as the split layout: an impact on the first frame, pops on the hook and the end, the voice cleaned with DeepFilterNet, the music bed ducked with its mids scooped, -14 LUFS.

## What's automatic, what the editor picks

| Automatic | The editor (Claude or you) picks per line |
|---|---|
| the face found, steadied, framed, white balanced and graded | which take of each line (the one with the most conviction) |
| every pause cut, the captions and their keyword pill | the graphic: kind, words or number, place, the word it lands on |
| the face making room, and every move between places | `face_zoom` zoom cuts, and which lines stay bare |
| when list items and chat bubbles land (on their words, or spaced) | `span`, `beat` after the hook, `join`, `pad_in` |
| the sound cues, the music bed, loudness, lip sync | the hook sticker's text, caption fixes in `fix:` |

## Rules of thumb for the editor

1. **The hook line stays clean.** At most a short callout beside the head, landing on the key word. The face sells the hook.
2. **A graphic on most lines, never on all.** Leave one or two lines bare (a zoom cut instead) so the face carries the feeling.
3. **One idea per graphic.** A callout is 1-4 words: the word the line turns on, not the sentence. Numbers exactly as measured, with the source in a comment.
4. **Alternate.** Right, then top, then left. Not more than two top lines in a row: the face is small there.
5. **Land on the word.** `word:` the word the graphic is about, so it appears as it's said.
6. **The follow line stays bare,** with a small `face_zoom`.
7. **No client data on screen.** Chat cards are written fresh and generic; images and B-roll show only your own screens or test data.

## Recording for this format

- **Mic:** the DJI mic on the shirt. A camera with a wireless mic records the sound late (130-140 ms on the DJI pocket camera), so measure it on every take: `stackcut sync face.mp4`, or `av_offset: auto`.
- **Framing:** chest up, eyes on the lens, a little room on both sides of the head. A landscape camera leaves the most room for the face to slide.
- **A clap at the start of each take:** easy to check the sync by eye.
- **Two or three takes per line,** with a one-second pause between them. stackcut hears every take (`stackcut lines face.mp4 --energy`), and the editor keeps the one said with the most conviction.
- **Same shirt and light for the whole session,** so the takes cut together.
- **Nobody right behind you.** When the face slides aside, more of the room shows: people at the next table end up on screen.
