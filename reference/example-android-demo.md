# Worked example: Android POC walkthrough

A real build: 98.6s, 1920x1080, 3.3 MB, five scenes. Recorded from an
Android emulator running a Jetpack Compose app.

## Shape

1. Title card (3s)
2. **Desktop recording** of the app on the emulator, scrolling (26.8s)
3. VHS terminal — the Gradle dependency block (28.6s)
4. VHS terminal — the `android {}` block and its two gotchas (20.2s)
5. VHS terminal — the ~6-line render path in `MainActivity.kt` (20.1s)

## What made it work

**Look at the app before narrating.** `adb exec-out screencap -p > shot.png`,
then actually read it. Narration that names what is actually on screen — the
section headings, the specific rows — is accurate because it was read off the
screen rather than guessed. Viewers notice immediately when it is not.

**Record only the emulator window.** Get its rectangle, bring it to the front,
pass `--region`:

```bash
osascript -e 'tell application "System Events" to tell process "qemu-system-aarch64"
  repeat with w in windows
    if name of w contains "Android Emulator" then
      return "" & (item 1 of position of w) & "," & (item 2 of position of w) & "," & (item 1 of size of w) & "," & (item 2 of size of w)
    end if
  end repeat
end tell'
# -> 100,100,353,813

python3 $SCRIPT record start --out app_scene.mov --region 100,100,353,813
sleep 3.5
for i in 1 2 3 4 5; do adb shell input swipe 540 1700 540 800 700; sleep 2.6; done
python3 $SCRIPT record stop
```

Retina doubles it: a 353x813 region yields a 706x1626 clip, which letterboxes
cleanly into 1920x1080.

**Scroll on a timer, not all at once.** Five swipes with 2.6s between them reads
as deliberate; continuous scrolling is unwatchable and outruns the narration.

**Code belongs in VHS tapes, not screen recordings.** Sharper, no window
chrome, reproducible. Match the closing `Sleep` to the narration length.

**Phonetic identifiers.** The narration says "androidx dot a 2 u i",
"material 3 dash a 2 u i", "one point zero point zero, alpha one". Written
literally, `say` produces noise.

## Rebuild it

```bash
python3 ~/.claude/skills/demo-video/scripts/demo_video.py build storyboard.json
```

You only need the emulator running if you re-record the screen capture;
otherwise the existing clip is reused and a rebuild takes about a minute.
