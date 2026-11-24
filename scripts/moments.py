"""moments.py: find clip-worthy moments in a long recording and draft clip.py specs for them.

Input is usually a VOD section from scripts/vod.py. Output is a ranked candidate list,
one draft spec per candidate, and (with --cut) a short source file plus a contact sheet
for each, so the VERIFY step in playbook.md can be done by eye.

Detectors:
  loudness  (default, ffmpeg only) Reaction spikes: momentary loudness (EBU R128, 100 ms
            steps) well above the recording's median. Screams, laughs and crashes are
            what GTA streamer clips are made of.
  clipsai   ClipsAI (github.com/ClipsAI/clipsai): WhisperX transcript + topic segmentation.
            Built for podcasts and interviews, so on its own it finds talk segments, not
            payoffs. Optional, heavy (torch): pip install -r requirements-clipsai.txt
  both      Loudness peaks, ranked higher when they sit inside a ClipsAI segment.

Nothing here decides a clip is good. Draft specs deliberately leave out the facecam and
game crop boxes and the hook text, so clip.py refuses them until someone has looked.

Usage:
    python -m scripts.moments work/vods/<file>.mp4 [--detector loudness|clipsai|both]
                              [--top 8] [--cut]
Writes work/moments/<stem>.json, <stem>_mN.spec.json and, with --cut, <stem>_mN.mp4/.jpg.
"""
from __future__ import annotations

import argparse
import json
import re
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "work" / "moments"

DELTA_DB = 8.0        # a peak must be this far above the recording's median loudness
MIN_SEP_S = 20.0      # peaks closer than this are one moment
PRE_S, POST_S = 12.0, 3.0   # setup before the peak, reaction hold after it
TEASER_PRE, TEASER_POST = 0.5, 2.5
CUT_PAD_S = 3.0       # extra source either side of a cut, so the editor has room
SILENCE_LUFS = -70.0
CLIPSAI_BOOST_DB = 3.0

TODO = [
    "facecam: crop box [x, y, w, h] from the contact sheet (real facecam, visible reaction)",
    "game: crop box [x, y, w, h] for the gameplay panel",
    "teaser_text: context, not description; never 'STREAMER DOES X'; game keyword in frame one",
    "verify: GTA 6 proof frame, nothing banned, fewer than 3 pages already posted it, send test",
    "check teaser/keep cut points land between words and actions",
]


class MomentsError(RuntimeError):
    pass


# ---------------------------------------------------------------- loudness (pure parts)

def parse_ebur128(text: str) -> list[tuple[float, float]]:
    """Parse ffmpeg `ametadata=mode=print` output into [(t_seconds, momentary_lufs)]."""
    out, t = [], None
    for line in text.splitlines():
        if m := re.search(r"pts_time:([0-9.]+)", line):
            t = float(m.group(1))
        elif line.startswith("lavfi.r128.M=") and t is not None:
            out.append((t, float(line.split("=", 1)[1])))
            t = None
    return out


def find_peaks(series: list[tuple[float, float]], duration: float, top: int = 8,
               delta_db: float = DELTA_DB, min_sep: float = MIN_SEP_S,
               pre: float = PRE_S, post: float = POST_S) -> list[dict]:
    voiced = [v for _, v in series if v > SILENCE_LUFS]
    if not voiced:
        return []
    base = statistics.median(voiced)
    thresh = base + delta_db
    # local maxima within +-2 s that clear the threshold
    cands = []
    for i, (t, v) in enumerate(series):
        if v < thresh:
            continue
        lo, hi = max(0, i - 20), min(len(series), i + 21)
        if v >= max(x for _, x in series[lo:hi]):
            cands.append((t, v))
    picked: list[tuple[float, float]] = []
    for t, v in sorted(cands, key=lambda c: -c[1]):
        if all(abs(t - p) >= min_sep for p, _ in picked):
            picked.append((t, v))
        if len(picked) == top:
            break
    return [{
        "peak_s": round(t, 2),
        "start_s": round(max(0.0, t - pre), 2),
        "end_s": round(min(duration, t + post), 2),
        "score_db": round(v - base, 1),
        "reasons": [f"reaction spike +{v - base:.1f} dB over median at {_mmss(t)}"],
    } for t, v in picked]


def _mmss(s: float) -> str:
    return f"{int(s) // 60:02d}:{int(s) % 60:02d}"


def loudness_series(src: Path) -> list[tuple[float, float]]:
    with tempfile.TemporaryDirectory(prefix="clippy_m_") as td:
        meta = Path(td) / "m.txt"
        subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-loglevel", "error", "-i", str(src), "-vn",
                        "-af", f"ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file={meta}",
                        "-f", "null", "-"], check=True, timeout=1800)
        return parse_ebur128(meta.read_text())


# ---------------------------------------------------------------- ClipsAI adapter

def clipsai_segments(src: Path) -> list[tuple[float, float]]:
    try:
        from clipsai import ClipFinder, Transcriber
    except ImportError:
        raise MomentsError("ClipsAI isn't installed. pip install -r requirements-clipsai.txt "
                           "(large: pulls torch and WhisperX), or use --detector loudness") from None
    transcription = Transcriber().transcribe(audio_file_path=str(Path(src).resolve()))
    return [(float(c.start_time), float(c.end_time)) for c in ClipFinder().find_clips(transcription=transcription)]


