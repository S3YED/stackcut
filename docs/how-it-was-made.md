# How the first two videos were made

On 18 October 2026 Seyed recorded two demos at his desk. Each one was a screen recording made with Cap and an iPhone take of his face filmed at the same moment:
- An English video about his new homepage, which an AI rebuilt overnight.
- A Dutch video about WhatsApp Intelligence, his own WhatsApp tool.

He doesn't edit. Claude did, and these are the decisions in order. stackcut is the result, written down as a tool.

## 1. What came in
| | Screen | Face take | Notes |
|---|---|---|---|
| Homepage (EN) | 50 s, 1920x1246, the getclark.app homepage with an animated particle hero | 68 s iPhone HDR, landscape | Said once, with a stumble near the end |
| WhatsApp (NL) | 118 s, the WhatsApp Intelligence app with real client chats | 222 s | Run 1 (95 s) was recorded before he noticed the screen recorder wasn't running: "Holy shit. I didn't record the cap." Run 2 is the full demo. |

## 2. Sync
Both microphones heard the same words. Their loudness curves line up by cross-correlation:
- Homepage: face = screen + 6.76 s (confidence 0.75).
- Dutch: face = screen + 101.96 s (0.83). The 102 s is run 1 plus the restart.

## 3. The words
Whisper turned both takes into word-timed text. The English take came back in lower case without punctuation, so it was transcribed again with a punctuated English prompt. A prompt in the wrong language is a trap: an English prompt on the Dutch take made Whisper skip the first 30 seconds.

## 4. The story
**Homepage (40 s):**
- **Hook:** "Nine hours ago I closed my laptop, and this morning I wake up and this is what I see:"
- **Reveal:** "a completely redesigned, fully interactive particle effect home screen."
- **The point:** "AI can do all of this now. But can you do AI?"
- **The stakes:** "If everybody else ... by just sleeping, what are you doing?"
- **Who it's for:** "If you're a business owner, entrepreneur, researcher, student, even if you're a mom..."
- **Ending:** the follow line, taken from its second, clean attempt (the first ended in "interacting, um").
- **Sticker:** "AI rebuilt my site overnight 😳". The first try, "I slept. AI rebuilt my site", broke into two lines at the full stop.

**WhatsApp Intelligence NL (48 s):**
- **Hook:** from run 1, the 3.5 s take "Ik heb met AI iets gebouwd en ik ben bang dat Meta mij gaat bannen", because it is shorter than the 6 s run 2 version. Its screen is a cutaway to the lead score.
- **Cut for length:** run 2's warm-up "Kijk met me mee", a near-duplicate line, and the Meta-pricing paragraph (claims nobody had checked).
- **Left out:** "WhatsApp Intelligence is volledig gratis", because run 1 said "exclusief voor Clark". Contradictions go to the human, not into the video.
- **Ending:** "Daarom ben ik bang dat Meta mij gaat bannen", then run 1's follow line.

## 5. What the screen shows
One zoom target per line:
- `full` for "It's called WhatsApp Intelligence" and every ending.
- Close-ups of the lead score, the AI suggestion box, the Team page and the Autoresponder for the lines about them.

When he talked about a page before clicking to it, the line got a `screen_at` cutaway. The browser's tabs and address bar never show, because of `top_crop: 0.107`.

## 6. Privacy
The WhatsApp screen showed real clients, so a frame-difference scan found every layout change: 31.06, 41.07, 42.0, 47.02 and 53.38 s. Blur boxes cover:
- the chat list, chat header, message bubbles, AI suggestion text and composer;
- the lead panel's summary, quotes and contact block;
- the Team page email;
- the Autoresponder's phone number and prompt.

The first render leaked a phone number for 0.2 s, because one box's window ended at 68.0 s while the panel only refreshed at 68.54 s. Since then every window overlaps its layout change, and every change is checked at full resolution.

## 7. The face
iPhone HDR was tone-mapped in hardware. The face was tracked 10 times a second and smoothed: about 1.3 s for position, about 3 s for size. The face fills 36% of the panel.

His room has pink LED light:
- **Grey-world white balance** turned the curtains green.
- **The white shirt under his face as the reference, at half strength,** looked natural.

His head tilt was left alone. Correcting it tilted the whole room.

## 8. Captions and sound
- **Captions:** 3 words at a time, the keyword on a blue pill. Two small bugs were found on the way and are now handled in code:
  - a soft first "I" that Whisper timed too early disappeared from the captions;
  - a manual fix for it then produced "I I am".
- **Sound:** the voice was cleaned (ElevenLabs Voice Isolator) and laid over a quiet music bed ducked under it. A soft impact on frame one, a pop on the last. Mastered to -14 LUFS.

## 9. Sign-off
Seyed watched the previews, picked the versions with the follow line ("Use with Follow me") and said: "This is really, really good, exactly what I wanted." Both went on Instagram that evening. The homepage video followed on TikTok, YouTube, X, Facebook and LinkedIn.
