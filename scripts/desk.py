"""desk.py: the Post desk. A local page for posting clips by hand, with every gate enforced.

    python -m scripts.desk            then open http://127.0.0.1:8765

Flow per clip:
  1. Drop an .mp4 into inbox/  ->  Add it (streamer + hook). Caption, YouTube title,
     description and thumbnails are pre-filled.
  2. Edit the text, regenerate the thumbnail if needed, watch the clip.
  3. Approve. This locks the exact video file and text (hashes). Any later edit voids it.
  4. Post: pick platforms and now/schedule -> review screen -> Confirm or Cancel.
Nothing goes out unless LIVE=true in the environment; otherwise you get a dry-run payload.

Standard library only. Listens on 127.0.0.1 so nothing outside this computer can reach it.
"""
from __future__ import annotations

import html
import json
import mimetypes
import os
import re
import secrets
import shutil
import sys
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
from zoneinfo import ZoneInfo

from scripts import gates, postiz, publish, thumb
from scripts.clip import probe

ROOT = Path(__file__).resolve().parent.parent
INBOX = ROOT / "inbox"
DESK = ROOT / "work" / "desk"
ET = ZoneInfo("America/Toronto")
TOKEN = secrets.token_urlsafe(16)
HANDLE_RE = re.compile(r"^[a-z0-9_]{2,25}$")


# ---------------------------------------------------------------- storage

