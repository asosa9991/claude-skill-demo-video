# Script format

Markdown is the normal form. JSON and YAML still work and accept the same
fields — use them only when generating a storyboard programmatically.

## Front matter

| Key | Default | Notes |
| --- | --- | --- |
| `output` | `demo.mp4` | |
| `voice` | `Samantha` | any installed `say` voice, or `system` |
| `rate` | `175` | words per minute; 178 is a good pace |
| `resolution` | `1920x1080` | every scene is scaled and letterboxed to this |
| `fps` | `30` | |
| `captions` | `none` | `none` \| `soft` \| `burn` |
| `loudness` | `-16` | LUFS target; `none` disables normalisation |
| `title_seconds` | `4` | length of the title card |

`# Heading` becomes the title card, `> line` under it the subtitle.

## Scene directives

One per scene, directly under the `## ` heading.

```
@card 1 | The libraries | What is published        numbered section card
@run  cat notes.txt                                terminal scene
@clip app.mov                                      whole clip
@clip app.mov 4-22                                 trimmed to those seconds
@image diagram.png                                 still, sized to narration
@image diagram.png 6                               still, at least 6s
@pause 2                                           black
```

Everything else under the heading is narration.

## Timing

`scene length = max(source, narration, 1.5s)`.

- `@run` scenes are generated after the narration is measured, so they fit.
- A clip shorter than its narration holds its last frame.
- A clip longer than its narration gets silence padded onto the audio.
- `@card`, `@image` and `@pause` size themselves to the narration.

Run `plan script.md` to see this per scene without building.

## Pronunciation

`pronounce.json` beside the script rewrites text **for the voice only** —
captions and the transcript keep the real spelling. Longest key wins.

```json
{ "androidx.a2ui": "androidx dot a 2 u i",
  "1.0.0-alpha01": "one point zero point zero, alpha one" }
```

## JSON form

Same fields; scenes take `card`, `terminal.run`, `terminal.tape`, `clip`
(+`from`/`to`), `image` (+`duration`), or `pause`, plus `narration` and an
optional `chapter`. A hand-written `terminal.tape` bypasses auto-sizing, so you
own the `Sleep` value.

## Build flags

| Flag | Effect |
| --- | --- |
| `--output PATH` | override the output |
| `--no-cache` | re-render every scene |
| `--clean` | delete intermediates and the cache afterwards |
