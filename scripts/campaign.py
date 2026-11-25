"""campaign.py: paid clipping campaigns (CPM payouts), starting with ClipFarm x Funko x TheBurntPeanut.

Three jobs, all deterministic:
  1. Money math: what a post earns at N views, and how many views it needs to pay.
  2. Gates: check an edit and its caption against the campaign brief before it is queued.
     A failed gate means the clip would not be paid ("No Pop = no pay"), so we never post it.
  3. Payout watch: read the post ledger (optionally refreshing public stats with yt-dlp),
     classify every post, and write a report that flags posts ready to submit on Whop.

This script never submits, posts, messages or signs up for anything. Submitting on Whop
and opening the approval ticket are done by the human.

Usage:
    python -m scripts.campaign payout --views 50000 [--platform tiktok]
    python -m scripts.campaign check-caption caption.txt --platform tiktok
    python -m scripts.campaign check-edit work/edit.json
    python -m scripts.campaign report [--fetch] [--out tracking/campaigns/funko-burntpeanut-report.md]
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import statistics
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.gates import BANNED_WORDS

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CAMPAIGN = "clipfarm-funko-burntpeanut"
CAMPAIGN_DIR = ROOT / "config" / "campaigns"
LEDGER = ROOT / "tracking" / "campaigns" / "funko-burntpeanut.csv"
REPORT = ROOT / "tracking" / "campaigns" / "funko-burntpeanut-report.md"
LEDGER_FIELDS = ["post_id", "platform", "url", "posted_at", "edit_id", "hook", "caption_sha256", "views", "likes",
                 "comments", "shares", "likes_visible", "tier1_share", "submitted_at", "paid_usd", "status", "notes"]
MIN_LEN_S, MAX_LEN_S = 12.0, 20.0     # playbook.md cut length
HOOK_WINDOW_S = 2.0                    # brief: strong hook in the first 2 seconds
MOMENTUM_X = 3.0                       # playbook momentum rule

# Claims the brief forbids us to invent: stock, scarcity, drop or ship times.
INVENTED_FACT_RE = re.compile(
    r"\b(sold[\s-]?out|selling fast|in stock|out of stock|restock\w*|only \d+ (left|made)|limited (stock|quantit\w+|edition|run)"
    r"|ships?\b|shipping|drops? (at|on|tomorrow|tonight|today|friday|monday)|releases? (at|on)|last chance"
    r"|ends? (at|on|tonight|tomorrow|today|soon)|\d{1,2}(:\d{2})?\s?(am|pm)\b|\b(est|pst|et|pt|gmt|utc)\b)",
    re.IGNORECASE,
)
PRICE_RE = re.compile(r"\$\s?(\d+(?:\.\d{2})?)")
NON_ASCII_LETTER_RE = re.compile(r"[^\W\d_]", re.UNICODE)


# ---------------------------------------------------------------- config

def load_campaign(cid: str = DEFAULT_CAMPAIGN) -> dict:
    return json.loads((CAMPAIGN_DIR / f"{cid}.json").read_text())


def terms(cfg: dict, platform: str) -> dict:
    p = cfg["platforms"].get(platform)
    if not p:
        raise ValueError(f"platform '{platform}' is not in this campaign")
    return p


# ---------------------------------------------------------------- money math

def est_payout(views: int | None, t: dict) -> float:
    """Dollars a post earns at `views` under CPM `t`: $0 below the minimum, capped at the max."""
    if not views or views <= 0:
        return 0.0
    gross = views / 1000 * t["cpm_usd"]
    if gross < t["min_payout_usd"]:
        return 0.0
    return round(min(gross, t["max_payout_usd"]), 2)


def views_for(dollars: float, t: dict) -> int:
    return int(-(-dollars * 1000 // t["cpm_usd"]))


def min_views(t: dict) -> int:
    return views_for(t["min_payout_usd"], t)


def max_views(t: dict) -> int:
    return views_for(t["max_payout_usd"], t)


def engagement_rate(row: dict) -> float | None:
    v = _int(row.get("views"))
    if not v:
        return None
    return sum(_int(row.get(k)) or 0 for k in ("likes", "comments", "shares")) / v


# ---------------------------------------------------------------- gates

def caption_hash(caption: str) -> str:
    return hashlib.sha256(_norm(caption).encode()).hexdigest()


def check_caption(caption: str, platform: str, cfg: dict, prior_hashes: set[str] | None = None,
                  disclosure_toggle: bool = False) -> list[str]:
    """Brief + playbook rules for a campaign caption. Empty list = OK."""
    errs: list[str] = []
    t = terms(cfg, platform)
    if not caption.strip():
        return ["caption is empty"]
    if cfg["funko_line"] not in caption:
        errs.append("missing Funko's caption line (must be copied exactly)")
    low = caption.lower()
    for tag in t["required_tags"]:
        if not re.search(rf"(?<!\w){re.escape(tag.lower())}(?!\w)", low):
            errs.append(f"missing required tag {tag} for {platform}")
    wrong = {"youtube": "@funko", "tiktok": "@originalfunko", "instagram": "@originalfunko"}[platform]
    if re.search(rf"(?<!\w){wrong}(?!\w)", low):
        errs.append(f"wrong Funko handle {wrong} for {platform}")
    for price in PRICE_RE.findall(caption):
        if price not in cfg["allowed_prices"]:
            errs.append(f"price ${price} is not an allowed fact (only $14.99)")
    m = INVENTED_FACT_RE.search(caption)
    if m:
        errs.append(f"possible made-up stock/drop/time claim: '{m.group(0)}'")
    for w in BANNED_WORDS:
        if re.search(rf"\b{re.escape(w)}\b", low):
            errs.append(f"banned word '{w}'")
    letters = NON_ASCII_LETTER_RE.findall(caption)
    if letters and sum(1 for c in letters if ord(c) > 127) / len(letters) > 0.05:
        errs.append("caption does not look like English (brief: English only)")
    if "—" in caption or "–" in caption:
        errs.append("caption contains an em/en dash")
    if cfg.get("require_disclosure") and not disclosure_toggle and not re.search(r"(?<!\w)#ad(?!\w)", low):
        errs.append("paid promotion not disclosed: add #ad or switch on the platform's branded-content toggle")
    if prior_hashes and caption_hash(caption) in prior_hashes:
        errs.append("caption already used on another post (spam rule: every post needs a new caption)")
    return errs


def check_edit(spec: dict, cfg: dict, prior_hooks: set[str] | None = None, footage_dir: str | None = None) -> list[str]:
    """Check an edit spec against the brief. Spec keys:
    edit_id, sources [paths], duration_s, hook_text, hook_start_s, subtitles_full (bool),
    pop_visible_s [timestamps in the edit where the Pop is on screen], pop_talk_s [timestamps
    where Peanut talks about the Pop], pop_overlay (bool), bad_for_brand (bool)."""
    errs: list[str] = []
    footage_dir = footage_dir or os.environ.get(cfg.get("footage_dir_env", ""), "")
    srcs = spec.get("sources") or []
    if not srcs:
        errs.append("no source files listed")
    if cfg.get("footage_only"):
        if not footage_dir:
            errs.append(f"set {cfg['footage_dir_env']} to the campaign footage folder so sources can be checked")
        else:
            root = Path(footage_dir).resolve()
            for s in srcs:
                p = Path(s).resolve()
                if root != p and root not in p.parents:
                    errs.append(f"source outside the campaign footage folder: {Path(s).name} (clips from outside the folder are rejected)")
    pop_seen = bool(spec.get("pop_visible_s")) or bool(spec.get("pop_talk_s"))
    if spec.get("pop_overlay"):
        if cfg.get("pop_overlay_allowed"):
            pop_seen = True
        else:
            errs.append("Pop image overlay is off until the owner approves it (CLAUDE.md: no logos/key art)")
    if not pop_seen:
        errs.append("no Pop in the edit: mark pop_visible_s or pop_talk_s from the contact sheet (No Pop = no pay)")
    d = spec.get("duration_s")
    if d is None or not MIN_LEN_S <= float(d) <= MAX_LEN_S:
        errs.append(f"duration {d}s outside the playbook's {MIN_LEN_S:.0f}-{MAX_LEN_S:.0f}s")
    hook = (spec.get("hook_text") or "").strip()
    if not hook:
        errs.append("no on-screen hook text")
    elif float(spec.get("hook_start_s", 0)) > HOOK_WINDOW_S:
        errs.append(f"hook starts after {HOOK_WINDOW_S:.0f}s")
    if hook and prior_hooks and _norm(hook) in prior_hooks:
        errs.append("hook already used on another post (spam rule: every post needs new text)")
    if hook and re.search(r"\bofficial\b", hook, re.IGNORECASE):
        errs.append("hook says 'official' (CLAUDE.md: never imply we're official)")
    if not spec.get("subtitles_full"):
        errs.append("subtitles must cover the whole clip")
    if spec.get("bad_for_brand"):
        errs.append("edit makes the Pop or Funko look bad")
    return errs


# ---------------------------------------------------------------- payout watch

def classify(row: dict, cfg: dict, now: datetime) -> dict:
    """Return {status, est_usd, action, detail} for one ledger row."""
    t = terms(cfg, row["platform"])
    views = _int(row.get("views"))
    est = est_payout(views, t)
    er = engagement_rate(row)
    posted = _ts(row.get("posted_at"))
    keep_until = posted + timedelta(days=cfg["keep_up_days"]) if posted else None
    out = {"est_usd": est, "engagement": er, "keep_until": keep_until}
    state = (row.get("status") or "").lower()

    if state in ("deleted", "removed"):
        early = keep_until and now < keep_until
        return {**out, "status": "REMOVED", "action": "none",
                "detail": "removed before the 30-day hold: payout at risk" if early else "removed"}
    if row.get("paid_usd"):
        return {**out, "status": "PAID", "action": "none", "detail": f"paid ${row['paid_usd']}"}
    if views is None:
        return {**out, "status": "NEEDS_STATS", "action": "fetch stats", "detail": "no view count yet"}
    if str(row.get("likes_visible", "")).lower() in ("false", "no", "0"):
        return {**out, "status": "FIX_LIKES", "action": "unhide likes", "detail": "hidden likes are rejected"}
    if views < min_views(t):
        return {**out, "status": "BELOW_MIN", "action": "wait",
                "detail": f"{min_views(t) - views:,} more views to reach the ${t['min_payout_usd']:.2f} minimum"}
    if er is not None and er < cfg["min_engagement_rate"]:
        need = int(views * cfg["min_engagement_rate"]) - int(er * views)
        return {**out, "status": "HOLD_ENGAGEMENT", "action": "do not submit yet",
                "detail": f"engagement {er:.2%} < {cfg['min_engagement_rate']:.2%} ({need} more likes/comments/shares needed)"}
    tier1 = _float(row.get("tier1_share"))
    if tier1 is not None and tier1 < cfg["tier1_min_share"]:
        return {**out, "status": "HOLD_AUDIENCE", "action": "do not submit",
                "detail": f"US/UK/CA/AU share {tier1:.0%} < {cfg['tier1_min_share']:.0%}"}
    maxed = est >= t["max_payout_usd"]
    if state == "submitted" or row.get("submitted_at"):
        return {**out, "status": "MAXED" if maxed else "SUBMITTED", "action": "keep up until " + _d(keep_until),
                "detail": "hit the $400 cap, stop pushing this edit" if maxed else "waiting on approval"}
    detail = "submit on Whop, then send the demographics screenshot"
    if tier1 is None:
        detail += " (check US/UK/CA/AU is 40% or more first)"
    return {**out, "status": "MAXED_SUBMIT" if maxed else "SUBMIT", "action": "SUBMIT NOW", "detail": detail}


def momentum(rows: list[dict]) -> list[dict]:
    views = [_int(r.get("views")) for r in rows if _int(r.get("views"))]
    if len(views) < 3:
        return []
    med = statistics.median(views)
    return [r for r in rows if (_int(r.get("views")) or 0) >= MOMENTUM_X * med]


def report(rows: list[dict], cfg: dict, now: datetime) -> str:
    res = [(r, classify(r, cfg, now)) for r in rows]
    submit = [x for x in res if x[1]["action"] == "SUBMIT NOW"]
    pending = sum(c["est_usd"] for r, c in res if c["status"] in ("SUBMIT", "MAXED_SUBMIT", "SUBMITTED", "MAXED"))
    L = [f"# {cfg['name']}: payout watch", "",
         f"Run {now.astimezone(timezone.utc):%Y-%m-%d %H:%M} UTC. Estimates only; ClipFarm decides the payout.", "",
         f"- Posts tracked: {len(rows)}",
         f"- Ready to submit: **{len(submit)}**",
         f"- Estimated earned, not yet paid: **${pending:,.2f}**",
         f"- Campaign budget left (as of {cfg['budget_checked_at']}): ${cfg['budget_remaining_usd']:,.0f} of ${cfg['budget_total_usd']:,.0f}"]
    if pending and pending > cfg["budget_remaining_usd"] * 0.5:
        L.append("- **Budget risk:** our pending estimate is more than half the remaining budget. Submit fast.")
    L += ["", "## SUBMIT NOW", ""]
    if submit:
        L += ["| Platform | Post | Views | Engagement | Est. $ | Next step |", "|---|---|---|---|---|---|"]
        for r, c in submit:
            L.append(f"| {r['platform']} | {r.get('url') or r.get('post_id')} | {_int(r['views']):,} | "
                     f"{_pct(c['engagement'])} | ${c['est_usd']:,.2f} | {c['detail']} |")
        L += ["", f"Where: {cfg['submit_where']}",
              "After submitting, set `submitted_at` in the ledger. Keep every post up for 30 days."]
    else:
        L.append("Nothing ready yet.")
    L += ["", "## All posts", "", "| Platform | Post | Views | Eng. | Est. $ | Status | Detail |", "|---|---|---|---|---|---|---|"]
    for r, c in sorted(res, key=lambda x: -(_int(x[0].get("views")) or 0)):
        v = _int(r.get("views"))
        L.append(f"| {r['platform']} | {r.get('url') or r.get('post_id')} | {'' if v is None else f'{v:,}'} | "
                 f"{_pct(c['engagement'])} | ${c['est_usd']:,.2f} | {c['status']} | {c['detail']} |")
    hot = momentum(rows)
    if hot:
        L += ["", "## Momentum (3x median or more): make a NEW edit in this style within 24h", ""]
        L += [f"- {r['platform']} {r.get('url') or r.get('post_id')}: {_int(r['views']):,} views, hook \"{r.get('hook', '')}\"" for r in hot]
    return "\n".join(L) + "\n"


# ---------------------------------------------------------------- stats (public pages, no keys)

def fetch_stats(url: str) -> dict | None:
    """Public view/like/comment/share counts via yt-dlp. None if the page can't be read."""
    try:
        r = subprocess.run(["yt-dlp", "--no-warnings", "-J", "--skip-download", url],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if r.returncode != 0:
        return None
    try:
        j = json.loads(r.stdout)
    except json.JSONDecodeError:
        return None
    return {"views": j.get("view_count"), "likes": j.get("like_count"),
            "comments": j.get("comment_count"), "shares": j.get("repost_count")}


def read_ledger(path: Path = LEDGER) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def write_ledger(rows: list[dict], path: Path = LEDGER) -> None:
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LEDGER_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


# ---------------------------------------------------------------- helpers

def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _int(v) -> int | None:
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _float(v) -> float | None:
    if v in (None, ""):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f / 100 if f > 1 else f


def _ts(v) -> datetime | None:
    if not v:
        return None
    t = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _d(t: datetime | None) -> str:
    return t.date().isoformat() if t else "?"


def _pct(x: float | None) -> str:
    return "" if x is None else f"{x:.2%}"


# ---------------------------------------------------------------- CLI

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Paid clipping campaign: money math, gates, payout watch.")
    ap.add_argument("--campaign", default=DEFAULT_CAMPAIGN)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("payout")
    p.add_argument("--views", type=int, required=True)
    p.add_argument("--platform", default="tiktok")
    c = sub.add_parser("check-caption")
    c.add_argument("file")
    c.add_argument("--platform", required=True)
    c.add_argument("--disclosure-toggle", action="store_true")
    e = sub.add_parser("check-edit")
    e.add_argument("spec")
    r = sub.add_parser("report")
    r.add_argument("--fetch", action="store_true", help="refresh public stats with yt-dlp and save them to the ledger")
    r.add_argument("--ledger", default=str(LEDGER))
    r.add_argument("--out", default=str(REPORT))
    a = ap.parse_args(argv)
    cfg = load_campaign(a.campaign)
    rows = read_ledger(Path(getattr(a, "ledger", LEDGER)))

    if a.cmd == "payout":
        t = terms(cfg, a.platform)
        print(json.dumps({"views": a.views, "est_usd": est_payout(a.views, t), "min_views": min_views(t),
                          "max_views": max_views(t)}))
        return 0
    if a.cmd == "check-caption":
        errs = check_caption(Path(a.file).read_text(), a.platform, cfg,
                             {r["caption_sha256"] for r in rows if r.get("caption_sha256")}, a.disclosure_toggle)
    elif a.cmd == "check-edit":
        errs = check_edit(json.loads(Path(a.spec).read_text()), cfg, {_norm(r["hook"]) for r in rows if r.get("hook")})
    else:
        if a.fetch:
            for row in rows:
                if not row.get("url") or (row.get("status") or "").lower() in ("deleted", "removed"):
                    continue
                s = fetch_stats(row["url"])
                if s is None:
                    print(f"stats unavailable: {row['url']} (enter them by hand)", file=sys.stderr)
                    continue
                for k, v in s.items():
                    if v is not None:
                        row[k] = v
            write_ledger(rows, Path(a.ledger))
        text = report(rows, cfg, datetime.now(timezone.utc))
        Path(a.out).write_text(text)
        n = sum(1 for r in rows if classify(r, cfg, datetime.now(timezone.utc))["action"] == "SUBMIT NOW")
        print(f"{n} post(s) ready to submit -> {a.out}")
        return 0
    for x in errs:
        print(f"FAIL: {x}")
    print("OK" if not errs else f"{len(errs)} problem(s)")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
