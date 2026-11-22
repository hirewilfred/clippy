"""clip.py: turn a small JSON spec into a finished 1080x1920, 30 fps payoff-first clip.

Usage:
    python -m scripts.clip spec.json [--preview] [--dry-run]

Spec (all times are source-clip seconds; clip.py maps them onto the output timeline):
{
  "src": "source.mp4",
  "out": "renders/GTA-014.mp4",
  "credit": "@ohnepixel (Twitch)",
  "layout": "stack",
  "facecam": [x, y, w, h],              # crop box in source pixels -> top panel
  "game":    [x, y, w, h],              # crop box in source pixels -> bottom panel
  "teaser":  {"start": 22.4, "end": 24.9},
  "teaser_text": "GTA 6 PLANES HAVE ZERO CHILL",
  "keep":    [[14.8, 27.8]],            # ranges played after the flash, in order
  "effect":  {"type": "slowmo", "start": 24.6, "end": 25.6, "factor": 0.5}
          or {"type": "freeze", "at": 25.1, "duration": 0.8},       (optional, at most one)
  "punchlines": [{"start": 25.0, "end": 27.8, "text": "INSURANCE: DENIED"}],
  "subtitles": "hand-timed.srt",        # optional, source times, proofread
  "volume": 1.4                         # optional pre-gain before loudness normalization
}

Output structure:
  [TEASER] -> [WHITE FLASH + WHOOSH] -> [KEEP RANGES with optional slow-mo/freeze] -> hard cut.
Hook text shows only during the teaser. Punchlines and subtitles show only after the
flash, so two text blocks never stack. All text sits in the gameplay panel, never over
the facecam.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

W, H, FPS = 1080, 1920, 30
FACE_H = 768                       # top panel height (40%)
GAME_H = H - FACE_H                # bottom panel height
FLASH_S = 0.18                     # white flash length at the teaser->setup cut
MIN_LEN, MAX_LEN = 12.0, 20.0
LOUDNESS_I = -20                   # target integrated loudness (LUFS)

FONT_CANDIDATES = [
    os.environ.get("CLIPPY_FONT", ""),
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
]
EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF\U00002600-\U000027BF\uFE0F]")


class SpecError(ValueError):
    pass


# ---------------------------------------------------------------- timeline

@dataclass
class Seg:
    """One piece of the output timeline."""
    kind: str          # "teaser" | "keep" | "freeze"
    src_start: float
    src_end: float     # for freeze: src_start == frame time, src_end == src_start
    speed: float       # 1.0 normal, <1 slow-mo; freeze uses 1.0
    out_start: float
    out_dur: float

    @property
    def out_end(self) -> float:
        return self.out_start + self.out_dur


def build_timeline(spec: dict) -> list[Seg]:
    """Lay out teaser + keep ranges (with the optional effect) on the output timeline."""
    t = spec["teaser"]
    segs: list[Seg] = [Seg("teaser", t["start"], t["end"], 1.0, 0.0, t["end"] - t["start"])]
    cursor = segs[0].out_end
    eff = spec.get("effect")

    def add(kind, s, e, speed):
        nonlocal cursor
        if e - s <= 1e-6 and kind != "freeze":
            return
        dur = (e - s) / speed if kind != "freeze" else speed  # freeze: speed slot carries duration
        segs.append(Seg(kind, s, e if kind != "freeze" else s, 1.0 if kind == "freeze" else speed, cursor, dur))
        cursor += dur

    for s, e in spec["keep"]:
        if eff and eff["type"] == "slowmo" and s < eff["end"] and eff["start"] < e:
            a, b = max(s, eff["start"]), min(e, eff["end"])
            add("keep", s, a, 1.0)
            add("keep", a, b, float(eff["factor"]))
            add("keep", b, e, 1.0)
        elif eff and eff["type"] == "freeze" and s <= eff["at"] < e:
            add("keep", s, eff["at"], 1.0)
            add("freeze", eff["at"], eff["at"], float(eff["duration"]))
            add("keep", eff["at"], e, 1.0)
        else:
            add("keep", s, e, 1.0)
    return segs


def map_interval(segs: list[Seg], start: float, end: float, include_teaser: bool = False) -> list[tuple[float, float]]:
    """Map a source-time interval onto every output interval where that source footage plays."""
    out = []
    for g in segs:
        if g.kind == "teaser" and not include_teaser:
            continue
        if g.kind == "freeze":
            if start <= g.src_start < end:
                out.append((g.out_start, g.out_end))
            continue
        a, b = max(start, g.src_start), min(end, g.src_end)
        if b > a:
            out.append((g.out_start + (a - g.src_start) / g.speed, g.out_start + (b - g.src_start) / g.speed))
    return _merge(out)


def _merge(iv: list[tuple[float, float]]) -> list[tuple[float, float]]:
    iv = sorted(iv)
    merged: list[list[float]] = []
    for a, b in iv:
        if merged and a <= merged[-1][1] + 1e-3:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


# ---------------------------------------------------------------- spec checks

def parse_srt(path: str | Path) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8-sig")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.strip().splitlines()
        tl = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
        if tl is None:
            continue
        a, b = [_srt_ts(x.strip()) for x in lines[tl].split("-->")]
        body = " ".join(ln.strip() for ln in lines[tl + 1:] if ln.strip())
        if body:
            cues.append({"start": a, "end": b, "text": body})
    return cues


def _srt_ts(s: str) -> float:
    h, m, rest = s.replace(",", ".").split(":")
    return int(h) * 3600 + int(m) * 60 + float(rest)


def validate(spec: dict, src_wh: tuple[int, int] | None, src_dur: float | None) -> list[str]:
    errs = []
    for k in ("src", "out", "credit", "facecam", "game", "teaser", "teaser_text", "keep"):
        if k not in spec:
            errs.append(f"missing '{k}'")
    if errs:
        return errs
    if spec.get("layout", "stack") != "stack":
        errs.append("only layout 'stack' is supported")
    t = spec["teaser"]
    if not 2.0 <= t["end"] - t["start"] <= 4.5:
        errs.append(f"teaser is {t['end'] - t['start']:.2f}s (need 2-4.5s)")
    if not spec["keep"]:
        errs.append("keep is empty")
    for s, e in spec["keep"]:
        if e <= s:
            errs.append(f"keep range [{s}, {e}] is empty")
    eff = spec.get("effect")
    if eff:
        if eff.get("type") == "slowmo":
            if not 0.25 <= float(eff.get("factor", 0)) <= 0.75:
                errs.append("slowmo factor must be 0.25-0.75")
        elif eff.get("type") == "freeze":
            if not 0.3 <= float(eff.get("duration", 0)) <= 1.5:
                errs.append("freeze duration must be 0.3-1.5s")
        else:
            errs.append("effect type must be 'slowmo' or 'freeze'")
    for name, box in (("facecam", spec["facecam"]), ("game", spec["game"])):
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            errs.append(f"{name} box must be [x, y, w, h]")
        elif src_wh and (box[0] < 0 or box[1] < 0 or box[0] + box[2] > src_wh[0] or box[1] + box[3] > src_wh[1]):
            errs.append(f"{name} box {box} is outside the source frame {src_wh}")
    if src_dur:
        last = max([e for _, e in spec["keep"]] + [t["end"]])
        if last > src_dur + 0.05:
            errs.append(f"spec uses source time {last}s but source is {src_dur:.2f}s")
    texts = [spec["teaser_text"], spec["credit"]] + [p["text"] for p in spec.get("punchlines", [])]
    for tx in texts:
        if EMOJI_RE.search(tx):
            errs.append(f"emoji in burned-in text: {tx!r}")
    if not errs:
        segs = build_timeline(spec)
        total = segs[-1].out_end
        if not MIN_LEN <= total <= MAX_LEN:
            errs.append(f"output is {total:.2f}s (need {MIN_LEN:.0f}-{MAX_LEN:.0f}s)")
        tz_end = segs[0].out_end
        for p in spec.get("punchlines", []):
            iv = map_interval(segs, p["start"], p["end"])
            if not iv:
                errs.append(f"punchline {p['text']!r} never appears on the output timeline")
            elif any(a < tz_end for a, _ in iv):
                errs.append(f"punchline {p['text']!r} overlaps the teaser")
    return errs


# ---------------------------------------------------------------- ffmpeg

def probe(path: str) -> tuple[tuple[int, int], float, bool]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration",
         "-of", "json", path], capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    v = next(s for s in j["streams"] if s["codec_type"] == "video")
    has_audio = any(s["codec_type"] == "audio" for s in j["streams"])
    return (int(v["width"]), int(v["height"])), float(j["format"]["duration"]), has_audio


def find_font() -> str:
    for f in FONT_CANDIDATES:
        if f and Path(f).exists():
            return f
    raise SpecError("no bold font found; set CLIPPY_FONT to a .ttf path")


def _atempo_chain(speed: float) -> str:
    parts, s = [], speed
    while s < 0.5:
        parts.append("atempo=0.5")
        s /= 0.5
    parts.append(f"atempo={s:.6f}")
    return ",".join(parts)


def _ff_text(tmp: Path, name: str, text: str) -> str:
    p = tmp / f"{name}.txt"
    p.write_text(text, encoding="utf-8")
    return str(p).replace("\\", "/")


def _wrap(text: str, width: int) -> str:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def build_filter(spec: dict, segs: list[Seg], has_audio: bool, font: str, tmp: Path) -> tuple[str, float]:
    fx, fy, fw, fh = spec["facecam"]
    gx, gy, gw, gh = spec["game"]
    total = segs[-1].out_end
    fontf = font.replace("\\", "/")
    lines = []

    # 1. Stack the source: facecam on top, gameplay below, both cropped to fill their panels.
    lines.append(
        f"[0:v]fps={FPS},split=2[vf][vg];"
        f"[vf]crop={fw}:{fh}:{fx}:{fy},scale={W}:{FACE_H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{FACE_H},setsar=1[face];"
        f"[vg]crop={gw}:{gh}:{gx}:{gy},scale={W}:{GAME_H}:force_original_aspect_ratio=increase,"
        f"crop={W}:{GAME_H},setsar=1[game];"
        f"[face][game]vstack=inputs=2,format=yuv420p[stk]"
    )
    n = len(segs)
    lines.append(f"[stk]split={n}" + "".join(f"[sv{i}]" for i in range(n)))
    if has_audio:
        lines.append(f"[0:a]aresample=48000,aformat=channel_layouts=stereo,asplit={n}" + "".join(f"[sa{i}]" for i in range(n)))

    # 2. Cut each timeline segment (with slow-mo / freeze).
    for i, g in enumerate(segs):
        if g.kind == "freeze":
            lines.append(
                f"[sv{i}]trim=start={g.src_start:.3f}:duration={1 / FPS:.4f},setpts=PTS-STARTPTS,"
                f"loop=loop={max(1, round(g.out_dur * FPS)) - 1}:size=1:start=0,setpts=N/{FPS}/TB,"
                f"trim=duration={g.out_dur:.3f}[v{i}]"
            )
            if has_audio:
                lines.append(f"[sa{i}]atrim=0:0.001,apad=whole_dur={g.out_dur:.3f},volume=0[a{i}]")
        else:
            lines.append(
                f"[sv{i}]trim=start={g.src_start:.3f}:end={g.src_end:.3f},"
                f"setpts=(PTS-STARTPTS)/{g.speed:.6f}[v{i}]"
            )
            if has_audio:
                tempo = "" if abs(g.speed - 1) < 1e-6 else "," + _atempo_chain(g.speed)
                lines.append(f"[sa{i}]atrim=start={g.src_start:.3f}:end={g.src_end:.3f},asetpts=PTS-STARTPTS{tempo}[a{i}]")
    if not has_audio:
        for i, g in enumerate(segs):
            lines.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={g.out_dur:.3f}[a{i}]")
    lines.append("".join(f"[v{i}][a{i}]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]")

    # 3. White flash at the teaser -> setup cut.
    cut = segs[0].out_end
    lines.append(
        f"color=c=white:s={W}x{H}:r={FPS}:d={total:.3f},format=rgba,"
        f"colorchannelmixer=aa=0.9[white];"
        f"[cv][white]overlay=enable='between(t,{cut:.3f},{cut + FLASH_S:.3f})'[fl]"
    )

    # 4. Text: hook (teaser only), punchlines and subtitles (after flash), credit (always).
    text_base = f"fontfile='{fontf}':fontcolor=white:borderw=6:bordercolor=black:x=(w-text_w)/2:line_spacing=10"
    draws = []
    hook = _ff_text(tmp, "hook", _wrap(spec["teaser_text"].upper(), 20))
    draws.append(f"drawtext={text_base}:textfile='{hook}':fontsize=78:y={FACE_H + 60}:enable='between(t,0,{cut:.3f})'")
    for k, p in enumerate(spec.get("punchlines", [])):
        tf = _ff_text(tmp, f"punch{k}", _wrap(p["text"].upper(), 18))
        for a, b in map_interval(segs, p["start"], p["end"]):
            draws.append(f"drawtext={text_base}:textfile='{tf}':fontsize=84:y={FACE_H + 120}:enable='between(t,{a:.3f},{b:.3f})'")
    if spec.get("subtitles"):
        for k, c in enumerate(parse_srt(spec["subtitles"])):
            tf = _ff_text(tmp, f"sub{k}", _wrap(c["text"], 28))
            for a, b in map_interval(segs, c["start"], c["end"]):
                draws.append(f"drawtext={text_base}:textfile='{tf}':fontsize=58:y=h-th-260:enable='between(t,{a:.3f},{b:.3f})'")
    credit = _ff_text(tmp, "credit", spec["credit"])
    draws.append(f"drawtext=fontfile='{fontf}':fontcolor=white@0.85:borderw=3:bordercolor=black:"
                 f"textfile='{credit}':fontsize=36:x=(w-text_w)/2:y=h-th-90")
    lines.append("[fl]" + ",".join(draws) + "[vout]")

    # 5. Audio: whoosh on the cut, pre-gain, loudness normalize.
    vol = float(spec.get("volume", 1.0))
    lines.append(
        f"anoisesrc=d=0.35:c=pink:r=48000:a=0.6,highpass=f=600,lowpass=f=6000,"
        f"afade=t=in:d=0.2,afade=t=out:st=0.2:d=0.15,aformat=channel_layouts=stereo,"
        f"adelay={int(max(0, cut - 0.2) * 1000)}|{int(max(0, cut - 0.2) * 1000)}[wh];"
        f"[ca]volume={vol}[cav];[cav][wh]amix=inputs=2:duration=first:normalize=0,"
        f"loudnorm=I={LOUDNESS_I}:TP=-1.5:LRA=11,aresample=48000[aout]"
    )
    return ";\n".join(lines), total


def render(spec: dict, spec_dir: Path, preview: bool = False, dry_run: bool = False) -> dict:
    spec = dict(spec)
    for k in ("src", "out", "subtitles"):
        if spec.get(k) and not Path(spec[k]).is_absolute():
            spec[k] = str((spec_dir / spec[k]).resolve())
    if not Path(spec["src"]).exists():
        raise SpecError(f"source not found: {spec['src']}")
    wh, dur, has_audio = probe(spec["src"])
    errs = validate(spec, wh, dur)
    if errs:
        raise SpecError("; ".join(errs))
    segs = build_timeline(spec)
    font = find_font()
    out = Path(spec["out"])
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="clippy_") as td:
        tmp = Path(td)
        graph, total = build_filter(spec, segs, has_audio, font, tmp)
        (tmp / "graph.txt").write_text(graph)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", spec["src"],
               "-filter_complex_script", str(tmp / "graph.txt"),
               "-map", "[vout]", "-map", "[aout]",
               "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(FPS),
               "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-movflags", "+faststart",
               "-t", f"{total:.3f}", str(out)]
        if dry_run:
            return {"dry_run": True, "duration_s": round(total, 3), "segments": [s.__dict__ for s in segs],
                    "cmd": cmd, "graph": graph}
        subprocess.run(cmd, check=True, timeout=600)
    result = {
        "out": str(out),
        "duration_s": round(probe(str(out))[1], 3),
        "planned_duration_s": round(total, 3),
        "sha256": _sha256(out),
        "teaser_end_s": round(segs[0].out_end, 3),
        "segments": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in s.__dict__.items()} for s in segs],
    }
    if preview:
        result["preview"] = make_preview(out, segs, spec)
    return result


def make_preview(out: Path, segs: list[Seg], spec: dict) -> str:
    """QA strip: hook frame, just after the flash, the payoff, and the last frame."""
    total = segs[-1].out_end
    eff = spec.get("effect")
    if eff:
        key = eff["at"] if eff["type"] == "freeze" else (eff["start"] + eff["end"]) / 2
        iv = map_interval(segs, key, key + 0.05)
        payoff = iv[0][0] if iv else total * 0.78
    else:
        payoff = total * 0.78
    times = [0.5, segs[0].out_end + FLASH_S + 0.1, payoff, max(0, total - 0.1)]
    png = out.with_suffix(".qa.png")
    sel = "+".join(f"eq(n,{int(t * FPS)})" for t in times)
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(out),
                    "-vf", f"select='{sel}',scale=360:-1,tile=4x1:padding=8:color=black",
                    "-frames:v", "1", "-vsync", "0", str(png)], check=True, timeout=120)
    return str(png)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Render a Clippy Club clip from a JSON spec.")
    ap.add_argument("spec")
    ap.add_argument("--preview", action="store_true", help="also write a 4-frame QA strip (.qa.png)")
    ap.add_argument("--dry-run", action="store_true", help="validate and print the plan without rendering")
    a = ap.parse_args(argv)
    if not shutil.which("ffmpeg"):
        print("ffmpeg not found", file=sys.stderr)
        return 2
    spec_path = Path(a.spec)
    try:
        res = render(json.loads(spec_path.read_text()), spec_path.parent, a.preview, a.dry_run)
    except SpecError as e:
        print(f"SPEC ERROR: {e}", file=sys.stderr)
        return 1
    if a.dry_run:
        res = {k: v for k, v in res.items() if k != "graph"}
    print(json.dumps(res, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
