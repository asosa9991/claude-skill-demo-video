#!/usr/bin/env python3
"""demo-video — build narrated demo videos on macOS from a storyboard.

Pipeline: generate voiceover with `say` -> capture or collect each scene's
video -> fit each clip to its narration -> concat -> optional captions.

Every scene is normalised to the same codec/resolution/fps/audio layout before
concatenation, which is what makes the concat demuxer safe here.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
import sys
import time
from pathlib import Path

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
SAY = shutil.which("say") or "/usr/bin/say"
SCREENCAPTURE = shutil.which("screencapture") or "/usr/sbin/screencapture"
VHS = shutil.which("vhs")

DEFAULT_VOICE = "Samantha"
DEFAULT_RATE = 175
DEFAULT_RES = "1920x1080"
DEFAULT_FPS = 30
MIN_SCENE = 1.5

FONT_CANDIDATES = [
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
]


def die(msg):
    print(f"demo-video: error: {msg}", file=sys.stderr)
    sys.exit(1)


def info(msg):
    print(f"demo-video: {msg}", flush=True)


def run(cmd, check=True, capture=False):
    if capture:
        p = subprocess.run(cmd, capture_output=True, text=True)
        if check and p.returncode != 0:
            die(f"command failed: {' '.join(str(c) for c in cmd)}\n{p.stderr.strip()}")
        return p.stdout.strip()
    p = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if check and p.returncode != 0:
        die(f"command failed: {' '.join(str(c) for c in cmd)}\n{p.stderr.strip()[-2000:]}")
    return p.returncode


def need_ffmpeg():
    if not FFMPEG or not FFPROBE:
        die("ffmpeg/ffprobe not found. Install with: brew install ffmpeg")


def probe_duration(path):
    need_ffmpeg()
    out = run([FFPROBE, "-v", "error", "-show_entries", "format=duration",
               "-of", "default=nw=1:nk=1", str(path)], capture=True)
    try:
        return float(out)
    except (TypeError, ValueError):
        die(f"could not read duration of {path} (is it a valid media file?)")


def pick_font():
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return f
    return None


_FILTERS = None


def has_filter(name):
    """Homebrew's ffmpeg ships without freetype/libass, so never assume a filter."""
    global _FILTERS
    if _FILTERS is None:
        need_ffmpeg()
        _FILTERS = run([FFMPEG, "-hide_banner", "-filters"], capture=True)
    return f" {name} " in _FILTERS


BROWSER_CANDIDATES = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
]


def find_browser():
    for b in BROWSER_CANDIDATES:
        if Path(b).exists():
            return b
    return shutil.which("chromium") or shutil.which("google-chrome")


def render_html_png(html, out, w, h, transparent=False):
    """Render HTML to PNG with headless Chrome. Returns the path, or None."""
    br = find_browser()
    if not br:
        return None
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = out.with_suffix(".html")
    src.write_text(html, encoding="utf-8")
    cmd = [br, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
           f"--window-size={w},{h}", f"--screenshot={out}"]
    if transparent:
        cmd.append("--default-background-color=00000000")
    cmd.append(str(src))
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out if out.exists() and out.stat().st_size > 0 else None


def esc(t):
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


SANS = "-apple-system,'SF Pro Display',Helvetica,Arial,sans-serif"

TITLE_HTML = """<html><body style="margin:0;width:{w}px;height:{h}px;background:{bg};
display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:{sans};">
<div style="color:#fff;font-size:{fs}px;font-weight:600;letter-spacing:-0.02em;
text-align:center;max-width:82%;line-height:1.15;">{title}</div>{sub}</body></html>"""

SUB_HTML = """<div style="color:#9aa0aa;font-size:{fs}px;margin-top:{mt}px;
text-align:center;max-width:74%;line-height:1.4;">{text}</div>"""

CAP_HTML = """<html><body style="margin:0;width:{w}px;height:{h}px;background:transparent;
display:flex;align-items:flex-end;justify-content:center;font-family:{sans};">
<div style="margin-bottom:{mb}px;max-width:84%;background:rgba(0,0,0,0.74);color:#fff;
font-size:{fs}px;line-height:1.38;padding:{pv}px {ph}px;border-radius:10px;
text-align:center;">{text}</div></body></html>"""


def burn_captions(src, subs, dst, res, fps, work):
    """Burn captions by overlaying pre-rendered PNGs (no libass required).

    A single-frame PNG persists for the whole overlay because overlay's
    repeatlast defaults to on; `enable` limits when it is visible.
    """
    if not find_browser():
        return None
    w, h = (int(x) for x in res.split("x"))
    pngs = []
    for i, (start, end, text) in enumerate(subs):
        html = CAP_HTML.format(w=w, h=h, sans=SANS, mb=int(h * 0.06),
                               fs=max(15, int(h * 0.032)), pv=int(h * 0.014),
                               ph=int(h * 0.022), text=esc(text))
        png = render_html_png(html, Path(work) / f"cap_{i:02d}.png", w, h, transparent=True)
        if not png:
            return None
        pngs.append((png, start, end))

    cmd = [FFMPEG, "-y", "-i", str(src)]
    for png, _, _ in pngs:
        cmd += ["-i", str(png)]
    chain, cur = [], "0:v"
    for i, (_, start, end) in enumerate(pngs, start=1):
        nxt = f"v{i}"
        chain.append(f"[{cur}][{i}:v]overlay=0:0:"
                     f"enable='between(t,{start:.3f},{end:.3f})'[{nxt}]")
        cur = nxt
    cmd += ["-filter_complex", ";".join(chain), "-map", f"[{cur}]", "-map", "0:a",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(dst)]
    run(cmd)
    return dst


# ---------------------------------------------------------------- voiceover

def make_vo(text, out_path, voice=DEFAULT_VOICE, rate=DEFAULT_RATE):
    """Render narration to an aiff via macOS `say`. Returns duration."""
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Pass text via file so quotes/newlines in narration never hit the shell.
    txt = out_path.with_suffix(".txt")
    txt.write_text(text, encoding="utf-8")
    # voice "system" omits -v, which is the ONLY way to reach Siri neural
    # voices: they cannot be passed to `say -v`, but bare `say` follows the
    # Spoken Content default voice.
    cmd = [SAY]
    if voice and voice.lower() != "system":
        cmd += ["-v", voice]
    cmd += ["-r", str(rate), "-f", str(txt), "-o", str(out_path)]
    run(cmd)
    if not out_path.exists():
        die(f"say produced no audio for scene text: {text[:60]!r}")
    return probe_duration(out_path)


def cmd_vo(args):
    text = args.text
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
    if not text:
        die("provide --text or --file")
    d = make_vo(text, args.out, args.voice, args.rate)
    info(f"wrote {args.out} ({d:.2f}s)")


