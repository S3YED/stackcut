# Credits and third-party notices

## Authors
- **Seyed Hosseini** ([@seyed.jpg](https://www.instagram.com/seyed.jpg/)): the idea, the footage and the taste. He approved the look the code reproduces.
- **Claude** ([Anthropic](https://www.anthropic.com)), working in [Claude Code](https://claude.com/claude-code): edited the first videos and wrote the code and docs.

## Used at run time (not bundled)
| Project | Licence | Notes |
|---|---|---|
| [FFmpeg](https://ffmpeg.org) | LGPL 2.1+ / GPL 2+ | Called as an external program; install it yourself. |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | MIT | Transcription; Whisper models by OpenAI (MIT) download on first use. |
| [Silero VAD](https://github.com/snakers4/silero-vad) | MIT | Ships inside faster-whisper. |
| [OpenCV](https://github.com/opencv/opencv) (opencv-python-headless) | Apache 2.0 | |
| [YuNet face detector](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet) | MIT, Copyright (c) 2020 Shiqi Yu | Downloaded to `~/.cache/stackcut` on first run and checked by SHA-256. |
| [NumPy](https://numpy.org) | BSD 3-Clause | |
| [Pillow](https://python-pillow.org) | MIT-CMU (HPND) | |
| [PyYAML](https://pyyaml.org) | MIT | |
| [ElevenLabs Voice Isolator](https://elevenlabs.io) | commercial API | Optional, only when you set `ELEVENLABS_API_KEY`. |

## Bundled
| File | Licence |
|---|---|
| `stackcut/assets/fonts/Geist-Bold.ttf`, `Geist-ExtraBold.ttf` ([Geist](https://github.com/vercel/geist-font) by Vercel) | SIL Open Font License 1.1, see `stackcut/assets/fonts/OFL.txt` |

## Inspiration
- [OpenScreen](https://github.com/siddharthvaddem/openscreen) and Screen Studio: the rounded window, soft background and eased zoom.
- [Cap](https://cap.so): the open-source recorder the example videos were made with.
- The caption style (a few words at a time, one keyword highlighted) follows what performs on short-form today.

The example videos in `docs/img` show Seyed Hosseini and his product getclark.app, and are used with his permission. Client data in the WhatsApp example was blurred before editing.
