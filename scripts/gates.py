"""Publisher gates for Clippy Club.

Every post, live or dry-run, has to pass these checks first. Each gate is a plain
function that returns a list of failure messages; an empty list means the post may go.
Nothing here does network or database I/O: callers pass in the records, so every
rule can be unit tested.

Rules come from CLAUDE.md, playbook.md and config/warmup.json.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
WARMUP_PATH = ROOT / "config" / "warmup.json"

PLATFORMS = ("tiktok", "youtube")
BANNED_WORDS = ("official", "leak", "early access", "free copy", "giveaway")
CREDIT_RE = re.compile(r"^Credit: @[A-Za-z0-9_]{2,25} \((Twitch|Kick|YouTube)\)$", re.MULTILINE)
HASHTAG_RE = re.compile(r"(?<!\w)#\w+")
EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\U00002600-\U000027BF\U0001F900-\U0001F9FF️]"
)
YT_DISCLAIMER = "Not affiliated with Rockstar Games"
YT_TITLE_MAX = 60
MIN_LEN_S, MAX_LEN_S = 12.0, 20.0


# ---------------------------------------------------------------- inputs

@dataclass
class PostRequest:
    """Everything the gates need to decide on one post to one platform."""
    platform: str
    scheduled_for: datetime                 # tz-aware
    clip: dict                              # row from `clips`
    render_bytes_sha256: str                # sha256 of the file that will actually be uploaded
    approval: dict | None                   # latest row from `approvals` for this clip
    streamer: dict | None                   # row from `streamers`
    platform_posts: list[dict] = field(default_factory=list)   # `posts` rows on this platform, not deleted/failed
    pauses: list[dict] = field(default_factory=list)           # rows from `pauses`
    last_profile_edit: datetime | None = None
    duration_s: float | None = None
    live: bool = False                       # True only for a real post


# ---------------------------------------------------------------- helpers

def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def caption_sha256(caption: str, yt_title: str | None, yt_description: str | None) -> str:
    """Hash of every piece of text that gets published. Approval locks this exact text."""
    blob = "\n\x1e\n".join([caption or "", yt_title or "", yt_description or ""])
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def load_warmup(path: Path = WARMUP_PATH) -> dict:
    return json.loads(path.read_text())


def daily_cap(warmup: dict, on: datetime) -> int:
    tz = ZoneInfo(warmup["timezone"])
    start = datetime.fromisoformat(warmup["start_date"]).date()
    day_n = (on.astimezone(tz).date() - start).days + 1
    if day_n < 1:
        return 0
    for band in warmup["daily_cap_by_day"]:
        if day_n >= band["from_day"] and (band["to_day"] is None or day_n <= band["to_day"]):
            return int(band["cap"])
    return 0


def _ts(v) -> datetime | None:
    if v is None or isinstance(v, datetime):
        return v
    return datetime.fromisoformat(str(v).replace("Z", "+00:00"))


# ---------------------------------------------------------------- gates

def gate_live_flag(req: PostRequest, live_env: str | None) -> list[str]:
    if req.live and live_env != "true":
        return ["LIVE is not 'true': real posting is disabled"]
    return []


def gate_platform(req: PostRequest) -> list[str]:
    return [] if req.platform in PLATFORMS else [f"unknown platform '{req.platform}'"]


def gate_schedule_tz(req: PostRequest) -> list[str]:
    return [] if req.scheduled_for.tzinfo else ["scheduled_for must be timezone-aware"]


def gate_clip_status(req: PostRequest) -> list[str]:
    status = req.clip.get("status")
    return [] if status == "approved" else [f"clip status is '{status}', must be 'approved'"]


def gate_approval(req: PostRequest) -> list[str]:
    a = req.approval
    if not a:
        return ["no approval on record"]
    errs = []
    if a.get("revoked_at"):
        errs.append("approval was revoked")
    if a.get("clip_id") != req.clip.get("id"):
        errs.append("approval belongs to a different clip")
    if a.get("render_sha256") != req.render_bytes_sha256:
        errs.append("video changed after approval (render hash mismatch)")
    if req.clip.get("render_sha256") != req.render_bytes_sha256:
        errs.append("file being uploaded is not the clip's recorded render")
    expected = caption_sha256(req.clip.get("caption"), req.clip.get("yt_title"), req.clip.get("yt_description"))
    if a.get("caption_sha256") != expected:
        errs.append("caption/title/description changed after approval (caption hash mismatch)")
    return errs


def gate_streamer(req: PostRequest) -> list[str]:
    s = req.streamer
    if not s:
        return ["streamer not in `streamers` table"]
    if s.get("handle") != req.clip.get("streamer"):
        return ["streamer record does not match clip"]
    if s.get("permission") == "blocked":
        return [f"streamer '{s['handle']}' is blocked"]
    if s.get("permission") not in ("allowlisted", "credit_only"):
        return [f"unknown permission '{s.get('permission')}'"]
    return []


def gate_caption(req: PostRequest) -> list[str]:
    caption = req.clip.get("caption") or ""
    errs = []
    if not caption.strip():
        return ["caption is empty"]
    lines = [ln for ln in caption.splitlines() if ln.strip()]
    first = lines[0].strip()
    if not CREDIT_RE.search(caption):
        errs.append("missing credit line ('Credit: @handle (Twitch)')")
    handle = (req.clip.get("streamer") or "").lower()
    if handle and f"@{handle}" not in caption.lower():
        errs.append("credit line does not name the clip's streamer")
    tags = HASHTAG_RE.findall(caption)
    if not 3 <= len(tags) <= 5:
        errs.append(f"{len(tags)} hashtags (need 3-5)")
    if len({t.lower() for t in tags}) != len(tags):
        errs.append("duplicate hashtags")
    if "—" in caption or "–" in caption:
        errs.append("caption contains an em/en dash")
    if first.endswith("?"):
        errs.append("caption ends with a question")
    if len(EMOJI_RE.findall(first)) > 1:
        errs.append("more than one emoji in caption line")
    words_n = len(EMOJI_RE.sub("", first).split())
    if not 1 <= words_n <= 6:
        errs.append(f"caption line has {words_n} words (need 1-6)")
    errs += _banned(caption, "caption")
    return errs


def gate_youtube_fields(req: PostRequest) -> list[str]:
    if req.platform != "youtube":
        return []
    title = req.clip.get("yt_title") or ""
    desc = req.clip.get("yt_description") or ""
    errs = []
    if not title.strip():
        errs.append("YouTube title is empty")
    elif len(title) > YT_TITLE_MAX:
        errs.append(f"YouTube title is {len(title)} chars (max {YT_TITLE_MAX})")
    if not CREDIT_RE.search(desc):
        errs.append("YouTube description missing credit line")
    if YT_DISCLAIMER.lower() not in desc.lower():
        errs.append("YouTube description missing Rockstar disclaimer")
    errs += _banned(title, "YouTube title") + _banned(desc, "YouTube description")
    return errs


def _banned(text: str, where: str) -> list[str]:
    low = text.lower()
    return [f"banned word '{w}' in {where}" for w in BANNED_WORDS if re.search(rf"\b{re.escape(w)}\b", low)]


def gate_duration(req: PostRequest) -> list[str]:
    d = req.duration_s
    if d is None:
        return ["clip duration unknown"]
    if not MIN_LEN_S <= d <= MAX_LEN_S:
        return [f"clip is {d:.1f}s (need {MIN_LEN_S:.0f}-{MAX_LEN_S:.0f}s)"]
    return []


def gate_not_already_posted(req: PostRequest) -> list[str]:
    for p in req.platform_posts:
        if p.get("clip_id") == req.clip.get("id") and p.get("status") not in ("failed", "deleted"):
            return [f"clip already posted/scheduled on {req.platform}"]
    return []


def gate_pauses(req: PostRequest) -> list[str]:
    errs = []
    for p in req.pauses:
        until = _ts(p.get("paused_until"))
        if p.get("platform") in (req.platform, "all") and until and until > req.scheduled_for:
            errs.append(f"{p['platform']} paused until {until.isoformat()} ({p.get('reason') or 'no reason'})")
    return errs


def gate_profile_quarantine(req: PostRequest, warmup: dict) -> list[str]:
    if not req.last_profile_edit:
        return []
    gap = timedelta(minutes=warmup["profile_edit_quarantine_minutes"])
    if timedelta(0) <= req.scheduled_for - req.last_profile_edit < gap:
        return [f"within {gap} of a profile edit"]
    return []


def gate_cadence(req: PostRequest, warmup: dict) -> list[str]:
    errs = []
    tz = ZoneInfo(warmup["timezone"])
    t = req.scheduled_for
    others = [
        _ts(p.get("scheduled_for")) for p in req.platform_posts
        if p.get("clip_id") != req.clip.get("id") and p.get("status") not in ("failed", "deleted")
        and p.get("scheduled_for")
    ]
    cap = daily_cap(warmup, t)
    same_day = [o for o in others if o.astimezone(tz).date() == t.astimezone(tz).date()]
    if len(same_day) >= cap:
        errs.append(f"daily cap reached on {req.platform}: {len(same_day)}/{cap} for {t.astimezone(tz).date()}")
    min_gap = timedelta(minutes=warmup["min_gap_minutes"])
    close = [o for o in others if abs(o - t) < min_gap]
    if close:
        errs.append(f"another {req.platform} post within {min_gap} ({min(close, key=lambda o: abs(o - t)).isoformat()})")
    in_hour = [o for o in others if abs(o - t) < timedelta(hours=1)]
    if len(in_hour) + 1 > warmup["max_posts_per_hour"]:
        errs.append(f"more than {warmup['max_posts_per_hour']} {req.platform} posts in an hour")
    return errs


def check(req: PostRequest, live_env: str | None = None, warmup: dict | None = None) -> list[str]:
    """Run every gate. Returns all failures; empty list means OK to post."""
    warmup = warmup or load_warmup()
    errs: list[str] = []
    errs += gate_platform(req)
    errs += gate_schedule_tz(req)
    if errs:
        return errs
    errs += gate_live_flag(req, live_env)
    errs += gate_clip_status(req)
    errs += gate_approval(req)
    errs += gate_streamer(req)
    errs += gate_caption(req)
    errs += gate_youtube_fields(req)
    errs += gate_duration(req)
    errs += gate_not_already_posted(req)
    errs += gate_pauses(req)
    errs += gate_profile_quarantine(req, warmup)
    errs += gate_cadence(req, warmup)
    return errs