def cmd_voices(args):
    out = run([SAY, "-v", "?"], capture=True)
    good = ["Samantha", "Alex", "Ava", "Allison", "Tom", "Susan", "Evan",
            "Zoe", "Nicky", "Noelle", "Daniel", "Serena", "Karen", "Moira"]
    installed = {}
    for line in out.splitlines():
        parts = line.split()
        if not parts:
            continue
        # Split before the language tag: a name exactly as wide as the column
        # is followed by a single space, so splitting on 2+ spaces misses it.
        name = re.split(r"\s+(?=[a-z]{2}_[A-Z]{2}\b)", line)[0].strip()
        installed[name] = line
    print("Recommended voices that are installed:")
    found = False
    for g in good:
        for name, line in installed.items():
            if name.lower().startswith(g.lower()):
                print("  " + line)
                found = True
                break
    if not found:
        print("  (none of the recommended voices found)")
    tiers = [n for n in installed if "(Premium)" in n or "(Enhanced)" in n]
    print(f"\nEnhanced/Premium installed: {len(tiers)}")
    for n in tiers[:12]:
        print("  " + n)
    if not tiers:
        print("  none — every voice above is the low-quality compact build.")
    print(f"\n{len(installed)} voices total.")
    print("\nQuality tiers, worst to best:")
    print("  compact   (default, robotic)")
    print("  Enhanced / Premium  -> System Settings > Accessibility > Spoken")
    print("                         Content > System Voice > Manage Voices")
    print("                         usable as:  \"voice\": \"Ava (Premium)\"")
    print("  Siri      -> cannot be passed to `say -v`. Set it as the Spoken")
    print("               Content system voice, then use \"voice\": \"system\"")


# ---------------------------------------------------------------- capture

def state_dir(base="."):
    d = Path(base) / ".demo-video"
    d.mkdir(parents=True, exist_ok=True)
    return d


WINDOW_AS = """
tell application "System Events"
  set out to ""
  repeat with pr in (every process whose visible is true)
    try
      tell pr
        repeat with w in windows
          set wn to name of w
          if wn is not missing value then
            if wn contains "%s" or (name of pr) contains "%s" then
              set pp to position of w
              set ss to size of w
              set x to (item 1 of pp) as integer
              set y to (item 2 of pp) as integer
              set ww to (item 1 of ss) as integer
              set hh to (item 2 of ss) as integer
              if ww > 0 and hh > 0 then
                return (x as text) & "," & (y as text) & "," & (ww as text) & "," & (hh as text)
              end if
            end if
          end if
        end repeat
      end tell
    end try
  end repeat
  return out
end tell
"""


def window_region(match):
    """Resolve a window title (or app name) substring to x,y,w,h."""
    script = WINDOW_AS % (match, match)
    out = subprocess.run(["osascript", "-e", script],
                         capture_output=True, text=True).stdout.strip()
    if not out:
        die(f"no visible window matching {match!r}. The app may be running with "
            "no open window, or minimised. Titles match as substrings.")
    return parse_region(out)


def parse_region(region):
    """Validate x,y,w,h. An empty or malformed region must never silently
    fall back to recording the whole screen."""
    if region is None:
        return None
    r = str(region).strip()
    if not r:
        die("--region was empty. Pass x,y,width,height, use --window NAME, or "
            "omit both to record the full screen deliberately.")
    parts = r.split(",")
    if len(parts) != 4:
        die(f"--region must be x,y,width,height (got {r!r})")
    try:
        x, y, w, h = (int(float(v.strip())) for v in parts)
    except ValueError:
        die(f"--region values must be numbers (got {r!r})")
    if w <= 0 or h <= 0:
        die(f"--region width and height must be positive (got {w}x{h})")
    return f"{x},{y},{w},{h}"


def resolve_region(args):
    """--window wins over --region; neither means full screen."""
    win = getattr(args, "window", None)
    if win:
        reg = window_region(win)
        info(f"window {win!r} -> region {reg}")
        return reg
    return parse_region(getattr(args, "region", None))


def screen_cmd(out, display=None, region=None, cursor=True):
    cmd = [SCREENCAPTURE, "-v", "-x"]
    if cursor:
        cmd.append("-C")
    if display:
        cmd += ["-D", str(display)]
    if region:
        cmd += ["-R", parse_region(region)]
    cmd.append(str(out))
    return cmd