def item_dir(iid: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{8}", iid):
        raise KeyError(iid)
    return DESK / iid


def load(iid: str) -> dict:
    return json.loads((item_dir(iid) / "item.json").read_text())


def save(item: dict) -> None:
    d = item_dir(item["id"])
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "item.json.tmp"
    tmp.write_text(json.dumps(item, indent=2))
    tmp.replace(d / "item.json")


def all_items() -> list[dict]:
    if not DESK.exists():
        return []
    out = []
    for p in DESK.glob("*/item.json"):
        try:
            out.append(json.loads(p.read_text()))
        except json.JSONDecodeError:
            continue
    return sorted(out, key=lambda i: i["created_at"], reverse=True)


def log(item: dict, msg: str) -> None:
    item.setdefault("log", []).append(f"{datetime.now(ET):%Y-%m-%d %H:%M} {msg}")


# ---------------------------------------------------------------- defaults

def default_text(streamer: str, hook: str) -> dict:
    h = " ".join(hook.split())
    return {
        "caption": f"{h.lower()}\n\nCredit: @{streamer} (Twitch)\n\n#gta6 #{streamer} #gaming #funnymoments",
        "yt_title": (f"{h} #gta6" if len(h) <= 53 else h[:60]).strip(),
        "yt_description": (f"Credit: @{streamer} (Twitch)\nhttps://www.twitch.tv/{streamer}\n\n"
                           f"#gta6 #{streamer} #gaming\n\nFan page. Not affiliated with Rockstar Games."),
    }


def add_item(filename: str, streamer: str, hook: str) -> dict:
    src = (INBOX / filename).resolve()
    if src.parent != INBOX.resolve() or not src.exists() or src.suffix.lower() != ".mp4":
        raise ValueError("pick an .mp4 from the inbox folder")
    streamer = streamer.strip().lstrip("@").lower()
    if not HANDLE_RE.fullmatch(streamer):
        raise ValueError("streamer must be a Twitch handle (letters, numbers, underscore)")
    if not hook.strip():
        raise ValueError("hook is required")
    iid = uuid.uuid4().hex[:8]
    d = item_dir(iid)
    d.mkdir(parents=True)
    video = d / "clip.mp4"
    shutil.move(str(src), video)
    _, duration, _ = probe(str(video))
    item = {
        "id": iid, "created_at": datetime.now(timezone.utc).isoformat(), "source_name": filename,
        "streamer": streamer, "hook": hook.strip(), "duration_s": round(duration, 2),
        "render_sha256": gates.sha256_file(video), "approval": None, "posts": {},
        "thumb_text": hook.strip().upper(), "thumb_at": None, "thumbs": {},
        **default_text(streamer, hook),
    }
    log(item, f"added from inbox/{filename}")
    _make_thumbs(item)
    save(item)
    return item


def _make_thumbs(item: dict) -> None:
    d = item_dir(item["id"])
    res = thumb.make(d / "clip.mp4", item["thumb_text"], item.get("thumb_at"), d)
    item["thumbs"] = {"landscape": Path(res["landscape"]).name, "vertical": Path(res["vertical"]).name}
    item["thumb_warnings"] = res["warnings"]
    item["thumb_from_s"] = res["frame_from_s"]


# ---------------------------------------------------------------- gate context

def streamer_record(handle: str) -> dict:
    p = ROOT / "config" / "permitted_streamers.json"
    for s in json.loads(p.read_text()).get("streamers", []):
        if s.get("handle", "").lower() == handle:
            return {"handle": handle, "permission": s.get("permission", "credit_only")}
    return {"handle": handle, "permission": "credit_only"}   # manual approval is happening right here


def platform_posts(platform: str) -> list[dict]:
    out = []
    for it in all_items():
        p = it.get("posts", {}).get(platform)
        if p and not p.get("dry_run") and p.get("status") not in ("failed", "deleted", "cancelled"):
            out.append({"clip_id": it["id"], "status": p["status"], "scheduled_for": p["scheduled_for"]})
    return out


def desk_config() -> dict:
    p = ROOT / "config" / "desk.json"
    return json.loads(p.read_text()) if p.exists() else {}


def approval_state(item: dict) -> str:
    a = item.get("approval")
    if not a or a.get("revoked_at"):
        return "none"
    cur = gates.caption_sha256(item["caption"], item["yt_title"], item["yt_description"])
    video = gates.sha256_file(item_dir(item["id"]) / "clip.mp4")
    return "valid" if a["caption_sha256"] == cur and a["render_sha256"] == video == item["render_sha256"] else "stale"


def post_request(item: dict, platform: str, when: datetime) -> gates.PostRequest:
    cfg = desk_config()
    a = item.get("approval")
    clip = {"id": item["id"], "streamer": item["streamer"], "render_sha256": item["render_sha256"],
            "caption": item["caption"], "yt_title": item["yt_title"], "yt_description": item["yt_description"],
            "status": "approved" if a and not a.get("revoked_at") else "queued"}
    approval = {**a, "clip_id": item["id"]} if a else None
    pauses = cfg.get("pauses", [])
    lpe = cfg.get("last_profile_edit")
    return gates.PostRequest(
        platform=platform, scheduled_for=when, clip=clip,
        render_bytes_sha256=gates.sha256_file(item_dir(item["id"]) / "clip.mp4"),
        approval=approval, streamer=streamer_record(item["streamer"]),
        platform_posts=platform_posts(platform), pauses=pauses,
        last_profile_edit=datetime.fromisoformat(lpe) if lpe else None,
        duration_s=item["duration_s"],
    )


def approve_item(item: dict) -> None:
    video = gates.sha256_file(item_dir(item["id"]) / "clip.mp4")
    if video != item["render_sha256"]:
        raise ValueError("The video file changed since it was added. Re-add it.")
    item["approval"] = {"render_sha256": video, "approved_by": "desk",
                        "caption_sha256": gates.caption_sha256(item["caption"], item["yt_title"], item["yt_description"]),
                        "approved_at": datetime.now(timezone.utc).isoformat(), "revoked_at": None}
    log(item, "approved")


def save_text(item: dict, caption: str, yt_title: str, yt_description: str) -> None:
    before = gates.caption_sha256(item["caption"], item["yt_title"], item["yt_description"])
    item["caption"], item["yt_title"], item["yt_description"] = (
        v.replace("\r\n", "\n").strip() for v in (caption, yt_title, yt_description))
    after = gates.caption_sha256(item["caption"], item["yt_title"], item["yt_description"])
    if after != before and item.get("approval") and not item["approval"].get("revoked_at"):
        item["approval"]["revoked_at"] = datetime.now(timezone.utc).isoformat()
        log(item, "text edited: approval voided")
    log(item, "text saved")


def live_enabled() -> bool:
    return os.environ.get("LIVE") == "true"


# ---------------------------------------------------------------- HTML

CSS = """
:root{--bg:#f4f5f8;--card:#fff;--fg:#151826;--muted:#5d647a;--line:#dde0e8;--accent:#e24e1b;--ok:#16794f;--bad:#b42318;--warn:#a15c00}
@media (prefers-color-scheme:dark){:root{--bg:#0f1220;--card:#171b2e;--fg:#eceef6;--muted:#a2a8be;--line:#2a2f48;--accent:#ff6b3d;--ok:#35c48a;--bad:#ff7b6e;--warn:#f2b24c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,Segoe UI,sans-serif}
.wrap{max-width:1100px;margin:0 auto;padding:20px 16px 60px;display:flex;flex-direction:column;gap:18px}
h1,h2,h3{margin:0;line-height:1.2}a{color:var(--accent)}
.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;display:flex;flex-direction:column;gap:12px;min-width:0}
.row{display:flex;gap:16px;flex-wrap:wrap;align-items:flex-start}.row>*{min-width:0}
label{display:flex;flex-direction:column;gap:4px;font-weight:600;font-size:13px}
input,textarea,select{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--bg);color:var(--fg);width:100%}
textarea{min-height:110px}
button{font:inherit;font-weight:600;padding:9px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}button.danger{color:var(--bad)}
.pill{display:inline-block;padding:2px 10px;border-radius:99px;font-size:12px;font-weight:700;border:1px solid currentColor}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.muted{color:var(--muted)}
ul.errs{margin:0;padding-left:18px}video{width:100%;max-width:320px;border-radius:10px;background:#000}
img.thumb{max-width:100%;border-radius:8px;border:1px solid var(--line)}
table{border-collapse:collapse;width:100%}td,th{text-align:left;padding:8px;border-bottom:1px solid var(--line);vertical-align:top}
.banner{padding:10px 14px;border-radius:10px;font-weight:600}.banner.live{background:#b4231822;color:var(--bad)}.banner.dry{background:#16794f22;color:var(--ok)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:10px;border-radius:8px;font-size:12px;max-height:340px;overflow:auto}
"""


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def page(title: str, body: str) -> bytes:
    mode = ('<div class="banner live">LIVE: confirmed posts go to TikTok/YouTube for real.</div>' if live_enabled()
            else '<div class="banner dry">DRY-RUN: nothing will be posted. Start with LIVE=true to post for real.</div>')
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body><div class='wrap'>"
            f"<div class='row' style='justify-content:space-between;align-items:center'><h1><a href='/' style='color:inherit;text-decoration:none'>Clippy Club · Post desk</a></h1></div>"
            f"{mode}{body}</div></body></html>").encode()


