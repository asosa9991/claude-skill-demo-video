---
name: demo-video
description: Record narrated demo videos on macOS — screen capture plus synthesized voiceover, assembled into an mp4. Use when asked to create a demo video, record a walkthrough, screen-record a feature with narration, or produce a video showing how something works. macOS only.
---

# Narrated demo videos on macOS

Records the desktop (or one window), synthesizes a voiceover with `say`, and
assembles them into an mp4 with ffmpeg. **Desktop recording is the spine**;
terminal, image and pause scenes exist for the parts of a demo a screen
recording handles badly (dense code, architecture stills).

macOS only by construction: `screencapture` and `say` have no portable
equivalents.

```
SCRIPT=~/.claude/skills/demo-video/scripts/demo_video.py
python3 $SCRIPT check          # always run this first
```

## Before anything else

1. `python3 $SCRIPT check` — verifies ffmpeg, `say`, a text renderer, and
   **Screen Recording permission**. If permission is missing the user must grant
   it themselves in System Settings > Privacy & Security > Screen & System Audio
   Recording, then restart the terminal. You cannot grant it for them.
2. `brew install ffmpeg` if missing. `brew install vhs` only for terminal scenes.
3. **Ask before recording the desktop.** Whatever is on screen ends up in the
   file — windows, tabs, notifications. Prefer `--region` to capture one window.

## The workflow

### 1. Learn what you are demoing, before writing a word of narration

Read the code, run the thing, screenshot it. Narration that describes a UI you
have not actually looked at will be wrong in a way viewers notice immediately.
For a running app, capture a still first (`adb exec-out screencap -p > shot.png`
for Android, or a screenshot tool) and look at it.

### 2. Capture the screen

The important pattern — you drive the app while it records:

```bash
python3 $SCRIPT record start --out app.mov --region 100,100,353,813
# ...drive the UI: adb input swipe, clicks, typing, whatever the demo shows...
python3 $SCRIPT record stop
```

Fixed-length, unattended:

```bash
python3 $SCRIPT record screen --out app.mov --duration 12 --region x,y,w,h
```

Capture one window by name — no AppleScript needed:

```bash
python3 $SCRIPT record start --out app.mov --window "Android Emulator"
```

`--window` matches window titles and app names as substrings, and prints the
region it resolved. An empty or malformed `--region` is rejected rather than
silently falling back to full-screen capture. Omit both only when you really do
want the whole desktop.

Bring the target window to the front first, or you will record whatever covers it.

### 3. Write a storyboard

JSON (or YAML if PyYAML is installed). One scene per idea:

```json
{
  "title": "Feature Walkthrough",
  "output": "demo/walkthrough.mp4",
  "voice": "Samantha", "rate": 180,
  "resolution": "1920x1080", "fps": 30,
  "captions": "soft",
  "title_card": { "text": "Feature Walkthrough", "subtitle": "What changed", "duration": 3.0 },
  "scenes": [
    { "narration": "...", "clip": "app.mov" },
    { "narration": "...", "terminal": { "tape": "deps.tape" } },
    { "narration": "...", "image": "diagram.png", "duration": 6 },
    { "pause": 1.0 }
  ]
}
```

Scene sources: `clip` (pre-recorded), `screen` (records during build), `terminal`
(a VHS tape), `image`, `pause`. See `reference/storyboard.md` for every field.

### 4. Check the timing before you record

```bash
python3 $SCRIPT plan storyboard.json
```

Renders only the voiceovers and reports, per scene, how long the narration runs
against the source you have. It flags any scene that will hold a frozen frame
for 3s or more — so you size the capture correctly the first time instead of
discovering the mismatch afterwards.

```
scene     words    voice    source    scene  note
scene_00     74    26.7s     23.6s    26.7s  FREEZE 3.1s
scene_01     84    26.5s     20.3s    26.5s  ~est FREEZE 6.1s
```

Terminal scenes are estimated from the tape's `Sleep`/`Type` directives (`~est`).

### 5. Build

```bash
python3 $SCRIPT build storyboard.json
```

Per scene it renders the narration, fits the picture to it, and normalises
everything to one codec/resolution/fps before concatenating.

**Scenes are cached.** A scene is re-rendered only when its narration, voice,
rate, source file or resolution changes, so editing one line of narration
rebuilds in seconds instead of re-rendering every VHS tape in real time. Use
`--no-cache` to force a full rebuild, `--clean` to discard intermediates.

## Writing narration that lands

- **Budget words by time: ~3 words per second** at rate 180. A 24-second clip
  wants ~70 words. Check your clip's length (`ffprobe`) before writing.
- **Say what is on screen, specifically.** "Equities, with Apple and Microsoft"
  beats "some data". Viewers are looking at it; vague narration reads as padding.
- **Spell out identifiers phonetically.** `androidx.a2ui` → "androidx dot a 2 u i".
  `material3-a2ui` → "material 3 dash a 2 u i". Version `1.0.0-alpha01` →
  "one point zero point zero, alpha one". `say` mangles the raw strings.
- One idea per scene. Re-recording one 20-second scene is cheap; re-recording a
  two-minute take is not.

## How picture and voice get synced

The voiceover is synthesized *after* capture, so lengths never match exactly:

- Narration longer than the clip → the **last frame freezes** for the remainder.
- Clip longer than narration → **silence pads** the audio.
- Scene length is `max(clip, narration, 1.5s)`.

So record a little longer than you think, and prefer several short scenes over
one long one. If a scene freezes for many seconds, extend the capture (or the
`Sleep` in a VHS tape) rather than cutting the narration.

## Voices

Default `Samantha`. `python3 $SCRIPT voices` lists the good installed ones.
Quality jumps a lot with an Enhanced/Premium voice: System Settings >
Accessibility > Spoken Content > System Voice > Manage Voices. Set per storyboard
(`"voice"`) or per scene.

## Captions

- `"soft"` (default choice) — an `.srt` is written next to the mp4 and muxed as a
  `mov_text` track.
- `"burn"` — permanently drawn on. Rendered as PNG overlays via headless Chrome,
  because Homebrew's ffmpeg ships **without** `drawtext` and `libass`.
Narration is split into caption-sized chunks on sentence boundaries, timed
proportionally, so a long scene produces several readable captions rather than
one block.

## Terminal scenes (VHS)

Better than screen-recording a terminal: deterministic, no font/window jitter.
Use absolute paths inside tapes — the tape runs from wherever the build runs.

```
Set FontSize 20
Set Width 1500
Set Height 840
Set Theme "Dracula"
Type "sed -n '/^dependencies/,/^}/p' app/build.gradle.kts"
Enter
Sleep 16s
```

The final `Sleep` sets how long the output stays up — match it to the narration.

## Quick one-off

```bash
python3 $SCRIPT quick --narration "Here is the new dashboard." --duration 15 --out demo.mp4
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `screencapture exited immediately` | Screen Recording permission not granted to the terminal |
| Recording is black or shows the wrong app | Target window was not frontmost, or wrong `--region` |
| `No such filter: 'drawtext'` | Expected — the skill renders text via headless Chrome instead |
| Long frozen frames | Narration outran the capture; run `plan` first, then lengthen the clip or VHS `Sleep` |
| `--region was empty` | Guard against silently recording the whole desktop; use `--window NAME` |
| Robotic name pronunciation | Spell identifiers phonetically in the narration |

A worked example lives in `reference/example-android-demo.md`.