def cmd_record_start(args):
    sd = state_dir()
    pidfile = sd / "record.pid"
    if pidfile.exists():
        die(f"a recording is already in progress ({pidfile}). Run: record stop")
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    proc = subprocess.Popen(screen_cmd(out, args.display, resolve_region(args)),
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    pidfile.write_text(json.dumps({"pid": proc.pid, "out": str(out)}))
    time.sleep(1.5)  # let the capture actually start before actions begin
    if proc.poll() is not None:
        pidfile.unlink(missing_ok=True)
        die("screencapture exited immediately — most likely the Screen Recording "
            "permission is not granted. Run: demo_video.py check")
    info(f"recording -> {out} (pid {proc.pid}); run `record stop` when done")


def cmd_record_stop(args):
    sd = state_dir()
    pidfile = sd / "record.pid"
    if not pidfile.exists():
        die("no recording in progress")
    st = json.loads(pidfile.read_text())
    pid, out = st["pid"], Path(st["out"])
    try:
        os.kill(pid, signal.SIGINT)   # SIGINT makes screencapture finalise the file
    except ProcessLookupError:
        pass
    for _ in range(80):
        if out.exists() and out.stat().st_size > 0:
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                break
        time.sleep(0.25)
    pidfile.unlink(missing_ok=True)
    if not out.exists() or out.stat().st_size == 0:
        die(f"no video was written to {out}. Check Screen Recording permission.")
    info(f"stopped; {out} ({probe_duration(out):.2f}s)")


def cmd_record_screen(args):
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        out.unlink()
    cmd = screen_cmd(out, args.display, resolve_region(args))
    cmd.insert(2, "-V")
    cmd.insert(3, str(args.duration))
    info(f"recording {args.duration}s -> {out}")
    run(cmd)
    if not out.exists():
        die("screencapture wrote no file — check Screen Recording permission "
            "(demo_video.py check)")
    info(f"wrote {out} ({probe_duration(out):.2f}s)")


def cmd_record_terminal(args):
    if not VHS:
        die("vhs not found. Install with: brew install vhs")
    tape = Path(args.tape)
    if not tape.exists():
        die(f"tape file not found: {tape}")
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    run([VHS, str(tape), "-o", str(out)])
    if not out.exists():
        die(f"vhs produced no output at {out}")
    info(f"wrote {out} ({probe_duration(out):.2f}s)")


# ---------------------------------------------------------------- scene build

def norm_video(src, dst, target, res, fps, src_kind="clip", workdir=None):
    """Normalise a source to fixed res/fps/pix_fmt and exactly `target` seconds."""
    need_ffmpeg()
    w, h = res.split("x")
    vf = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
          f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,fps={fps},format=yuv420p")

    if src_kind == "image":
        cmd = [FFMPEG, "-y", "-loop", "1", "-t", f"{target:.3f}", "-i", str(src)]
    elif src_kind == "pause":
        cmd = [FFMPEG, "-y", "-f", "lavfi", "-t", f"{target:.3f}",
               "-i", f"color=c=black:s={w}x{h}:r={fps}"]
    else:
        cur = probe_duration(src)
        if cur + 0.05 < target:
            vf += f",tpad=stop_mode=clone:stop_duration={target - cur:.3f}"
        cmd = [FFMPEG, "-y", "-i", str(src)]

    cmd += ["-an", "-vf", vf, "-t", f"{target:.3f}",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", str(dst)]
    run(cmd)
    return dst


def norm_audio(vo, dst, target, loudness=-16):
    need_ffmpeg()
    if vo and Path(vo).exists():
        # Normalise every scene to the same loudness so a series of videos, or
        # scenes voiced at different rates, do not drift in level.
        af = "apad" if loudness is None else \
            f"loudnorm=I={loudness}:TP=-1.5:LRA=11,apad"
        run([FFMPEG, "-y", "-i", str(vo), "-af", af, "-t", f"{target:.3f}",
             "-ar", "48000", "-ac", "2", "-c:a", "aac", "-b:a", "128k", str(dst)])
    else:
        run([FFMPEG, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
             "-t", f"{target:.3f}", "-c:a", "aac", "-b:a", "128k", str(dst)])
    return dst


def mux(v, a, dst):
    run([FFMPEG, "-y", "-i", str(v), "-i", str(a),
         "-c", "copy", "-shortest", str(dst)])
    return dst


def title_card(text, subtitle, dst, target, res, fps, work):
    """Render a title card. Prefers headless Chrome, falls back to drawtext,
    then to a plain colour card."""
    w, h = (int(x) for x in res.split("x"))
    bg = "#101014"
    sub = SUB_HTML.format(fs=max(16, int(h * 0.034)), mt=int(h * 0.028),
                          text=esc(subtitle)) if subtitle else ""
    html = TITLE_HTML.format(w=w, h=h, bg=bg, sans=SANS,
                             fs=max(24, int(h * 0.072)), title=esc(text), sub=sub)
    png = render_html_png(html, Path(work) / "title.png", w, h)
    if png:
        return norm_video(png, dst, target, res, fps, "image", work)

    font = pick_font()
    if font and has_filter("drawtext"):
        tf = Path(dst).with_suffix(".title.txt")
        tf.write_text(text, encoding="utf-8")
        draw = (f"drawtext=fontfile={font}:textfile={tf}:fontcolor=white:"
                f"fontsize={int(h * 0.072)}:x=(w-text_w)/2:y=(h-text_h)/2")
        run([FFMPEG, "-y", "-f", "lavfi", "-t", f"{target:.3f}",
             "-i", f"color=c=0x101014:s={w}x{h}:r={fps}", "-vf", draw,
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
             "-pix_fmt", "yuv420p", str(dst)])
        return dst

    info("no headless browser and no drawtext filter; title card will have no text")
    return norm_video(None, dst, target, res, fps, "pause", work)


SENT_RE = re.compile(r"(?<=[.!?])\s+")


def chunk_narration(text, start, total, max_chars=90):
    """Split narration into caption-sized pieces, timed proportionally.

    One caption per scene turns a 70-word scene into an unreadable block, so
    split on sentences and share the scene's audio duration by length.
    """
    sents = [x.strip() for x in SENT_RE.split(text.strip()) if x.strip()]
    if not sents:
        return []
    merged = []
    for sent in sents:
        if merged and len(merged[-1]) + len(sent) + 1 <= max_chars:
            merged[-1] += " " + sent
        else:
            merged.append(sent)
    out = []
    for m in merged:
        if len(m) <= max_chars * 1.6:
            out.append(m)
            continue
        buf = ""
        for piece in re.split(r",\s+", m):
            cand = f"{buf}, {piece}" if buf else piece
            if len(cand) > max_chars and buf:
                out.append(buf)
                buf = piece
            else:
                buf = cand
        if buf:
            out.append(buf)
    span = sum(len(x) for x in out) or 1
    entries, t = [], start
    for x in out:
        d = total * (len(x) / span)
        entries.append((t, t + d, x))
        t += d
    return entries


def _typing_estimate(command):
    return len(command) * 0.026 + 1.0


def estimate_tape_duration(tape_path):
    """Rough VHS tape length from its Sleep/Type directives, for planning."""
    typing, total = 0.05, 0.0
    for line in Path(tape_path).read_text().splitlines():
        ls = line.strip()
        m = re.match(r"Set\s+TypingSpeed\s+([\d.]+)(ms|s)", ls)
        if m:
            typing = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
            continue
        m = re.match(r"Sleep\s+([\d.]+)(ms|s)?", ls)
        if m:
            total += float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
            continue
        m = re.match(r'Type\s+"(.*)"', ls)
        if m:
            total += len(m.group(1)) * typing
            continue
        if re.match(r"(Enter|Backspace|Tab|Ctrl\+|Down|Up|Left|Right)", ls):
            total += 0.1
    return total


def load_pronounce(base, sb):
    """Merge pronounce.json in the storyboard's folder with an inline `pronounce` map."""
    rules = {}
    f = Path(base) / "pronounce.json"
    if f.exists():
        try:
            rules.update(json.loads(f.read_text()))
        except ValueError:
            die(f"{f} is not valid JSON")
    rules.update(sb.get("pronounce") or {})
    return rules


def apply_pronounce(text, rules):
    """Rewrite terms for the voice only. Captions keep the real spelling, so
    the audio says 'androidx dot a 2 u i' while the caption reads androidx.a2ui.
    Longest keys first, so a specific term wins over a prefix of it."""
    if not rules:
        return text
    for key in sorted(rules, key=len, reverse=True):
        text = text.replace(key, rules[key])
    return text


def resolve_path(base, value):
    q = Path(value)
    return q if q.is_absolute() else base / q


def file_sig(path):
    q = Path(path)
    if not q.exists():
        return "missing"
    st = q.stat()
    if st.st_size <= 200_000:
        return hashlib.sha256(q.read_bytes()).hexdigest()[:16]
    return f"{st.st_size}:{int(st.st_mtime)}"


def scene_signature(sc, base, voice, rate, res, fps, narration, loudness=None):
    bits = [narration, str(sc.get("voice", voice)), str(sc.get("rate", rate)),
            res, str(fps), str(sc.get("duration", "")), str(loudness)]
    for key in ("clip", "image"):
        if key in sc:
            bits += [key, file_sig(resolve_path(base, sc[key]))]
    if "terminal" in sc:
        t = sc["terminal"]
        if t.get("run"):
            bits += ["run", t["run"]]
        else:
            bits += ["terminal", file_sig(resolve_path(base, t["tape"]))]
    if "card" in sc:
        bits += ["card", json.dumps(sc["card"], sort_keys=True)]
    if "from" in sc or "to" in sc:
        bits += ["trim", str(sc.get("from")), str(sc.get("to"))]
    if "pause" in sc:
        bits += ["pause", str(sc["pause"])]
    if "screen" in sc:
        bits += ["screen", json.dumps(sc["screen"], sort_keys=True)]
    return hashlib.sha256("|".join(bits).encode()).hexdigest()[:20]


def srt_time(t):
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def write_srt(entries, path):
    lines = []
    for i, (start, end, text) in enumerate(entries, 1):
        lines += [str(i), f"{srt_time(start)} --> {srt_time(end)}", text, ""]
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def srt_to_vtt(srt_text):
    return "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt_text)


def write_transcript(title, scenes, path):
    lines = [f"# {title}", ""] if title else []
    for sc in scenes:
        n = (sc.get("narration") or "").strip()
        if n:
            lines += [n, ""]
    Path(path).write_text("\n".join(lines).strip() + "\n", encoding="utf-8")


def chapter_title(sc, i):
    if sc.get("chapter"):
        return str(sc["chapter"])
    n = (sc.get("narration") or "").strip()
    if not n:
        return f"Scene {i + 1}"
    first = SENT_RE.split(n)[0].strip().rstrip(".")
    return first if len(first) <= 52 else first[:49].rsplit(" ", 1)[0] + "..."


SCRIPT_HELP = """A script is markdown. Front matter sets the defaults, each
`## ` heading starts a scene, one @directive picks what is on screen, and the
prose under it is the narration."""


def _front_matter(text):
    meta, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].strip().splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            body = text[end + 4:]
    for k in ("rate", "fps"):
        if k in meta:
            meta[k] = int(meta[k])
    if "loudness" in meta:
        meta["loudness"] = None if meta["loudness"] == "none" else int(meta["loudness"])
    return meta, body


DIRECTIVE = re.compile(r"^@(card|run|clip|image|pause)\s*(.*)$", re.I)


def parse_script(path):
    """Markdown -> storyboard. Narration is prose, not a JSON string."""
    text = Path(path).read_text(encoding="utf-8")
    meta, body = _front_matter(text)
    sb = {"scenes": []}
    sb.update(meta)

    title_text = None
    chunks = re.split(r"^##\s+", body, flags=re.M)
    head = chunks[0]
    m = re.search(r"^#\s+(.+)$", head, flags=re.M)
    if m:
        title_text = m.group(1).strip()
    sub = re.search(r"^>\s*(.+)$", head, flags=re.M)
    if title_text:
        sb.setdefault("title", title_text)
        sb["title_card"] = {"text": title_text, "duration": float(meta.get("title_seconds", 4.0))}
        if sub:
            sb["title_card"]["subtitle"] = sub.group(1).strip()

    for chunk in chunks[1:]:
        lines = chunk.splitlines()
        chapter = lines[0].strip()
        scene = {"chapter": chapter}
        narration = []
        for line in lines[1:]:
            d = DIRECTIVE.match(line.strip())
            if d:
                kind, arg = d.group(1).lower(), d.group(2).strip()
                if kind == "card":
                    parts = [x.strip() for x in arg.split("|")]
                    scene["card"] = {"number": parts[0] if len(parts) > 1 else "",
                                     "title": parts[1] if len(parts) > 1 else parts[0],
                                     "subtitle": parts[2] if len(parts) > 2 else ""}
                elif kind == "run":
                    scene["terminal"] = {"run": arg}
                elif kind == "clip":
                    bits = arg.split()
                    scene["clip"] = bits[0]
                    if len(bits) > 1 and "-" in bits[1]:
                        a, b = bits[1].split("-", 1)
                        scene["from"] = float(a)
                        scene["to"] = float(b)
                elif kind == "image":
                    bits = arg.split()
                    scene["image"] = bits[0]
                    if len(bits) > 1:
                        scene["duration"] = float(bits[1])
                elif kind == "pause":
                    scene["pause"] = float(arg or 1.5)
            elif line.strip():
                narration.append(line.strip())
        scene["narration"] = " ".join(narration)
        sb["scenes"].append(scene)
    return sb


CARD_HTML = """<html><body style="margin:0;width:{w}px;height:{h}px;background:{bg};
display:flex;flex-direction:column;align-items:center;justify-content:center;font-family:{sans};">
{num}<div style="color:#fff;font-size:{fs}px;font-weight:600;letter-spacing:-0.02em;
text-align:center;max-width:84%;line-height:1.12;">{title}</div>{sub}</body></html>"""


def render_card(card, out, res):
    """A numbered section card, so nobody hand-writes HTML for these again."""
    w, h = (int(x) for x in res.split("x"))
    num = ""
    if str(card.get("number", "")).strip():
        num = (f'<div style="color:#5b8cff;font-size:{int(h * 0.125)}px;font-weight:700;'
               f'line-height:1;">{esc(str(card["number"]))}</div>')
    sub = ""
    if card.get("subtitle"):
        sub = (f'<div style="color:#8b93a7;font-size:{max(18, int(h * 0.029))}px;'
               f'margin-top:{int(h * 0.017)}px;text-align:center;max-width:74%;">'
               f'{esc(card["subtitle"])}</div>')
    html = CARD_HTML.format(w=w, h=h, bg="#0f1117", sans=SANS,
                            fs=max(28, int(h * 0.062)), title=esc(card.get("title", "")),
                            num=num, sub=sub)
    return render_html_png(html, out, w, h)


def write_tape(command, out_tape, seconds, res):
    """Generate a VHS tape sized to the narration -- no Sleep to guess."""
    w, h = (int(x) for x in res.split("x"))
    tw = min(1600, int(w * 0.83)) // 2 * 2
    th = min(900, int(h * 0.82)) // 2 * 2
    # VHS has no escape inside Type, so pick the quote style the command allows.
    shown = command
    if '"' not in command:
        typed = f'Type "{command}"'
    elif "'" not in command:
        typed = f"Type '{command}'"
    else:
        # both quote styles present: run it from a file rather than fail
        sh = Path(out_tape).with_suffix(".sh")
        sh.write_text(command + "\n", encoding="utf-8")
        shown = f"sh {sh.name}"
        typed = f'Type "{shown}"'
        info(f"command mixes ' and \" so the tape runs it from {sh.name}; "
             "simplify the quoting if you want it shown verbatim")
    Path(out_tape).write_text(
        f'Set FontSize 17\nSet Width {tw}\nSet Height {th}\nSet Padding 26\n'
        f'Set Theme "Dracula"\nSet TypingSpeed 26ms\n'
        f'{typed}\nSleep 400ms\nEnter\nSleep {seconds:.0f}s\n',
        encoding="utf-8")
    return out_tape


def trim_clip(src, dst, start, end, fps=30):
    """Trim to an exact length.

    adb screenrecord (and most screen capture) is variable frame rate, where
    -t works off timestamps and silently overshoots. Forcing a constant rate
    makes the requested duration the duration you get.
    """
    need_ffmpeg()
    run([FFMPEG, "-y", "-i", str(src), "-ss", f"{start:.2f}", "-t", f"{end - start:.2f}",
         "-vf", f"fps={fps}", "-fps_mode", "cfr",
         "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-an", str(dst)])
    return dst


def load_storyboard(path):
    p = Path(path)
    if not p.exists():
        die(f"storyboard not found: {p}")
    if p.suffix.lower() in (".md", ".markdown"):
        return parse_script(p)
    raw = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            die("storyboard is YAML but PyYAML is not installed. "
                "Use JSON, or: pip install pyyaml")
        return yaml.safe_load(raw)
    return json.loads(raw)


def cmd_build(args):
    need_ffmpeg()
    sb = load_storyboard(args.storyboard)
    base = Path(args.storyboard).parent

    voice = sb.get("voice", DEFAULT_VOICE)
    rate = int(sb.get("rate", DEFAULT_RATE))
    res = sb.get("resolution", DEFAULT_RES)
    fps = int(sb.get("fps", DEFAULT_FPS))
    captions = sb.get("captions", "none")
    loudness = sb.get("loudness", -16)
    rules = load_pronounce(base, sb)
    out_path = Path(args.output or sb.get("output", "demo.mp4"))
    scenes = sb.get("scenes") or []
    if not scenes:
        die("storyboard has no scenes")

    work = Path(args.workdir) if args.workdir else base / ".demo-video" / "build"
    work.mkdir(parents=True, exist_ok=True)

    cache_file = work / "cache.json"
    cache = {}
    if cache_file.exists() and not getattr(args, "no_cache", False):
        try:
            cache = json.loads(cache_file.read_text())
        except ValueError:
            cache = {}
    fresh, reused = {}, 0

    parts, subs, clock = [], [], 0.0
    chapters = []

    tc = sb.get("title_card")
    if tc:
        d = float(tc.get("duration", 2.5))
        p = title_card(tc.get("text", sb.get("title", "")), tc.get("subtitle"),
                       work / "scene_title.mp4", d, res, fps, work)
        if p:
            a = norm_audio(None, work / "scene_title.m4a", d, None)
            parts.append(mux(p, a, work / "scene_title_av.mp4"))
            chapters.append({"start": 0.0, "title": tc.get("text") or sb.get("title") or "Opening"})
            clock += d

    for i, sc in enumerate(scenes):
        tag = f"scene_{i:02d}"
        narration = (sc.get("narration") or "").strip()
        spoken = apply_pronounce(narration, rules)
        sig = scene_signature(sc, base, voice, rate, res, fps, spoken, loudness)
        hit = cache.get(tag)
        av_path = work / f"{tag}_av.mp4"
        if hit and hit.get("sig") == sig and av_path.exists():
            parts.append(av_path)
            target, vo_dur = float(hit["target"]), float(hit["vo_dur"])
            fresh[tag] = hit
            reused += 1
            chapters.append({"start": clock, "title": chapter_title(sc, i)})
            if narration and captions != "none":
                subs.extend(chunk_narration(narration, clock, max(vo_dur, 0.8)))
            clock += target
            info(f"{tag}: {target:.2f}s (cached)")
            continue

        chapters.append({"start": clock, "title": chapter_title(sc, i)})
        vo, vo_dur = None, 0.0
        if narration:
            vo = work / f"{tag}.aiff"
            vo_dur = make_vo(spoken, vo, sc.get("voice", voice),
                             int(sc.get("rate", rate)))

        kind, src = "clip", None
        if "card" in sc:
            # a section card is just an image we render for you
            kind = "image"
            src = work / f"{tag}_card.png"
            if not render_card(sc["card"], src, res):
                die(f"{tag}: cannot render a card without a headless browser")
        elif "terminal" in sc and sc["terminal"].get("run"):
            # THE POINT: the tape is written now, sized to the narration we just
            # measured, so a terminal scene can never outrun or trail its voice.
            if not VHS:
                die(f"{tag}: terminal scene needs vhs. Install with: brew install vhs")
            hold = max(4.0, vo_dur - _typing_estimate(sc["terminal"]["run"]) + 2.5)
            tape = write_tape(sc["terminal"]["run"], work / f"{tag}.tape", hold, res)
            src = work / f"{tag}_term.mp4"
            run([VHS, str(tape), "-o", str(src)])
        elif "clip" in sc:
            src = (base / sc["clip"]) if not Path(sc["clip"]).is_absolute() else Path(sc["clip"])
            if not src.exists():
                die(f"{tag}: clip not found: {src}")
            if "from" in sc or "to" in sc:
                start = float(sc.get("from", 0))
                end = float(sc.get("to", probe_duration(src)))
                src = trim_clip(src, work / f"{tag}_trim.mp4", start, end)
        elif "image" in sc:
            kind = "image"
            src = (base / sc["image"]) if not Path(sc["image"]).is_absolute() else Path(sc["image"])
            if not src.exists():
                die(f"{tag}: image not found: {src}")
        elif "terminal" in sc:
            t = sc["terminal"]
            src = work / f"{tag}_term.mp4"
            if not VHS:
                die(f"{tag}: terminal scene needs vhs. Install with: brew install vhs")
            tape = (base / t["tape"]) if not Path(t["tape"]).is_absolute() else Path(t["tape"])
            run([VHS, str(tape), "-o", str(src)])
        elif "screen" in sc:
            s = sc["screen"]
            src = work / f"{tag}_screen.mov"
            dur = float(s.get("duration", max(vo_dur, 5.0)))
            info(f"{tag}: recording screen for {dur:.1f}s — get the window ready")
            for n in range(int(s.get("countdown", 3)), 0, -1):
                info(f"  starting in {n}...")
                time.sleep(1)
            cmd = screen_cmd(src, s.get("display"),
                             window_region(s["window"]) if s.get("window")
                             else parse_region(s.get("region")))
            cmd.insert(2, "-V")
            cmd.insert(3, f"{dur:.0f}")
            run(cmd)
            if not src.exists():
                die(f"{tag}: no screen recording produced — check permissions "
                    "(demo_video.py check)")
        elif "pause" in sc:
            kind = "pause"
        else:
            die(f"{tag}: scene needs one of clip/image/terminal/screen/pause")

        if kind == "pause":
            vid_dur = float(sc.get("pause") or 0)
        elif kind == "image":
            vid_dur = float(sc.get("duration", 0))
        else:
            vid_dur = probe_duration(src)

        target = max(vid_dur, vo_dur, MIN_SCENE)
        v = norm_video(src, work / f"{tag}_v.mp4", target, res, fps, kind, work)
        a = norm_audio(vo, work / f"{tag}_a.m4a", target, loudness)
        parts.append(mux(v, a, av_path))
        fresh[tag] = {"sig": sig, "target": target, "vo_dur": vo_dur}

        if narration and captions != "none":
            subs.extend(chunk_narration(narration, clock, max(vo_dur, 0.8)))
        clock += target
        info(f"{tag}: {target:.2f}s{' (narrated)' if narration else ''}")

    listfile = work / "concat.txt"
    listfile.write_text("".join(f"file '{Path(p).resolve()}'\n" for p in parts))
    joined = work / "joined.mp4"
    run([FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(listfile),
         "-c", "copy", str(joined)])

    out_path.parent.mkdir(parents=True, exist_ok=True)
    srt = out_path.with_suffix(".srt")
    if subs:
        write_srt(subs, srt)
        vtt = out_path.with_suffix(".vtt")
        vtt.write_text(srt_to_vtt(srt.read_text(encoding="utf-8")), encoding="utf-8")
    write_transcript(sb.get("title"), scenes, out_path.with_suffix(".transcript.md"))
    out_path.with_suffix(".chapters.json").write_text(json.dumps(chapters, indent=2))

    if captions == "burn" and subs:
        done = None
        if len(subs) <= 120:
            done = burn_captions(joined, subs, out_path, res, fps, work)
        if not done and has_filter("subtitles"):
            run([FFMPEG, "-y", "-i", str(joined),
                 "-vf", f"subtitles={srt}:force_style='FontSize=18,OutlineColour=&H80000000,BorderStyle=3'",
                 "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                 "-c:a", "copy", "-movflags", "+faststart", str(out_path)])
            done = out_path
        if not done:
            info("cannot burn captions (no headless browser, no subtitles filter); "
                 "writing soft captions instead")
            run([FFMPEG, "-y", "-i", str(joined), "-i", str(srt), "-c", "copy",
                 "-c:s", "mov_text", "-movflags", "+faststart", str(out_path)])
    elif captions == "soft" and subs:
        run([FFMPEG, "-y", "-i", str(joined), "-i", str(srt),
             "-c", "copy", "-c:s", "mov_text", "-movflags", "+faststart", str(out_path)])
    else:
        run([FFMPEG, "-y", "-i", str(joined), "-c", "copy",
             "-movflags", "+faststart", str(out_path)])

    total = probe_duration(out_path)
    size = out_path.stat().st_size / 1e6
    info(f"built {out_path} — {total:.1f}s, {size:.1f} MB, {len(scenes)} scenes")
    if subs:
        info(f"captions: {srt} (+ .vtt)")
    info(f"transcript: {out_path.with_suffix('.transcript.md')}")
    cache_file.write_text(json.dumps(fresh, indent=2))
    if reused:
        info(f"reused {reused} cached scene(s); --no-cache forces a full rebuild")
    if getattr(args, "clean", False):
        for f in work.glob("scene_*"):
            f.unlink(missing_ok=True)
        cache_file.unlink(missing_ok=True)


def cmd_plan(args):
    """Render only the voiceovers and report how each scene will fit.

    Run this BEFORE recording: it tells you how long each capture needs to be,
    instead of discovering a 10-second frozen frame after the fact.
    """
    sb = load_storyboard(args.storyboard)
    base = Path(args.storyboard).parent
    voice = sb.get("voice", DEFAULT_VOICE)
    rate = int(sb.get("rate", DEFAULT_RATE))
    scenes = sb.get("scenes") or []
    if not scenes:
        die("storyboard has no scenes")

    tmp = Path(tempfile.mkdtemp(prefix="demo-video-plan-"))
    total, warnings = 0.0, 0

    tc = sb.get("title_card")
    if tc:
        total += float(tc.get("duration", 2.5))

    print(f"{'scene':<9} {'words':>5} {'voice':>8} {'source':>9} {'scene':>8}  note")
    print("-" * 68)
    for i, sc in enumerate(scenes):
        tag = f"scene_{i:02d}"
        narration = (sc.get("narration") or "").strip()
        words = len(narration.split())
        vo_dur = 0.0
        if narration:
            vo_dur = make_vo(apply_pronounce(narration, load_pronounce(base, sb)),
                             tmp / f"{tag}.aiff",
                             sc.get("voice", voice), int(sc.get("rate", rate)))

        src_dur, est = 0.0, ""
        if "clip" in sc:
            src = resolve_path(base, sc["clip"])
            src_dur = probe_duration(src) if src.exists() else 0.0
            if "from" in sc or "to" in sc:
                src_dur = float(sc.get("to", src_dur)) - float(sc.get("from", 0))
            if not src.exists():
                est = "clip MISSING"
        elif "image" in sc:
            src_dur = float(sc.get("duration", 0))
        elif "pause" in sc:
            src_dur = float(sc.get("pause") or 0)
        elif "screen" in sc:
            src_dur = float(sc["screen"].get("duration", 0))
        elif "card" in sc:
            src_dur = 0.0
        elif "terminal" in sc and sc["terminal"].get("run"):
            src_dur, est = vo_dur, "auto"
        elif "terminal" in sc:
            tape = resolve_path(base, sc["terminal"]["tape"])
            src_dur = estimate_tape_duration(tape) if tape.exists() else 0.0
            est = "~est"
            if not tape.exists():
                est = "tape MISSING"

        target = max(src_dur, vo_dur, MIN_SCENE)
        note = est
        if src_dur and vo_dur > src_dur + 3:
            note = (note + " " if note else "") + f"FREEZE {vo_dur - src_dur:.1f}s"
            warnings += 1
        elif vo_dur and src_dur > vo_dur + 4:
            note = (note + " " if note else "") + f"silence {src_dur - vo_dur:.1f}s"
        total += target
        print(f"{tag:<9} {words:>5} {vo_dur:>7.1f}s {src_dur:>8.1f}s {target:>7.1f}s  {note}")

    print("-" * 68)
    mins, secs = divmod(total, 60)
    print(f"total {int(mins)}m {secs:04.1f}s across {len(scenes)} scenes"
          + (f" (+ title card)" if tc else ""))
    if warnings:
        print(f"\n{warnings} scene(s) will hold a frozen frame for 3s or more.")
        print("Record longer, raise the VHS `Sleep`, or shorten the narration.")
    else:
        print("\nEvery scene fits its narration.")
    shutil.rmtree(tmp, ignore_errors=True)


def cmd_quick(args):
    need_ffmpeg()
    work = Path(".demo-video/quick")
    work.mkdir(parents=True, exist_ok=True)
    clip = work / "capture.mov"
    info(f"recording {args.duration}s of the screen — get ready")
    for n in range(3, 0, -1):
        info(f"  starting in {n}...")
        time.sleep(1)
    cmd = screen_cmd(clip, args.display, resolve_region(args))
    cmd.insert(2, "-V")
    cmd.insert(3, str(args.duration))
    run(cmd)
    if not clip.exists():
        die("no recording produced — check Screen Recording permission")
    sb = {"output": args.out, "voice": args.voice, "rate": args.rate,
          "scenes": [{"narration": args.narration, "clip": str(clip.resolve())}]}
    sbp = work / "storyboard.json"
    sbp.write_text(json.dumps(sb, indent=2))
    ns = argparse.Namespace(storyboard=str(sbp), output=args.out,
                            workdir=str(work), keep=False, no_cache=True,
                            clean=False)
    cmd_build(ns)



PAGE_HTML = """<title>__TITLE__</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600;700&display=swap">
<style>
  :root { color-scheme: light;
    --bg:#f5f6f9; --surface:#fff; --ink:#171a21; --muted:#5d6577;
    --accent:#2f5fe0; --accent-soft:#e8edfd; --rule:#e1e5ec;
    --sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;
    --mono:"IBM Plex Mono",ui-monospace,"SF Mono",Menlo,monospace; }
  @media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
    color-scheme: dark; --bg:#0f1117; --surface:#171a22; --ink:#e8eaf0;
    --muted:#98a0b2; --accent:#7fa2ff; --accent-soft:#1b2440; --rule:#252a35; } }
  :root[data-theme="dark"] { color-scheme: dark;
    --bg:#0f1117; --surface:#171a22; --ink:#e8eaf0; --muted:#98a0b2;
    --accent:#7fa2ff; --accent-soft:#1b2440; --rule:#252a35; }
  body { background:var(--bg); color:var(--ink); font-family:var(--sans);
    font-size:16px; line-height:1.6; -webkit-font-smoothing:antialiased; }
  .wrap { max-width:920px; margin:0 auto; padding-inline:20px;
    padding-block:40px 72px; display:flex; flex-direction:column; gap:32px; }
  .eyebrow { font-family:var(--mono); font-size:12px; letter-spacing:.09em;
    text-transform:uppercase; color:var(--accent); }
  h1 { margin:0; font-size:clamp(30px,5vw,44px); font-weight:700;
    letter-spacing:-.025em; line-height:1.1; text-wrap:balance; }
  .standfirst { margin:0; max-width:62ch; color:var(--muted); font-size:17px; }
  .meta { display:flex; flex-wrap:wrap; gap:8px 18px; font-family:var(--mono);
    font-size:13px; color:var(--muted); font-variant-numeric:tabular-nums; }
  header { display:flex; flex-direction:column; gap:10px; }
  .player { background:#000; border:1px solid var(--rule); border-radius:12px;
    overflow:hidden; line-height:0; }
  video { width:100%; max-width:100%; aspect-ratio:16/9; display:block; background:#000; }
  h2 { margin:0 0 14px; font-size:13px; font-family:var(--mono); font-weight:500;
    letter-spacing:.09em; text-transform:uppercase; color:var(--muted); }
  .chapters { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:2px; }
  @media (max-width:620px) { .chapters { grid-template-columns:1fr; } }
  .chapter { display:flex; align-items:baseline; gap:14px; width:100%;
    padding:10px 12px; background:transparent; border:0; border-radius:7px;
    font:inherit; color:var(--ink); text-align:left; cursor:pointer; }
  .chapter:hover { background:var(--surface); }
  .chapter[aria-current="true"] { background:var(--accent-soft); color:var(--accent); }
  .chapter:focus-visible { outline:2px solid var(--accent); outline-offset:1px; }
  .t { font-family:var(--mono); font-size:13px; color:var(--accent);
    font-variant-numeric:tabular-nums; flex:none; }
  .chapter[aria-current="true"] .t { color:inherit; }
  details { background:var(--surface); border:1px solid var(--rule);
    border-radius:10px; padding:14px 18px; }
  summary { cursor:pointer; font-weight:600; font-size:15px; }
  details p { color:var(--muted); font-size:15px; white-space:pre-wrap; }
  @media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
</style>
<div class="wrap">
  <header>
    <span class="eyebrow">__EYEBROW__</span>
    <h1>__HEADING__</h1>
    <p class="standfirst">__DESC__</p>
    <div class="meta">__META__</div>
  </header>
  <div class="player">
    <video id="v" controls preload="metadata" poster="poster.jpg" playsinline>
      <source src="video.mp4" type="video/mp4">
    </video>
  </div>
  <section>
    <h2>Chapters</h2>
    <div class="chapters" id="chapters"></div>
  </section>
  __TRANSCRIPT__
</div>
<script>
  const video = document.getElementById("v");
  const list = document.getElementById("chapters");
  const CHAPTERS = __CHAPTERS__;
  const VTT = __VTT__;
  if (VTT) {
    // Inlined: artifacts do not serve .vtt files.
    try {
      const tr = document.createElement("track");
      tr.kind = "captions"; tr.srclang = "en"; tr.label = "English";
      tr.src = URL.createObjectURL(new Blob([VTT], { type: "text/vtt" }));
      video.appendChild(tr);
    } catch (e) {}
  }
  const fmt = (s) => Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
  CHAPTERS.forEach(function (c) {
    const b = document.createElement("button");
    b.className = "chapter"; b.type = "button";
    b.innerHTML = '<span class="t">' + fmt(c.start) + '</span><span></span>';
    b.lastChild.textContent = c.title;
    b.addEventListener("click", function () {
      video.currentTime = c.start;
      video.play().catch(function () {});
    });
    list.appendChild(b);
  });
  const buttons = Array.prototype.slice.call(list.children);
  video.addEventListener("timeupdate", function () {
    let active = 0;
    for (let i = 0; i < CHAPTERS.length; i++) {
      if (video.currentTime >= CHAPTERS[i].start) active = i;
    }
    buttons.forEach(function (b, i) { b.setAttribute("aria-current", String(i === active)); });
  });
</script>
"""


def cmd_page(args):
    """Emit a publish-ready HTML page around a built video.

    Chapters, captions and the transcript already exist as build sidecars, so
    the page is assembled rather than hand-written.
    """
    need_ffmpeg()
    video = Path(args.video)
    if not video.exists():
        die(f"video not found: {video}")
    out_dir = Path(args.out_dir or video.parent / "page")
    out_dir.mkdir(parents=True, exist_ok=True)

    dur = probe_duration(video)
    chap_file = video.with_suffix(".chapters.json")
    chapters = []
    if chap_file.exists():
        chapters = json.loads(chap_file.read_text())
    else:
        info("no .chapters.json beside the video; the page will have no chapters")

    vtt_file = video.with_suffix(".vtt")
    vtt = vtt_file.read_text(encoding="utf-8") if vtt_file.exists() else ""

    tr_file = video.with_suffix(".transcript.md")
    transcript = ""
    if tr_file.exists() and not args.no_transcript:
        body = tr_file.read_text(encoding="utf-8")
        body = "\n".join(l for l in body.splitlines() if not l.startswith("# "))
        transcript = ("<section><details><summary>Full narration</summary><p>"
                      + body.strip().replace("&", "&amp;").replace("<", "&lt;")
                      + "</p></details></section>")

    shutil.copyfile(video, out_dir / "video.mp4")
    poster = out_dir / "poster.jpg"
    run([FFMPEG, "-y", "-ss", f"{min(1.6, dur / 2):.2f}", "-i", str(video),
         "-frames:v", "1", "-vf", "scale=1280:-1", "-q:v", "4", str(poster)])

    mins, secs = divmod(dur, 60)
    meta = f"<span>{int(mins)} min {int(secs):02d} s</span>"
    if args.meta:
        meta += "".join(f"<span>{m.strip()}</span>" for m in args.meta.split("|"))

    title = args.title or video.stem.replace("-", " ").replace("_", " ").title()
    html = (PAGE_HTML
            .replace("__TITLE__", title)
            .replace("__EYEBROW__", args.eyebrow or "Walkthrough")
            .replace("__HEADING__", args.heading or title)
            .replace("__DESC__", args.description or "")
            .replace("__META__", meta)
            .replace("__TRANSCRIPT__", transcript)
            .replace("__CHAPTERS__", json.dumps(chapters))
            .replace("__VTT__", json.dumps(vtt)))
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    info(f"page: {out_dir}/index.html  (+ video.mp4, poster.jpg)")
    info(f"{len(chapters)} chapters, captions {'inlined' if vtt else 'absent'}")
    info("publish it with the Artifact tool, passing video.mp4 and poster.jpg as files")


def cmd_check(args):
    ok = True
    print("dependencies")
    for name, path, how in [
        ("ffmpeg", FFMPEG, "brew install ffmpeg"),
        ("ffprobe", FFPROBE, "brew install ffmpeg"),
        ("say", SAY if Path(SAY).exists() else None, "built into macOS"),
        ("screencapture", SCREENCAPTURE if Path(SCREENCAPTURE).exists() else None, "built into macOS"),
        ("vhs (optional, terminal scenes)", VHS, "brew install vhs"),
    ]:
        mark = "ok  " if path else "MISS"
        if not path and "optional" not in name:
            ok = False
        print(f"  [{mark}] {name}" + (f" -> {path}" if path else f"  ({how})"))

    br = find_browser()
    print(f"  [{'ok  ' if br else 'warn'}] text renderer (title cards, burned-in captions)"
          + (f" -> {Path(br).name}" if br else "  (install Chrome, or captions stay soft)"))
    if FFMPEG:
        print(f"  [{'ok  ' if has_filter('drawtext') else 'warn'}] ffmpeg drawtext filter"
              + ("" if has_filter('drawtext') else "  (not needed when a browser is present)"))

    print("\nvoiceover")
    try:
        t = Path("/tmp/.demo_video_vo_test.aiff")
        d = make_vo("Checking voiceover.", t, args.voice, DEFAULT_RATE)
        print(f"  [ok  ] `say` rendered {d:.2f}s with voice {args.voice}")
        t.unlink(missing_ok=True)
        t.with_suffix(".txt").unlink(missing_ok=True)
    except SystemExit:
        ok = False
        print(f"  [MISS] `say` failed with voice {args.voice} — try: demo_video.py voices")

    print("\nscreen recording permission")
    probe = Path("/tmp/.demo_video_perm_test.mov")
    probe.unlink(missing_ok=True)
    subprocess.run([SCREENCAPTURE, "-v", "-V", "1", "-x", str(probe)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if probe.exists() and probe.stat().st_size > 0:
        print(f"  [ok  ] captured {probe.stat().st_size/1000:.0f} KB test clip")
        probe.unlink(missing_ok=True)
    else:
        ok = False
        print("  [MISS] no clip produced. Grant Screen Recording to your terminal:")
        print("         System Settings > Privacy & Security > Screen & System Audio")
        print("         Recording > add your terminal app, then restart it.")

    print("\n" + ("all required checks passed" if ok else "some required checks FAILED"))
    sys.exit(0 if ok else 1)


def main():
    ap = argparse.ArgumentParser(prog="demo_video.py",
                                 description="Build narrated demo videos on macOS.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("check", help="verify dependencies and permissions")
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("voices", help="list recommended installed voices")
    p.set_defaults(func=cmd_voices)

    p = sub.add_parser("vo", help="render narration to audio")
    p.add_argument("--text")
    p.add_argument("--file")
    p.add_argument("--out", required=True)
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--rate", type=int, default=DEFAULT_RATE)
    p.set_defaults(func=cmd_vo)

    rec = sub.add_parser("record", help="capture source clips")
    rsub = rec.add_subparsers(dest="rcmd", required=True)

    q = rsub.add_parser("start", help="start recording the screen (returns immediately)")
    q.add_argument("--out", required=True)
    q.add_argument("--display", type=int)
    q.add_argument("--region", help="x,y,width,height")
    q.add_argument("--window", help="window title or app name substring; resolves the region for you")
    q.set_defaults(func=cmd_record_start)

    q = rsub.add_parser("stop", help="stop the in-progress recording")
    q.set_defaults(func=cmd_record_stop)

    q = rsub.add_parser("screen", help="record the screen for a fixed duration")
    q.add_argument("--out", required=True)
    q.add_argument("--duration", type=float, required=True)
    q.add_argument("--display", type=int)
    q.add_argument("--region", help="x,y,width,height")
    q.add_argument("--window", help="window title or app name substring; resolves the region for you")
    q.set_defaults(func=cmd_record_screen)

    q = rsub.add_parser("terminal", help="render a VHS tape to video")
    q.add_argument("--tape", required=True)
    q.add_argument("--out", required=True)
    q.set_defaults(func=cmd_record_terminal)

    p = sub.add_parser("build", help="build the video from a storyboard")
    p.add_argument("storyboard")
    p.add_argument("--output")
    p.add_argument("--workdir")
    p.add_argument("--keep", action="store_true",
                   help="deprecated; scene files are kept by default for caching")
    p.add_argument("--no-cache", action="store_true", dest="no_cache",
                   help="re-render every scene even if unchanged")
    p.add_argument("--clean", action="store_true",
                   help="delete intermediates and the cache after building")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("plan", help="preview scene timing without building")
    p.add_argument("storyboard")
    p.set_defaults(func=cmd_plan)

    p = sub.add_parser("page", help="build a publish-ready HTML page around a video")
    p.add_argument("video")
    p.add_argument("--out-dir", dest="out_dir")
    p.add_argument("--title")
    p.add_argument("--heading")
    p.add_argument("--eyebrow")
    p.add_argument("--description", default="")
    p.add_argument("--meta", help="extra pipe-separated facts, e.g. 'A2UI v0.9.1|alpha01'")
    p.add_argument("--no-transcript", action="store_true", dest="no_transcript")
    p.set_defaults(func=cmd_page)

    p = sub.add_parser("quick", help="one screen recording + one narration")
    p.add_argument("--narration", required=True)
    p.add_argument("--duration", type=float, required=True)
    p.add_argument("--out", default="demo.mp4")
    p.add_argument("--display", type=int)
    p.add_argument("--region")
    p.add_argument("--window", help="window title or app name substring")
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--rate", type=int, default=DEFAULT_RATE)
    p.set_defaults(func=cmd_quick)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