def form(action: str, inner: str, cls: str = "") -> str:
    return f"<form method='post' action='{esc(action)}' class='{cls}'><input type='hidden' name='t' value='{TOKEN}'>{inner}</form>"


def status_pill(item: dict) -> str:
    posts = item.get("posts", {})
    live = [p for p in posts.values() if not p.get("dry_run") and p.get("status") not in ("failed", "cancelled")]
    if live:
        names = sorted({(p.get("backend") or "blotato").capitalize() for p in live})
        return f"<span class='pill ok'>sent to {esc(', '.join(names))}</span>"
    st = approval_state(item)
    return {"valid": "<span class='pill ok'>approved</span>", "stale": "<span class='pill warn'>approval void (edited)</span>",
            "none": "<span class='pill muted'>draft</span>"}[st]


def posts_summary(item: dict) -> str:
    return ", ".join(f"{k}: {v.get('status')}" for k, v in item.get("posts", {}).items())


def home_page(msg: str = "") -> bytes:
    INBOX.mkdir(exist_ok=True)
    files = sorted(p.name for p in INBOX.glob("*.mp4"))
    opts = "".join(f"<option>{esc(f)}</option>" for f in files)
    add = (form("/add", f"""
        <div class='row'>
          <label style='flex:2 1 240px'>Clip from inbox/<select name='file' required>{opts}</select></label>
          <label style='flex:1 1 160px'>Streamer (Twitch handle)<input name='streamer' placeholder='ohnepixel' required></label>
          <label style='flex:2 1 260px'>Hook (on-screen idea, 2-6 words)<input name='hook' placeholder='GTA 6 cops chose violence' required></label>
        </div><div><button class='primary'>Add clip</button></div>""")
           if files else f"<p class='muted'>Drop .mp4 files into <code>{esc(INBOX)}</code>, then refresh.</p>")
    rows = "".join(
        f"<tr><td><a href='/item/{i['id']}'>{esc(i['hook'])}</a><div class='muted'>@{esc(i['streamer'])} · {i['duration_s']}s</div></td>"
        f"<td>{status_pill(i)}</td><td class='muted'>{esc(posts_summary(i))}</td></tr>"
        for i in all_items())
    note = f"<div class='banner warn'>{esc(msg)}</div>" if msg else ""
    return page("Post desk", f"""{note}
      <div class='card'><h2>Add a clip</h2>{add}</div>
      <div class='card'><h2>Clips</h2>{'<table><tr><th>Clip</th><th>Status</th><th>Posts</th></tr>' + rows + '</table>' if rows else "<p class='muted'>No clips yet.</p>"}</div>""")


