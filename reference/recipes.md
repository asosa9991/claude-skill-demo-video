# Recipes

## An app walkthrough

```bash
python3 $SCRIPT record start --out app.mov --window "My App"
# drive the app
python3 $SCRIPT record stop
```

```markdown
## It running
@clip app.mov 3-20
Narration describing exactly what is on screen.
```

## An Android emulator

`adb` captures the device directly — better quality, no macOS permission, and it
keeps the emulator chrome out of frame.

```bash
adb shell screenrecord --time-limit 60 /sdcard/f.mp4 &
adb shell am start -n com.example.app/.MainActivity
# drive it with: adb shell input tap X Y
adb pull /sdcard/f.mp4 flow.mp4
```

Find tap targets instead of guessing coordinates:

```bash
adb shell uiautomator dump /sdcard/u.xml
adb shell cat /sdcard/u.xml | tr '>' '\n' | grep -F 'Button label' \
  | grep -oE 'bounds="\[[0-9]+,[0-9]+\]\[[0-9]+,[0-9]+\]"' | head -1 \
  | sed 's/[^0-9]/ /g' | awk '{print int(($1+$3)/2), int(($2+$4)/2)}'
```

## Code on screen

Prefer `@run` over screen-recording an editor: sharper, no window chrome, and
it re-renders when the code changes.

```markdown
## The render path
@run sed -n '/val processor/,/Surface(surface)/p' src/Main.kt
Six lines, and this is the whole integration.
```

Avoid mixing `'` and `"` in one command — the tape then has to run it from a
file and the video shows that instead of your command.

## A window's exact rectangle

`--window` usually suffices. If you need the numbers:

```bash
osascript -e 'tell application "System Events" to tell process "PROC"
  set w to window 1
  set p to position of w
  set s to size of w
  return ((item 1 of p) as integer as text) & "," & ((item 2 of p) as integer as text) & "," & ((item 1 of s) as integer as text) & "," & ((item 2 of s) as integer as text)
end tell'
```

An app can be running with no window, and then this returns nothing.

## Sharing it

```bash
python3 $SCRIPT page demo.mp4 --title "My Feature" --meta "v1.2|3 sections"
```

Builds `page/index.html` with the video, chapters that seek it, inlined captions
and a poster — publish that folder. Keep the page's video under 15 MB; re-encode
with `-crf 33` if the master is larger.

## Rebuilding after an edit

Scenes are cached by content. Change one line of narration and only that scene
re-renders. `--no-cache` forces everything.
