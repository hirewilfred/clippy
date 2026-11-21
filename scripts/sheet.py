"""sheet.py: download a clip and build a contact sheet (one frame every 2 s, timestamped).

The agent (or a human) looks at the sheet to check, without trusting the title:
real facecam with a reaction, really GTA 6 (new HUD, Lucia/Jason, Vice City),
where the facecam sits for the crop box, and where the payoff lands.

Usage:
    python -m scripts.sheet <clip_url> [--every 2] [--cols 5]
Writes work/downloads/<id>.mp4 and work/sheets/<id>.jpg (both gitignored).
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"


def clip_id(url: str) -> str:
    m = re.search(r"/clip/([A-Za-z0-9_-]+)", url) or re.search(r"clips\.twitch\.tv/([A-Za-z0-9_-]+)", url)
    if not m:
        raise ValueError(f"not a Twitch clip URL: {url}")
    return m.group(1)


def download(url: str, dest_dir: Path = WORK / "downloads") -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    cid = clip_id(url)
    out = dest_dir / f"{cid}.mp4"
    if not out.exists():
        subprocess.run(["yt-dlp", "-q", "--no-warnings", "-f", "best[height<=1080][ext=mp4]/best[height<=1080]",
                        "-o", str(out), url], check=True, timeout=300)
    return out


def sheet_cmd(src: Path, out: Path, every: float, cols: int, duration: float) -> list[str]:
    n = max(1, int(duration // every) + 1)
    rows = -(-n // cols)
    stamp = ""
    if Path(FONT).exists():
        stamp = (f",drawtext=fontfile='{FONT}':text='%{{pts\\:hms}}':x=8:y=8:fontsize=22:"
                 f"fontcolor=white:box=1:boxcolor=black@0.6")
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
            "-vf", f"fps=1/{every},scale=384:-2{stamp},tile={cols}x{rows}:padding=4:color=black",
            "-frames:v", "1", "-q:v", "3", str(out)]


def make_sheet(src: Path, every: float = 2.0, cols: int = 5, dest_dir: Path = WORK / "sheets") -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dur = float(json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(src)],
        capture_output=True, text=True, check=True).stdout)["format"]["duration"])
    out = dest_dir / f"{src.stem}.jpg"
    subprocess.run(sheet_cmd(src, out, every, cols, dur), check=True, timeout=300)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Download a clip and build its contact sheet.")
    ap.add_argument("url")
    ap.add_argument("--every", type=float, default=2.0)
    ap.add_argument("--cols", type=int, default=5)
    a = ap.parse_args(argv)
    src = download(a.url)
    sheet = make_sheet(src, a.every, a.cols)
    print(json.dumps({"source": str(src), "sheet": str(sheet)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
