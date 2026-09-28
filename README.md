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

Write a markdown script:

```markdown
---
output: demo.mp4
voice: Zoe (Premium)
resolution: 1920x1080
---

# My Feature
> What changed and why

## What this covers
@card 1 | Setup | Two commands and one trap
Narration as ordinary prose. Write as much as the scene needs —
the picture is sized to fit it automatically.

## The dependencies
@run sed -n '/^dependencies/,/^}/p' app/build.gradle.kts
Five artifacts, and the third is the one people miss.

## It running
@clip app.mov 4-22
Here it is on a device.
```

Build it:

```bash
python3 ~/.claude/skills/demo-video/scripts/demo_video.py build demo.md
```

**You never set a duration.** Terminal scenes are generated *after* the
narration is measured, so they always fit. Clips hold their last frame if the
narration runs long, and get padded with silence if it runs short.

Capture an app first if you need one:

```bash
python3 $SCRIPT record start --out app.mov --window "My App"
# ...drive the app...
python3 $SCRIPT record stop
```

## Scene types

| Directive | Renders |
| --- | --- |
| `@card N \| Title \| Subtitle` | A numbered section card |
| `@run <command>` | A terminal scene, sized to the narration |
| `@clip file [from-to]` | Video, optionally trimmed to those seconds |
| `@image file [seconds]` | A still |
| `@pause 2` | A beat of black |

JSON and YAML storyboards still work and take the same fields.

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
- Don't hand-spell identifiers. A `pronounce.json` beside the storyboard rewrites
  terms **for the voice only**, so captions keep the real spelling:
  `{"androidx.a2ui": "androidx dot a 2 u i"}`.
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
