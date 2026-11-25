"""moments.py: rank the best cut windows inside campaign footage (no outside sources).

For a footage-only campaign the "best video" is the best 12-20 s window in the supplied
stream. We score windows on two signals:
  * reaction: loudness spikes (Peanut and chat getting hyped), from ffmpeg's RMS level;
  * Pop: transcript lines that mention the Pop (needs an .srt/.vtt next to the video or --subs).
A window with no Pop mention is still listed but marked `needs_pop_check`: someone has to
confirm on the contact sheet that the Pop is visible, or it can't be cut ("No Pop = no pay").

The payoff (loudest moment) is placed at about 75% of the window, as the playbook asks.

Usage:
    python -m scripts.moments <video> [--subs file.srt] [--top 8] [--len 16]
Writes work/moments/<stem>.json (gitignored) and prints the ranking.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
from pathlib import Path

from scripts.campaign import load_campaign, DEFAULT_CAMPAIGN

ROOT = Path(__file__).resolve().parent.parent
STEP_S = 0.5
PAYOFF_AT = 0.75
TS_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d+):(\d{2}):(\d{2})[.,](\d{3})")
SHORT_TS_RE = re.compile(r"(\d{2}):(\d{2})[.,](\d{3})\s*-->\s*(\d{2}):(\d{2})[.,](\d{3})")


def rms_series(video: Path) -> list[float]:
    """RMS level in dB for each STEP_S window of audio."""
    n = int(8000 * STEP_S)
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(video), "-vn", "-af",
           f"aresample=8000,asetnsamples=n={n}:p=0,astats=metadata=1:reset=1,"
           "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=1800).stdout
    vals = []
    for line in out.splitlines():
        if "RMS_level=" in line:
            v = line.split("=", 1)[1].strip()
            vals.append(-90.0 if v in ("-inf", "inf", "nan") else max(float(v), -90.0))
    return vals


def parse_subs(text: str) -> list[tuple[float, float, str]]:
    cues, cur = [], None
    for line in text.splitlines():
        m = TS_RE.search(line)
        if m:
            g = [int(x) for x in m.groups()]
            cur = [g[0] * 3600 + g[1] * 60 + g[2] + g[3] / 1000, g[4] * 3600 + g[5] * 60 + g[6] + g[7] / 1000, ""]
            cues.append(cur)
            continue
        m = SHORT_TS_RE.search(line)
        if m:
            g = [int(x) for x in m.groups()]
            cur = [g[0] * 60 + g[1] + g[2] / 1000, g[3] * 60 + g[4] + g[5] / 1000, ""]
            cues.append(cur)
            continue
        if cur is not None and line.strip() and not line.strip().isdigit():
            cur[2] = (cur[2] + " " + re.sub(r"<[^>]+>", "", line.strip())).strip()
    return [tuple(c) for c in cues]


def pop_hits(cues: list[tuple[float, float, str]], keywords: list[str]) -> list[tuple[float, str]]:
    rx = re.compile(r"\b(" + "|".join(re.escape(k) for k in keywords) + r")\b", re.IGNORECASE)
    return [(s, t) for s, _, t in cues if rx.search(t)]


def rank(rms: list[float], hits: list[tuple[float, str]], length: float = 16.0, top: int = 8) -> list[dict]:
    """Pick non-overlapping windows around the strongest loudness peaks, boosted by Pop mentions."""
    if not rms:
        return []
    med = statistics.median(rms)
    spread = statistics.pstdev(rms) or 1.0
    dur = len(rms) * STEP_S
    scored = []
    for i, v in enumerate(rms):
        peak_t = i * STEP_S
        start = max(0.0, min(peak_t - PAYOFF_AT * length, max(0.0, dur - length)))
        end = min(dur, start + length)
        react = (v - med) / spread
        pops = [h for h in hits if start <= h[0] <= end]
        score = react + 1.5 * min(len(pops), 3)
        scored.append({"start_s": round(start, 1), "end_s": round(end, 1), "payoff_s": round(peak_t, 1),
                       "reaction_z": round(react, 2), "pop_mentions": [t for _, t in pops][:3],
                       "needs_pop_check": not pops, "score": round(score, 2)})
    scored.sort(key=lambda w: -w["score"])
    picked: list[dict] = []
    for w in scored:
        if all(w["end_s"] <= p["start_s"] or w["start_s"] >= p["end_s"] for p in picked):
            picked.append(w)
        if len(picked) >= top:
            break
    return picked


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Rank cut windows in campaign footage.")
    ap.add_argument("video")
    ap.add_argument("--subs", help=".srt/.vtt transcript (default: same name next to the video)")
    ap.add_argument("--len", type=float, default=16.0)
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    a = ap.parse_args(argv)
    video = Path(a.video)
    cfg = load_campaign(a.campaign)
    subs = Path(a.subs) if a.subs else next((p for p in (video.with_suffix(".srt"), video.with_suffix(".vtt")) if p.exists()), None)
    hits = pop_hits(parse_subs(subs.read_text(errors="ignore")), cfg["pop_keywords"]) if subs else []
    if not subs:
        print("no transcript found: every window will need a Pop check on the contact sheet", file=sys.stderr)
    wins = rank(rms_series(video), hits, a.len, a.top)
    out = ROOT / "work" / "moments" / f"{video.stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"video": video.name, "windows": wins}, indent=2))
    for w in wins:
        flag = "POP?" if w["needs_pop_check"] else "POP "
        print(f"{w['score']:>6}  {flag}  {w['start_s']:>7.1f}-{w['end_s']:<7.1f} payoff {w['payoff_s']:.1f}s  "
              f"{'; '.join(w['pop_mentions'])[:70]}")
    print(f"-> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
