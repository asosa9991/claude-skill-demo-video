---
name: demo-video
description: Record narrated demo videos on macOS — screen capture plus synthesized voiceover, assembled into an mp4. Use when asked to create a demo video, record a walkthrough, screen-record a feature with narration, or produce a video showing how something works. macOS only.
---

# Narrated demo videos on macOS

Write a markdown script, run one command, get an mp4. Timing is handled for you.

```
SCRIPT=~/.claude/skills/demo-video/scripts/demo_video.py
python3 $SCRIPT check          # run this first, once
python3 $SCRIPT build demo.md
```

macOS only: it uses `screencapture` and `say`.

## 1. Check

`check` verifies ffmpeg, `say`, a text renderer, and **Screen Recording
permission**. If permission is missing the user must grant it in System Settings
> Privacy & Security > Screen & System Audio Recording and restart the terminal.
You cannot grant it for them. `brew install ffmpeg`; `brew install vhs` only if
you want terminal scenes.

## 2. Capture, if the demo shows an app

**Ask before recording the desktop** — whatever is on screen ends up in the file.

```bash
python3 $SCRIPT record start --out app.mov --window "Android Emulator"
# ...drive the app...
python3 $SCRIPT record stop
```

`--window` matches a window title or app name and works out the region. An empty
or malformed `--region` is rejected rather than silently capturing everything.
For an Android emulator prefer `adb shell screenrecord` — it captures the device
directly at full resolution and does not need macOS permission.

## 3. Write the script

Markdown. Front matter sets defaults, `## ` starts a scene, one `@` directive
says what is on screen, and the prose under it is the narration.

```markdown
---
output: demo.mp4
voice: Zoe (Premium)
resolution: 1920x1080
captions: soft
---

# My Feature
> What changed and why

## What this covers
@card 1 | Setup | Two commands and one trap
Narration goes here as ordinary prose. Write as much as the
scene needs; the picture is sized to fit it automatically.

## The dependencies
@run sed -n '/^dependencies/,/^}/p' app/build.gradle.kts
Five artifacts, and the third one is the one people miss.

## It running
@clip app.mov 4-22
Here it is on a device.
```

| Directive | Renders |
| --- | --- |
| `@card N \| Title \| Subtitle` | A numbered section card |
| `@run <command>` | A terminal scene showing that command and its output |
| `@clip file [from-to]` | Video, optionally trimmed to those seconds |
| `@image file [seconds]` | A still |
| `@pause 2` | A beat of black |

**You do not set any durations.** Terminal scenes are generated *after* the
narration is measured, so they always fit. A clip shorter than its narration
holds its last frame; longer, and the audio is padded.

## 4. Build

```bash
python3 $SCRIPT build demo.md
```

Writes the mp4 plus `.srt`, `.vtt`, `.transcript.md` and `.chapters.json`.
Scenes are cached, so editing one line of narration rebuilds in seconds.

Optional: `plan demo.md` prints per-scene timing without building.
`page demo.mp4` builds a shareable HTML page with clickable chapters.

## Writing narration

- Budget **~3 words per second**. A 20-second scene is ~60 words.
- Say what is on screen, specifically. Viewers are looking at it.
- For identifiers, add a `pronounce.json` next to the script — it rewrites text
  **for the voice only**, so captions keep the real spelling:
  `{"androidx.a2ui": "androidx dot a 2 u i"}`
- One idea per scene.

## Voices

macOS ships robotic **compact** voices. Enhanced/Premium are a large upgrade:
System Settings > Accessibility > Spoken Content > System Voice > Manage Voices,
then `voice: Zoe (Premium)`. Siri voices cannot be used with `say -v`; set one as
the system voice and use `voice: system`. `python3 $SCRIPT voices` lists what is
installed.

## When it goes wrong

| Symptom | Cause |
| --- | --- |
| `screencapture exited immediately` | Screen Recording permission not granted |
| Capture is black, or hangs | Capture session wedged; restart the terminal |
| `--region was empty` | Guard against recording the whole desktop; use `--window` |
| A command mixes `'` and `"` | The tape runs it from a file; simplify the quoting |
| `No such filter: 'drawtext'` | Expected — text is rendered via headless Chrome |

More detail: `reference/script-format.md` for every field and the JSON form,
`reference/recipes.md` for worked examples.
