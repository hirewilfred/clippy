"""thumb.py: make YouTube thumbnails for a clip.

Two outputs from one frame:
  <name>.thumb.jpg        1280x720  (YouTube's standard size; shown on the channel page and in search)
  <name>.thumb-916.jpg    1080x1920 (vertical; for apps that take a Shorts cover)

The frame is picked automatically from the payoff window (60-90% of the clip) using
ffmpeg's `thumbnail` filter, which picks the most representative frame, or set it
yourself with --at. The hook text is drawn big, upper case, with a heavy outline.
YouTube's limit is 2 MB, so JPEG quality steps down until the file fits.

Usage:
    python -m scripts.thumb clip.mp4 --text "GTA 6 COPS DON'T DO ROLEPLAY" [--at 12.4]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from scripts.clip import EMOJI_RE, find_font, probe

MAX_BYTES = 2 * 1024 * 1024
LAND = (1280, 720)
VERT = (1080, 1920)


def wrap(text: str, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    return lines


def check_text(text: str) -> list[str]:
    warns = []
    if EMOJI_RE.search(text):
        warns.append("emoji removed: thumbnails can't render them")
    if len(text.split()) > 6:
        warns.append("over 6 words: thumbnail text reads best at 2-5 words")
    return warns


def pick_frame(src: Path, out_png: Path, at: float | None, duration: float) -> float:
    if at is None:
        start = duration * 0.6
        window = max(0.5, duration * 0.3)
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.3f}", "-t", f"{window:.3f}",
               "-i", str(src), "-vf", "thumbnail=60", "-frames:v", "1", str(out_png)]
        chosen = start
    else:
        if not 0 <= at <= duration:
            raise ValueError(f"--at {at} is outside the clip (0-{duration:.2f}s)")
        cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{at:.3f}", "-i", str(src),
               "-frames:v", "1", str(out_png)]
        chosen = at
    subprocess.run(cmd, check=True, timeout=120)
    return chosen


def _text_filters(lines: list[str], font: str, size: int, x0: int, box_w: int, center_y: int, tmp: Path, tag: str) -> str:
    fontf = font.replace("\\", "/")
    step = int(size * 1.08)
    top = center_y - (len(lines) * step) // 2
    parts = []
    for i, ln in enumerate(lines):
        tf = tmp / f"{tag}{i}.txt"
        tf.write_text(ln, encoding="utf-8")
        color = "yellow" if i == len(lines) - 1 and len(lines) > 1 else "white"
        parts.append(
            f"drawtext=fontfile='{fontf}':textfile='{tf.as_posix()}':fontsize={size}:fontcolor={color}:"
            f"borderw={max(6, size // 9)}:bordercolor=black:x={x0}+({box_w}-text_w)/2:y={top + i * step}"
        )
    return ",".join(parts)


def render_thumbs(frame: Path, text: str, out_base: Path, tmp: Path) -> dict:
    font = find_font()
    clean = EMOJI_RE.sub("", text).upper().strip()
    out = {}

    # Landscape: blurred fill + sharp frame on the left, text on the right.
    lw, lh = LAND
    lines = wrap(clean, 11)
    size = 104 if len(lines) <= 3 else 84
    land_vf = (
        f"[0:v]split=2[a][b];"
        f"[a]scale={lw}:{lh}:force_original_aspect_ratio=increase,crop={lw}:{lh},boxblur=24:2,eq=brightness=-0.15[bg];"
        f"[b]scale=-2:{lh}[fg];"
        f"[bg][fg]overlay=x=40:y=0[base];"
        f"[base]{_text_filters(lines, font, size, 440, lw - 460, lh // 2, tmp, 'l')}[o]"
    )
    out["landscape"] = _encode(frame, land_vf, out_base.with_suffix(".thumb.jpg"))

    # Vertical: frame fills, text in the middle band.
    vw, vh = VERT
    lines = wrap(clean, 12)
    vert_vf = (
        f"[0:v]scale={vw}:{vh}:force_original_aspect_ratio=increase,crop={vw}:{vh}[base];"
        f"[base]{_text_filters(lines, font, 120, 0, vw, int(vh * 0.55), tmp, 'v')}[o]"
    )
    out["vertical"] = _encode(frame, vert_vf, out_base.with_suffix(".thumb-916.jpg"))
    return out


def _encode(frame: Path, graph: str, dest: Path) -> str:
    for q in (2, 4, 6, 9, 12):
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(frame),
                        "-filter_complex", graph, "-map", "[o]", "-frames:v", "1", "-q:v", str(q), str(dest)],
                       check=True, timeout=120)
        if dest.stat().st_size <= MAX_BYTES:
            return str(dest)
    raise RuntimeError(f"could not get {dest.name} under 2 MB")


def make(src: str | Path, text: str, at: float | None = None, out_dir: str | Path | None = None) -> dict:
    src = Path(src)
    _, duration, _ = probe(str(src))
    out_dir = Path(out_dir) if out_dir else src.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="thumb_") as td:
        tmp = Path(td)
        frame = tmp / "frame.png"
        chosen = pick_frame(src, frame, at, duration)
        paths = render_thumbs(frame, text, out_dir / src.stem, tmp)
    return {"frame_from_s": round(chosen, 2), "warnings": check_text(text), **paths}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Make YouTube thumbnails for a clip.")
    ap.add_argument("clip")
    ap.add_argument("--text", required=True, help="2-5 words; the hook")
    ap.add_argument("--at", type=float, help="seconds into the clip to take the frame from")
    ap.add_argument("--out-dir")
    a = ap.parse_args(argv)
    print(json.dumps(make(a.clip, a.text, a.at, a.out_dir), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
