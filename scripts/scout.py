"""scout.py: find fresh GTA 6 clips from a watchlist of streamers, using public Twitch pages.

No API key: yt-dlp reads the same public clip pages a browser does. It works per
streamer, not per category, so the watchlist in config/watchlist.json sets the scope.

Usage:
    python -m scripts.scout [--streamers a,b] [--out work/candidates.json]

Titles are never trusted to *pass* a clip: they can only reject one. Passing
candidates still need a contact-sheet check (facecam, really GTA 6, actually funny).
Requests are paced and capped; this is a light, human-scale read of public pages.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WATCHLIST = ROOT / "config" / "watchlist.json"
PACE_S = 1.0

# Title words that reject a clip outright (sexual content, spoilers, leaks, drama bait).
REJECT_TITLE = re.compile(
    r"\b(strip|stripper|nsfw|sexy|thirst|boob|naked|nude|onlyfans|horny|leak|spoiler|ending|datamine)\w*",
    re.IGNORECASE,
)


def ytdlp_json(args: list[str], timeout: int = 120) -> dict | None:
    r = subprocess.run(["yt-dlp", "--no-warnings", "-J", *args], capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        return None


def list_channel_clips(handle: str, limit: int) -> list[str]:
    url = f"https://www.twitch.tv/{handle}/clips?filter=clips&range=24hr"
    j = ytdlp_json(["--flat-playlist", "--playlist-end", str(limit), url])
    return [e["url"] for e in (j or {}).get("entries") or [] if e.get("url")]


def clip_meta(url: str) -> dict | None:
    j = ytdlp_json(["--skip-download", url])
    if not j:
        return None
    return {
        "source_url": j.get("webpage_url") or url,
        "clip_id": j.get("display_id") or j.get("id"),
        "title": j.get("title") or "",
        "streamer": (j.get("creator") or "").lower() or None,
        "streamer_display": j.get("creator"),
        "clipped_by": j.get("uploader"),
        "created_at": datetime.fromtimestamp(j["timestamp"], timezone.utc).isoformat() if j.get("timestamp") else None,
        "duration_s": j.get("duration"),
        "views": j.get("view_count"),
        "category": (j.get("categories") or [None])[0],
        "max_height": j.get("height"),
    }


def evaluate(m: dict, cfg: dict, now: datetime) -> list[str]:
    """Return reject reasons; empty list = candidate worth a contact sheet."""
    why = []
    if m.get("category") != cfg["game_category"]:
        why.append(f"category is {m.get('category')!r}")
    created = datetime.fromisoformat(m["created_at"]) if m.get("created_at") else None
    if not created:
        why.append("no creation time")
    else:
        if now - created > timedelta(hours=cfg["max_age_hours"]):
            why.append(f"older than {cfg['max_age_hours']}h")
        if created < datetime.fromisoformat(cfg["launch_utc"].replace("Z", "+00:00")):
            why.append("created before launch (likely not GTA 6)")
    d = m.get("duration_s") or 0
    if d < cfg["min_source_seconds"]:
        why.append(f"too short ({d}s)")
    if d > cfg["max_source_seconds"]:
        why.append(f"too long ({d}s)")
    if (m.get("views") or 0) < cfg["min_views"]:
        why.append(f"under {cfg['min_views']} views")
    if REJECT_TITLE.search(m.get("title") or ""):
        why.append("title suggests sexual/spoiler/leak content")
    return why


def scout(streamers: list[str], cfg: dict, now: datetime | None = None, log=print) -> dict:
    now = now or datetime.now(timezone.utc)
    passed, rejected = [], []
    for handle in streamers:
        urls = list_channel_clips(handle, cfg["clips_per_channel"])
        log(f"{handle}: {len(urls)} clips in last 24h")
        time.sleep(PACE_S)
        for url in urls:
            m = clip_meta(url)
            time.sleep(PACE_S)
            if not m:
                rejected.append({"source_url": url, "reasons": ["metadata unavailable"]})
                continue
            why = evaluate(m, cfg, now)
            (rejected if why else passed).append({**m, "reasons": why} if why else m)
    passed.sort(key=lambda m: m.get("views") or 0, reverse=True)
    return {"scouted_at": now.isoformat(), "passed": passed, "rejected": rejected}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Scout fresh GTA 6 clips from public Twitch pages.")
    ap.add_argument("--streamers", help="comma-separated handles (default: config/watchlist.json)")
    ap.add_argument("--out", default=str(ROOT / "work" / "candidates.json"))
    a = ap.parse_args(argv)
    cfg = json.loads(WATCHLIST.read_text())
    streamers = [s.strip().lower() for s in a.streamers.split(",")] if a.streamers else cfg["streamers"]
    res = scout(streamers, cfg, log=lambda s: print(s, file=sys.stderr))
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2))
    print(f"{len(res['passed'])} passed, {len(res['rejected'])} rejected -> {out}", file=sys.stderr)
    for m in res["passed"]:
        print(f"{m['views']:>8} views  {m['duration_s']:>3}s  {m['streamer']:<14} {m['title'][:60]}  {m['source_url']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