def default_when() -> str:
    return (datetime.now(ET) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M")


def gate_block(item: dict, when: datetime) -> str:
    out = []
    for plat in gates.PLATFORMS:
        errs = gates.check(post_request(item, plat, when), live_env="true" if live_enabled() else None)
        errs = [e for e in errs if not e.startswith("LIVE")]
        body = "<span class='pill ok'>ready</span>" if not errs else "<ul class='errs bad'>" + "".join(f"<li>{esc(e)}</li>" for e in errs) + "</ul>"
        out.append(f"<div style='flex:1 1 280px'><h3>{plat.title()}</h3>{body}</div>")
    return "<div class='row'>" + "".join(out) + "</div>"


def item_page(iid: str, msg: str = "", when_s: str | None = None) -> bytes:
    item = load(iid)
    when_s = when_s or default_when()
    when = datetime.fromisoformat(when_s).replace(tzinfo=ET)
    st = approval_state(item)
    th = item.get("thumbs", {})
    media = f"/media/{iid}"
    warns = "".join(f"<div class='warn'>{esc(w)}</div>" for w in item.get("thumb_warnings", []))
    posts = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(v.get('status'))}{' (dry-run)' if v.get('dry_run') else ''}</td>"
        f"<td>{esc(v.get('scheduled_for') or 'now')}</td><td class='muted'>{esc(v.get('postSubmissionId') or v.get('error') or '')}</td>"
        f"<td>{form(f'/item/{iid}/refresh', f'<input type=hidden name=platform value={esc(k)}><button>Refresh status</button>') if v.get('postSubmissionId') else ''}"
        f"{form(f'/item/{iid}/mark-cancelled', f'<input type=hidden name=platform value={esc(k)}><button class=danger>Mark cancelled</button>') if v.get('status') == 'scheduled' and not v.get('dry_run') else ''}</td></tr>"
        for k, v in item.get("posts", {}).items())
    if st == "valid":
        approve_ui = (f"<span class='pill ok'>approved {esc(item['approval']['approved_at'][:16])}</span>"
                      + form(f"/item/{iid}/unapprove", "<button class='danger'>Withdraw approval</button>"))
    elif st == "stale":
        approve_ui = "<span class='pill warn'>approval void: video or text changed</span>"
    else:
        approve_ui = ""
    if st != "valid":
        approve_ui += form(f"/item/{iid}/approve",
                           "<label style='flex-direction:row;gap:8px;align-items:center;font-weight:500'>"
                           "<input type='checkbox' name='watched' value='1' required style='width:auto'> I watched the whole clip and checked the text</label>"
                           "<div><button class='primary'>Approve</button></div>")
    note = f"<div class='banner warn'>{esc(msg)}</div>" if msg else ""
    return page(item["hook"], f"""{note}
    <div class='row'>
      <div class='card' style='flex:0 1 340px'>
        <video controls preload='metadata' src='{media}/clip.mp4'></video>
        <div class='muted'>@{esc(item['streamer'])} · {item['duration_s']}s · from {esc(item['source_name'])}</div>
        {status_pill(item)}
      </div>
      <div class='card' style='flex:1 1 480px'>
        <h2>Text</h2>
        {form(f'/item/{iid}/save', f'''
          <label>TikTok caption<textarea name='caption'>{esc(item['caption'])}</textarea></label>
          <label>YouTube title (max 60)<input name='yt_title' maxlength='100' value='{esc(item['yt_title'])}'></label>
          <label>YouTube description<textarea name='yt_description'>{esc(item['yt_description'])}</textarea></label>
          <div><button>Save text</button> <span class='muted'>Saving changed text voids the approval.</span></div>''')}
      </div>
    </div>
    <div class='card'>
      <h2>YouTube thumbnail</h2>
      <div class='row'>
        <div style='flex:2 1 420px'><img class='thumb' src='{media}/{esc(th.get('landscape', ''))}?v={secrets.token_hex(3)}' alt='16:9 thumbnail'></div>
        <div style='flex:1 1 160px'><img class='thumb' style='max-height:300px' src='{media}/{esc(th.get('vertical', ''))}?v={secrets.token_hex(3)}' alt='9:16 cover'></div>
      </div>{warns}
      {form(f'/item/{iid}/thumb', f'''<div class='row'>
        <label style='flex:3 1 260px'>Thumbnail text (2-5 words)<input name='thumb_text' value='{esc(item['thumb_text'])}'></label>
        <label style='flex:1 1 120px'>Frame at (s, blank = auto)<input name='thumb_at' value='{esc(item.get('thumb_at') or '')}' placeholder='auto ({item.get('thumb_from_s', '')}s)'></label>
        </div><div><button>Regenerate thumbnail</button> <span class='muted'>YouTube uses it only if your channel is verified.</span></div>''')}
    </div>
    <div class='card'><h2>Approve</h2>{approve_ui}</div>
    <div class='card'>
      <h2>Post</h2>
      {form(f'/item/{iid}', f"<div class='row' style='align-items:flex-end'><label style='flex:0 1 260px'>Check gates for (Eastern)<input type='datetime-local' name='when' value='{esc(when_s)}'></label><button>Re-check</button></div>")}
      {gate_block(item, when)}
      {form(f'/item/{iid}/review', f'''
        <div class='row' style='align-items:flex-end'>
          <label style='flex-direction:row;gap:6px;align-items:center'><input type='checkbox' name='platform' value='tiktok' checked style='width:auto'> TikTok</label>
          <label style='flex-direction:row;gap:6px;align-items:center'><input type='checkbox' name='platform' value='youtube' checked style='width:auto'> YouTube</label>
          <label style='flex:0 1 200px'>When<select name='mode'><option value='schedule'>Schedule</option><option value='now'>Post now</option></select></label>
          <label style='flex:0 1 260px'>Scheduled time (Eastern)<input type='datetime-local' name='when' value='{esc(when_s)}'></label>
          <button class='primary'>Review post</button>
        </div>''')}
      {'<table><tr><th>Platform</th><th>Status</th><th>When</th><th>ID / error</th><th></th></tr>' + posts + '</table>' if posts else ''}
    </div>
    <div class='card'><h3>History</h3><pre>{esc(chr(10).join(item.get('log', [])))}</pre></div>""")


