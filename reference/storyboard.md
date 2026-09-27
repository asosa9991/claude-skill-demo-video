# Storyboard reference

JSON, or YAML when PyYAML is installed. Relative paths resolve against the
storyboard file's directory.

## Top level

| Field | Default | Notes |
|---|---|---|
| `output` | `demo.mp4` | Final mp4. `--output` overrides. |
| `title` | — | Used as the title-card text if that omits its own. |
| `voice` | `Samantha` | Any installed `say` voice. |
| `rate` | `175` | Words per minute. 180 is a good demo pace. |
| `resolution` | `1920x1080` | All scenes are scaled and letterboxed to this. |
| `fps` | `30` | |
| `captions` | `none` | `none` \| `soft` \| `burn` |
| `title_card` | — | `{text, subtitle, duration}` |
| `scenes` | required | Array, in order. |

## Scene fields

Every scene takes `narration` (optional), plus exactly one source:

| Source | Shape | Notes |
|---|---|---|
| `clip` | `"path.mov"` | Pre-recorded. The main path. |
| `screen` | `{duration, display, region, countdown}` | Records during the build, with a countdown. |
| `terminal` | `{tape: "x.tape"}` | Rendered by VHS. |
| `image` | `"path.png"` + optional `duration` | Still. |
| `pause` | `2.0` | Black for N seconds. |

Per-scene overrides: `voice`, `rate`.

## Duration rules

`scene length = max(clip length, narration length, 1.5s)`

- Narration longer → last video frame freezes to fill.
- Clip longer → audio is padded with silence.

## Example

```json
{
  "output": "demo/walkthrough.mp4",
  "voice": "Samantha", "rate": 180,
  "resolution": "1920x1080",
  "captions": "soft",
  "title_card": { "text": "Positions on Android", "subtitle": "A2UI v0.9.1", "duration": 3.0 },
  "scenes": [
    { "narration": "The app renders the payload natively.", "clip": "app.mov" },
    { "narration": "Five dependencies make it work.", "terminal": { "tape": "deps.tape" } },
    { "pause": 1.0 }
  ]
}
```
