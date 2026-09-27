# demo-video

A [Claude Code](https://claude.com/claude-code) skill that records narrated demo
videos on macOS: screen capture plus a synthesized voiceover, assembled into an mp4.

Ask your agent for a demo video and it drives the whole pipeline — record the
screen, write the narration, build the file.

## What it does

- **Records the desktop**, or a single window, while the agent drives the app
- **Synthesizes narration** with the built-in `say` command
- **Fits picture to voice** automatically — freezes the last frame, or pads with silence
- **Assembles** scenes into one mp4, with an optional title card and captions
- Optional **terminal scenes** via [VHS](https://github.com/charmbracelet/vhs), plus still-image and pause scenes

macOS only, by construction: `screencapture` and `say` have no portable equivalents.

## Install

```bash
git clone https://github.com/asosa9991/claude-skill-demo-video.git ~/.claude/skills/demo-video
brew install ffmpeg          # required
brew install vhs             # optional, for terminal scenes

python3 ~/.claude/skills/demo-video/scripts/demo_video.py check
```

`check` verifies dependencies and, importantly, **Screen Recording permission**.
Grant it to your terminal under System Settings → Privacy & Security → Screen &
System Audio Recording, then restart the terminal.

## Quick start

Record while you drive the app:

```bash
SCRIPT=~/.claude/skills/demo-video/scripts/demo_video.py

python3 $SCRIPT record start --out app.mov --region 100,100,353,813
# ...click, type, scroll — whatever the demo shows...
python3 $SCRIPT record stop
```

Describe it:

```json
{
  "output": "demo.mp4",
  "voice": "Zoe (Premium)",
  "resolution": "1920x1080",
  "title_card": { "text": "My Feature", "duration": 3.0 },
  "scenes": [
    { "narration": "Here is the new screen.", "clip": "app.mov" },
    { "narration": "And this is how it is wired up.", "terminal": { "tape": "code.tape" } }
  ]
}
```

Build it:

```bash
python3 $SCRIPT build storyboard.json
```

Or one-shot, no storyboard:

```bash
python3 $SCRIPT quick --narration "Here is the dashboard." --duration 15 --out demo.mp4
```

## Scene types

| Source | Use for |
| --- | --- |
| `clip` | A screen recording you already captured |
| `screen` | Records during the build, with a countdown |
| `terminal` | A VHS tape — sharper than screen-recording a terminal |
| `image` | A still, e.g. an architecture diagram |
| `pause` | A beat of black |

Scene length is `max(clip, narration, 1.5s)`. Narration longer than the clip
freezes the last frame; a clip longer than the narration gets padded with silence.

## Voices

macOS ships **compact** voices, which sound robotic. Enhanced and Premium voices
are a large upgrade — install them under System Settings → Accessibility → Spoken
Content → System Voice → Manage Voices, then use e.g. `"voice": "Zoe (Premium)"`.

Siri voices sound best but **cannot** be passed to `say -v`. Set one as your
Spoken Content system voice and use `"voice": "system"` instead.

```bash
python3 $SCRIPT voices    # reports which tiers you have installed
```

## Writing narration that lands

- Budget **~3 words per second** at rate 180. A 24-second clip wants ~70 words.
- Say what is actually on screen. Viewers are looking at it.
- Spell identifiers phonetically: `androidx.a2ui` → `"androidx dot a 2 u i"`.
- One idea per scene — re-recording 20 seconds is cheap, two minutes is not.

## Documentation

- [SKILL.md](SKILL.md) — full instructions, including the agent workflow
- [reference/storyboard.md](reference/storyboard.md) — every storyboard field
- [reference/example-android-demo.md](reference/example-android-demo.md) — a worked example

## Notes

Homebrew's ffmpeg ships without `drawtext` and `libass`, so title cards and
burned-in captions are rendered as PNGs via headless Chrome instead. The skill
detects this and falls back cleanly.

## License

MIT
