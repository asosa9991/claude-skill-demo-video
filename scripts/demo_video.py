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


def norm_audio(vo, dst, target):
    need_ffmpeg()
    if vo and Path(vo).exists():
        run([FFMPEG, "-y", "-i", str(vo), "-af", "apad", "-t", f"{target:.3f}",
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


def scene_signature(sc, base, voice, rate, res, fps, narration):
    bits = [narration, str(sc.get("voice", voice)), str(sc.get("rate", rate)),
            res, str(fps), str(sc.get("duration", ""))]
    for key in ("clip", "image"):
        if key in sc:
            bits += [key, file_sig(resolve_path(base, sc[key]))]
    if "terminal" in sc:
        bits += ["terminal", file_sig(resolve_path(base, sc["terminal"]["tape"]))]
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


def load_storyboard(path):
    p = Path(path)
    if not p.exists():
        die(f"storyboard not found: {p}")
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

    tc = sb.get("title_card")
    if tc:
        d = float(tc.get("duration", 2.5))
        p = title_card(tc.get("text", sb.get("title", "")), tc.get("subtitle"),
                       work / "scene_title.mp4", d, res, fps, work)
        if p:
            a = norm_audio(None, work / "scene_title.m4a", d)
            parts.append(mux(p, a, work / "scene_title_av.mp4"))
            clock += d

    for i, sc in enumerate(scenes):
        tag = f"scene_{i:02d}"
        narration = (sc.get("narration") or "").strip()
        sig = scene_signature(sc, base, voice, rate, res, fps, narration)
        hit = cache.get(tag)
        av_path = work / f"{tag}_av.mp4"
        if hit and hit.get("sig") == sig and av_path.exists():
            parts.append(av_path)
            target, vo_dur = float(hit["target"]), float(hit["vo_dur"])
            fresh[tag] = hit
            reused += 1
            if narration and captions != "none":
                subs.extend(chunk_narration(narration, clock, max(vo_dur, 0.8)))
            clock += target
            info(f"{tag}: {target:.2f}s (cached)")
            continue

        vo, vo_dur = None, 0.0
        if narration:
            vo = work / f"{tag}.aiff"
            vo_dur = make_vo(narration, vo, sc.get("voice", voice),
                             int(sc.get("rate", rate)))

        kind, src = "clip", None
        if "clip" in sc:
            src = (base / sc["clip"]) if not Path(sc["clip"]).is_absolute() else Path(sc["clip"])
            if not src.exists():
                die(f"{tag}: clip not found: {src}")
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
        a = norm_audio(vo, work / f"{tag}_a.m4a", target)
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
        info(f"captions: {srt}")
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
            vo_dur = make_vo(narration, tmp / f"{tag}.aiff",
                             sc.get("voice", voice), int(sc.get("rate", rate)))

        src_dur, est = 0.0, ""
        if "clip" in sc:
            src = resolve_path(base, sc["clip"])
            src_dur = probe_duration(src) if src.exists() else 0.0
            if not src.exists():
                est = "clip MISSING"
        elif "image" in sc:
            src_dur = float(sc.get("duration", 0))
        elif "pause" in sc:
            src_dur = float(sc.get("pause") or 0)
        elif "screen" in sc:
            src_dur = float(sc["screen"].get("duration", 0))
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