def review_page(iid: str, platforms: list[str], mode: str, when_s: str) -> bytes:
    item = load(iid)
    when = datetime.now(ET) if mode == "now" else datetime.fromisoformat(when_s).replace(tzinfo=ET)
    blocks, all_ok = [], True
    for plat in platforms:
        req = post_request(item, plat, when)
        errs = [e for e in gates.check(req, live_env="true" if live_enabled() else None) if not e.startswith("LIVE")]
        all_ok &= not errs
        shown = item["caption"] if plat == "tiktok" else f"TITLE: {item['yt_title']}\n\n{item['yt_description']}"
        blocks.append(f"<div class='card' style='flex:1 1 320px'><h3>{plat.title()}</h3><pre>{esc(shown)}</pre>"
                      + ("<span class='pill ok'>all gates pass</span>" if not errs else "<ul class='errs bad'>" + "".join(f"<li>{esc(e)}</li>" for e in errs) + "</ul>")
                      + "</div>")
    hidden = "".join(f"<input type='hidden' name='platform' value='{esc(p)}'>" for p in platforms)
    hidden += f"<input type='hidden' name='mode' value='{esc(mode)}'><input type='hidden' name='when' value='{esc(when_s)}'>"
    what = "right now" if mode == "now" else f"at {when:%a %b %d, %I:%M %p} Eastern"
    verb = "Post for real" if live_enabled() else "Run dry-run"
    actions = (form(f"/item/{iid}/post", hidden + f"<div class='row'><button class='primary'>{verb}: {esc(', '.join(platforms))} {esc(what)}</button></div>")
               if all_ok else "<p class='bad'>Fix the problems above first.</p>")
    return page("Review post", f"""
      <div class='card'><h2>Review: {esc(item['hook'])}</h2>
        <p>This sends <b>exactly</b> the video and text below to <b>{esc(', '.join(platforms))}</b> {esc(what)}.</p>
        <video controls preload='metadata' src='/media/{iid}/clip.mp4' style='max-width:240px'></video>
      </div>
      <div class='row'>{''.join(blocks)}</div>
      <div class='card'>{actions}<a href='/item/{iid}'>Cancel and go back</a></div>""")


