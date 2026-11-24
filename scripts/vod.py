"""vod.py: pull one section of a finished stream (Twitch VOD or YouTube stream) with yt-dlp.

scout.py finds moments viewers already clipped. This finds the ones nobody clipped:
download a short window of a VOD, then run scripts/moments.py on it.

Best use: VODs of retainer streamers (they asked us to clip them). For anyone else,
the publisher gates still apply to whatever comes out of it.

Usage:
    python -m scripts.vod <vod_url> --section 01:10:00-01:25:00 [--dry-run]
Writes work/vods/<id>_<start>-<end>.mp4 plus a .json sidecar (gitignored).

Limits, on purpose:
  - finished VODs only: no live recording, no channel or clip URLs (use sheet.py for clips);
  - a section is required and is at most 30 minutes (disk, and a light touch on the host);
  - streamers marked `blocked` in config/permitted_streamers.json are refused before download.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PERMITTED = ROOT / "config" / "permitted_streamers.json"
OUT_DIR = ROOT / "work" / "vods"
MAX_SECTION_S = 30 * 60
FORMAT = "best[height<=1080][ext=mp4]/best[height<=1080]"

TWITCH_VOD = re.compile(r"^https?://(?:www\.|m\.)?twitch\.tv/videos/(\d+)")
YOUTUBE = re.compile(r"^https?://(?:www\.|m\.)?(?:youtube\.com/(?:watch\?(?:.*&)?v=|live/)|youtu\.be/)([A-Za-z0-9_-]{11})")


class VodError(ValueError):
    pass


def parse_url(url: str) -> tuple[str, str]:
    """Return (site, video_id). Refuses clips, channels and live pages."""
    if m := TWITCH_VOD.match(url):
        return "twitch", m.group(1)
    if m := YOUTUBE.match(url):
        return "youtube", m.group(1)
    if "clips.twitch.tv" in url or "/clip/" in url:
        raise VodError("that's a clip URL: use scripts.sheet for clips")
    raise VodError("need a finished VOD URL: twitch.tv/videos/<id> or a YouTube video/stream URL")


def _secs(ts: str) -> int:
    parts = ts.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        raise VodError(f"bad timestamp {ts!r} (use HH:MM:SS, MM:SS or seconds)")
    s = 0
    for p in parts:
        s = s * 60 + int(p)
    return s


def parse_section(section: str) -> tuple[int, int]:
    if "-" not in section:
        raise VodError("section must look like 01:10:00-01:25:00")
    a, b = (_secs(x) for x in section.split("-", 1))
    if b <= a:
        raise VodError("section end must be after its start")
    if b - a > MAX_SECTION_S:
        raise VodError(f"section is {(b - a) // 60} min; max is {MAX_SECTION_S // 60} min")
    return a, b


def hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def streamer_errors(handle: str | None, permitted: dict) -> list[str]:
    if not handle:
        return ["couldn't tell whose stream this is; refusing (credit is required)"]
    for s in permitted.get("streamers", []):
        if (s.get("handle") or "").lower() == handle.lower() and s.get("permission") == "blocked":
            return [f"@{handle} is blocked"]
    return []


def download_cmd(url: str, start: int, end: int, out: Path) -> list[str]:
    return ["yt-dlp", "-q", "--no-warnings", "--no-playlist", "-f", FORMAT,
            "--download-sections", f"*{hms(start)}-{hms(end)}",
            "-o", str(out), url]


def meta(url: str) -> dict:
    r = subprocess.run(["yt-dlp", "--no-warnings", "--no-playlist", "-J", "--skip-download", url],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise VodError(f"yt-dlp couldn't read the VOD: {r.stderr.strip()[:200]}")
    j = json.loads(r.stdout)
    if j.get("is_live") or j.get("live_status") in ("is_live", "is_upcoming"):
        raise VodError("stream is live or upcoming; wait for the VOD")
    # Twitch: uploader_id is the login name. YouTube: uploader_id is "@handle".
    handle = (j.get("uploader_id") or j.get("uploader") or "").lstrip("@").lower() or None
    return {"title": j.get("title"), "streamer": handle, "streamer_display": j.get("uploader"),
            "duration_s": j.get("duration"), "extractor": j.get("extractor")}


def fetch(url: str, section: str, dry_run: bool = False, out_dir: Path = OUT_DIR,
          permitted: dict | None = None, meta_fn=meta, run=subprocess.run) -> dict:
    site, vid = parse_url(url)
    start, end = parse_section(section)
    m = meta_fn(url)
    if m.get("duration_s") and end > m["duration_s"] + 1:
        raise VodError(f"section ends at {hms(end)} but the VOD is {hms(m['duration_s'])}")
    errs = streamer_errors(m.get("streamer"), permitted if permitted is not None else json.loads(PERMITTED.read_text()))
    if errs:
        raise VodError("; ".join(errs))
    out = out_dir / f"{site}-{vid}_{start}-{end}.mp4"
    cmd = download_cmd(url, start, end, out)
    info = {"source_url": url, "site": site, "video_id": vid, "section": [start, end],
            "streamer": m.get("streamer"), "streamer_display": m.get("streamer_display"),
            "title": m.get("title"), "file": str(out)}
    if dry_run:
        return {**info, "dry_run": True, "cmd": cmd}
    out_dir.mkdir(parents=True, exist_ok=True)
    run(cmd, check=True, timeout=1800)
    out.with_suffix(".json").write_text(json.dumps(info, indent=2))
    return info


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Download one section of a finished VOD.")
    ap.add_argument("url")
    ap.add_argument("--section", required=True, help="start-end, e.g. 01:10:00-01:25:00 (max 30 min)")
    ap.add_argument("--dry-run", action="store_true", help="check and print the yt-dlp command only")
    a = ap.parse_args(argv)
    try:
        res = fetch(a.url, a.section, a.dry_run)
    except VodError as e:
        print(f"REFUSED: {e}", file=sys.stderr)
        return 1
    print(json.dumps(res, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