def merge_clipsai(peaks: list[dict], segments: list[tuple[float, float]]) -> list[dict]:
    for p in peaks:
        hit = next((s for s in segments if s[0] <= p["peak_s"] <= s[1]), None)
        if hit:
            p["score_db"] = round(p["score_db"] + CLIPSAI_BOOST_DB, 1)
            p["reasons"].append(f"inside ClipsAI segment {_mmss(hit[0])}-{_mmss(hit[1])}")
    return sorted(peaks, key=lambda p: -p["score_db"])


def from_segments(segments: list[tuple[float, float]], top: int) -> list[dict]:
    out = []
    for a, b in segments[:top]:
        start = max(a, b - (PRE_S + POST_S))
        out.append({"peak_s": round(b - POST_S, 2), "start_s": round(start, 2), "end_s": round(b, 2),
                    "score_db": None,
                    "reasons": [f"ClipsAI segment {_mmss(a)}-{_mmss(b)} (topic boundary, no reaction signal; "
                                "peak guessed at the end)"]})
    return out


# ---------------------------------------------------------------- draft specs

def draft_spec(c: dict, src_name: str, offset: float, out_name: str, info: dict | None) -> dict:
    """A clip.py spec with times relative to `src_name` (which starts `offset` s into the recording).

    facecam, game and teaser_text are left out on purpose: clip.py reports them missing
    until someone has checked the contact sheet.
    """
    p = c["peak_s"] - offset
    spec = {
        "src": src_name,
        "out": out_name,
        "layout": "stack",
        "teaser": {"start": round(max(0.0, p - TEASER_PRE), 2), "end": round(p + TEASER_POST, 2)},
        "keep": [[round(c["start_s"] - offset, 2), round(c["end_s"] - offset, 2)]],
        "_todo": TODO,
        "_why": c["reasons"],
    }
    if info and info.get("streamer"):
        site = "YouTube" if info.get("site") == "youtube" else "Twitch"
        spec["credit"] = f"@{info['streamer']} ({site})"
        rec_start = (info.get("section") or [0])[0]
        spec["_source"] = {"url": info.get("source_url"),
                           "vod_time": _hms(rec_start + c["peak_s"]),
                           "streamer": info["streamer"]}
    else:
        spec["_todo"] = TODO + ["credit: '@handle (Twitch)' (unknown streamer; required)"]
    return spec


def _hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def cut_cmd(src: Path, start: float, end: float, out: Path) -> list[str]:
    return ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
            "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
            str(out)]


# ---------------------------------------------------------------- run

def detect(src: Path, detector: str = "loudness", top: int = 8,
           series_fn=loudness_series, segments_fn=clipsai_segments) -> list[dict]:
    from scripts.clip import probe
    duration = probe(str(src))[1]
    if detector == "clipsai":
        return from_segments(segments_fn(src), top)
    peaks = find_peaks(series_fn(src), duration, top=top)
    if detector == "both":
        peaks = merge_clipsai(peaks, segments_fn(src))
    elif detector != "loudness":
        raise MomentsError(f"unknown detector {detector!r}")
    return peaks


def run(src: Path, detector: str = "loudness", top: int = 8, cut: bool = False, out_dir: Path = OUT_DIR,
        **kw) -> dict:
    src = Path(src)
    if not src.exists():
        raise MomentsError(f"not found: {src}")
    side = src.with_suffix(".json")
    info = json.loads(side.read_text()) if side.exists() else None
    cands = detect(src, detector, top, **kw)
    out_dir.mkdir(parents=True, exist_ok=True)
    from scripts.clip import probe
    duration = probe(str(src))[1]
    for n, c in enumerate(cands, 1):
        stem = f"{src.stem}_m{n}"
        if cut:
            a, b = max(0.0, c["start_s"] - CUT_PAD_S), min(duration, c["end_s"] + CUT_PAD_S)
            piece = out_dir / f"{stem}.mp4"
            subprocess.run(cut_cmd(src, a, b, piece), check=True, timeout=600)
            from scripts.sheet import make_sheet
            c["sheet"] = str(make_sheet(piece, every=1.0, dest_dir=out_dir))
            spec = draft_spec(c, piece.name, a, f"{stem}.render.mp4", info)
        else:
            spec = draft_spec(c, str(src.resolve()), 0.0, f"{stem}.render.mp4", info)
        spec_path = out_dir / f"{stem}.spec.json"
        spec_path.write_text(json.dumps(spec, indent=2))
        c["spec"] = str(spec_path)
    report = {"src": str(src), "detector": detector, "candidates": cands}
    (out_dir / f"{src.stem}.json").write_text(json.dumps(report, indent=2))
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Find clip-worthy moments in a recording.")
    ap.add_argument("src")
    ap.add_argument("--detector", default="loudness", choices=("loudness", "clipsai", "both"))
    ap.add_argument("--top", type=int, default=8)
    ap.add_argument("--cut", action="store_true", help="cut each candidate and build a contact sheet")
    a = ap.parse_args(argv)
    try:
        rep = run(Path(a.src), a.detector, a.top, a.cut)
    except MomentsError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    for n, c in enumerate(rep["candidates"], 1):
        score = "  n/a" if c["score_db"] is None else f"{c['score_db']:>5}"
        print(f"m{n}  {score} dB  {_mmss(c['start_s'])}-{_mmss(c['end_s'])}  {'; '.join(c['reasons'])}")
    print(f"{len(rep['candidates'])} candidates -> {OUT_DIR}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