def do_post(iid: str, platforms: list[str], mode: str, when_s: str) -> str:
    item = load(iid)
    when = datetime.now(ET) if mode == "now" else datetime.fromisoformat(when_s).replace(tzinfo=ET)
    d = item_dir(iid)
    live = live_enabled()
    msgs = []
    for plat in platforms:
        req = post_request(item, plat, when)
        thumb_path = d / item["thumbs"]["landscape"] if plat == "youtube" and item.get("thumbs") else None
        try:
            res = publish.publish(req, d / "clip.mp4", thumb_path, live=live, post_now=(mode == "now"))
        except (publish.PublishRefused, publish.PublisherError) as e:
            item["posts"][plat] = {"status": "failed", "error": str(e), "dry_run": not live,
                                   "scheduled_for": when.isoformat()}
            log(item, f"{plat}: FAILED {e}")
            msgs.append(f"{plat}: failed. {e}")
            continue
        item["posts"][plat] = {
            "status": ("dry-run" if res["dry_run"] else ("scheduled" if mode == "schedule" else "submitted")),
            "dry_run": res["dry_run"], "scheduled_for": when.isoformat(),
            "postSubmissionId": res.get("postSubmissionId"), "backend": res.get("backend", "blotato"),
        }
        via = res.get("backend", "blotato").capitalize()
        if res["dry_run"]:
            item["posts"][plat]["payload"] = res["payload"]
        log(item, f"{plat}: {'dry-run OK' if res['dry_run'] else f'sent to {via} ' + str(res.get('postSubmissionId'))}"
                  f" for {'now' if mode == 'now' else when.isoformat()}")
        msgs.append(f"{plat}: {'dry-run OK (nothing posted)' if res['dry_run'] else f'sent to {via}'}")
    save(item)
    return " · ".join(msgs)


# ---------------------------------------------------------------- server

class Handler(BaseHTTPRequestHandler):
    server_version = "ClippyDesk/1"

    def log_message(self, fmt, *args):
        sys.stderr.write("desk: " + (fmt % args) + "\n")

    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8", extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # browser cancelled a video range request

    def _redirect(self, to: str):
        self.send_response(303)
        self.send_header("Location", to)
        self.end_headers()

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        try:
            if u.path == "/":
                return self._send(200, home_page(q.get("msg", [""])[0]))
            m = re.fullmatch(r"/item/([a-f0-9]{8})", u.path)
            if m:
                return self._send(200, item_page(m.group(1), q.get("msg", [""])[0], q.get("when", [None])[0]))
            m = re.fullmatch(r"/media/([a-f0-9]{8})/([A-Za-z0-9._-]+)", u.path)
            if m:
                return self._media(item_dir(m.group(1)) / m.group(2))
            self._send(404, page("Not found", "<p>Not found.</p>"))
        except KeyError:
            self._send(404, page("Not found", "<p>Not found.</p>"))

    def _media(self, p: Path):
        if not p.is_file() or p.name == "item.json":
            return self._send(404, b"not found", "text/plain")
        size = p.stat().st_size
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        rng = self.headers.get("Range")
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", rng or "")
        if m and (m.group(1) or m.group(2)):
            start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
            end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
            end = min(end, size - 1)
            with open(p, "rb") as f:
                f.seek(start)
                data = f.read(end - start + 1)
            return self._send(206, data, ctype, {"Content-Range": f"bytes {start}-{end}/{size}", "Accept-Ranges": "bytes"})
        self._send(200, p.read_bytes(), ctype, {"Accept-Ranges": "bytes"})

    def do_POST(self):
        u = urlparse(self.path)
        n = int(self.headers.get("Content-Length") or 0)
        f = parse_qs(self.rfile.read(n).decode("utf-8"), keep_blank_values=True)
        get = lambda k, d="": f.get(k, [d])[0]
        if get("t") != TOKEN:
            return self._send(403, page("Expired", "<p>This page is out of date. Go back and refresh.</p>"))
        try:
            if u.path == "/add":
                item = add_item(get("file"), get("streamer"), get("hook"))
                return self._redirect(f"/item/{item['id']}")
            m = re.fullmatch(r"/item/([a-f0-9]{8})(?:/([a-z-]+))?", u.path)
            if not m:
                return self._send(404, page("Not found", "<p>Not found.</p>"))
            iid, action = m.group(1), m.group(2)
            item = load(iid)
            if action is None:
                return self._redirect(f"/item/{iid}?when={quote(get('when'))}")
            if action == "save":
                save_text(item, get("caption"), get("yt_title"), get("yt_description"))
                save(item)
                return self._redirect(f"/item/{iid}?msg=Saved")
            if action == "thumb":
                item["thumb_text"] = get("thumb_text").strip() or item["hook"].upper()
                at = get("thumb_at").strip()
                item["thumb_at"] = float(at) if at else None
                _make_thumbs(item)
                log(item, "thumbnail regenerated")
                save(item)
                return self._redirect(f"/item/{iid}")
            if action == "approve":
                if get("watched") != "1":
                    return self._redirect(f"/item/{iid}?msg={quote('Tick the box to confirm you watched it.')}")
                try:
                    approve_item(item)
                except ValueError as e:
                    return self._redirect(f"/item/{iid}?msg={quote(str(e))}")
                save(item)
                return self._redirect(f"/item/{iid}?msg=Approved")
            if action == "unapprove":
                if item.get("approval"):
                    item["approval"]["revoked_at"] = datetime.now(timezone.utc).isoformat()
                log(item, "approval withdrawn")
                save(item)
                return self._redirect(f"/item/{iid}")
            if action in ("review", "post"):
                platforms = [p for p in f.get("platform", []) if p in gates.PLATFORMS]
                if not platforms:
                    return self._redirect(f"/item/{iid}?msg={quote('Pick at least one platform.')}")
                mode = "now" if get("mode") == "now" else "schedule"
                when_s = get("when") or default_when()
                if action == "review":
                    return self._send(200, review_page(iid, platforms, mode, when_s))
                return self._redirect(f"/item/{iid}?msg={quote(do_post(iid, platforms, mode, when_s))}")
            if action == "refresh":
                plat = get("platform")
                p = item["posts"].get(plat) or {}
                if p.get("postSubmissionId") and p.get("backend") == "postiz":
                    r = postiz.Postiz().find_post(p["postSubmissionId"], datetime.fromisoformat(p["scheduled_for"]))
                    if r:
                        p["status"] = postiz.STATE_MAP.get(r.get("state"), p.get("status"))
                        if r.get("releaseURL"):
                            p["live_url"] = r["releaseURL"]
                        if r.get("error"):
                            p["error"] = r["error"]
                    log(item, f"{plat}: status {p['status']}")
                    save(item)
                elif p.get("postSubmissionId"):
                    r = publish.Blotato().post_status(p["postSubmissionId"])
                    p["status"] = r.get("status", p.get("status"))
                    if r.get("publicUrl"):
                        p["live_url"] = r["publicUrl"]
                    if r.get("errorMessage"):
                        p["error"] = r["errorMessage"]
                    log(item, f"{plat}: status {p['status']}")
                    save(item)
                return self._redirect(f"/item/{iid}")
            if action == "mark-cancelled":
                plat = get("platform")
                if plat in item["posts"]:
                    item["posts"][plat]["status"] = "cancelled"
                    via = (item["posts"][plat].get("backend") or "blotato").capitalize()
                    log(item, f"{plat}: marked cancelled (cancel it in {via}'s scheduled view too)")
                    save(item)
                else:
                    via = "Blotato"
                return self._redirect(f"/item/{iid}?msg={quote(f'Marked cancelled here. Also delete it in {via}.')}")
            return self._send(404, page("Not found", "<p>Unknown action.</p>"))
        except (ValueError, KeyError) as e:
            return self._redirect(f"/?msg={quote(str(e))}")
        except Exception as e:  # show the error instead of a blank page
            traceback.print_exc()
            return self._send(500, page("Error", f"<div class='card'><h2>Something failed</h2><pre>{esc(e)}</pre><a href='/'>Back</a></div>"))


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=VALUE lines from .env into the environment (existing variables win). Values are never printed."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main(argv=None) -> int:
    import argparse
    load_dotenv()
    ap = argparse.ArgumentParser(description="Clippy Club post desk")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args(argv)
    INBOX.mkdir(exist_ok=True)
    DESK.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), Handler)
    print(f"Post desk on http://127.0.0.1:{a.port}  ({'LIVE' if live_enabled() else 'dry-run'})  inbox: {INBOX}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
