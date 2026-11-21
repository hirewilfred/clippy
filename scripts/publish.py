"""publish.py: send an approved clip to TikTok / YouTube through Blotato.

Every call runs gates.check first. Refuses on any failure. Dry-run unless the
caller passes live=True AND the LIVE env var is exactly "true".

Blotato REST API v2 (help.blotato.com/rest-api-reference):
  auth header   blotato-api-key
  accounts      GET  /v2/users/me/accounts?platform=tiktok|youtube  -> items[].id
  upload        POST /v2/media/uploads {filename} -> {presignedUrl, publicUrl}; PUT bytes to presignedUrl
  post          POST /v2/posts {post:{accountId, content:{text, mediaUrls, platform}, target:{...}}, scheduledTime?}
                -> {postSubmissionId, scheduledTime?}
  status        GET  /v2/posts/{postSubmissionId} -> status in-progress|scheduled|published|failed
  cancel        DELETE /v2/schedules/{id}
The API key is read from BLOTATO_API_KEY and never printed.
"""
from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from scripts.gates import PostRequest, check

BASE = "https://backend.blotato.com/v2"
HANDLE = "clippyclub67"


class PublishRefused(RuntimeError):
    pass


class BlotatoError(RuntimeError):
    pass


# ---------------------------------------------------------------- payloads (pure)

def build_payload(platform: str, clip: dict, account_id: str, media_url: str,
                  scheduled_for: datetime | None, thumbnail_url: str | None = None) -> dict:
    if platform == "tiktok":
        content_text = clip["caption"]
        target = {
            "targetType": "tiktok",
            "privacyLevel": "PUBLIC_TO_EVERYONE",
            "disabledComments": False,
            "disabledDuet": False,
            "disabledStitch": False,
            "isBrandedContent": False,
            "isYourBrand": False,
            "isAiGenerated": False,          # real gameplay; never label as AI
        }
    elif platform == "youtube":
        content_text = clip["yt_description"]
        target = {
            "targetType": "youtube",
            "title": clip["yt_title"],
            "privacyStatus": "public",
            "shouldNotifySubscribers": True,
            "isMadeForKids": False,
            "containsSyntheticMedia": False,
        }
        if thumbnail_url:
            target["thumbnailUrl"] = thumbnail_url
    else:
        raise ValueError(f"unknown platform {platform}")
    body = {"post": {"accountId": str(account_id),
                     "content": {"text": content_text, "mediaUrls": [media_url], "platform": platform},
                     "target": target}}
    if scheduled_for is not None:
        if scheduled_for.tzinfo is None:
            raise ValueError("scheduled_for must be timezone-aware")
        body["scheduledTime"] = scheduled_for.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return body


# ---------------------------------------------------------------- HTTP client

class Blotato:
    def __init__(self, api_key: str | None = None, base: str = BASE, opener=None):
        self.key = api_key or os.environ.get("BLOTATO_API_KEY") or ""
        self.base = base
        self.open = opener or urllib.request.urlopen

    def _req(self, method: str, path: str, body: dict | None = None) -> dict:
        if not self.key:
            raise BlotatoError("BLOTATO_API_KEY is not set")
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method,
                                     headers={"blotato-api-key": self.key, "Content-Type": "application/json"})
        try:
            with self.open(req, timeout=60) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise BlotatoError(f"{method} {path} -> HTTP {e.code}: {detail}") from None

    def accounts(self, platform: str) -> list[dict]:
        return self._req("GET", f"/users/me/accounts?platform={platform}").get("items", [])

    def account_id(self, platform: str, handle: str = HANDLE) -> str:
        env = os.environ.get(f"BLOTATO_{platform.upper()}_ACCOUNT_ID")
        if env:
            return env
        items = self.accounts(platform)
        match = [a for a in items if (a.get("username") or "").lstrip("@").lower() == handle]
        if len(match) != 1:
            names = [a.get("username") for a in items]
            raise BlotatoError(f"expected exactly one {platform} account '@{handle}', found {names}")
        return str(match[0]["id"])

    def upload(self, path: str | Path) -> str:
        path = Path(path)
        r = self._req("POST", "/media/uploads", {"filename": path.name})
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        put = urllib.request.Request(r["presignedUrl"], data=path.read_bytes(), method="PUT",
                                     headers={"Content-Type": ctype})
        try:
            with self.open(put, timeout=600):
                pass
        except urllib.error.HTTPError as e:
            raise BlotatoError(f"upload PUT failed: HTTP {e.code}") from None
        return r["publicUrl"]

    def create_post(self, body: dict) -> dict:
        return self._req("POST", "/posts", body)

    def post_status(self, submission_id: str) -> dict:
        return self._req("GET", f"/posts/{submission_id}")

    def schedules(self) -> list[dict]:
        return self._req("GET", "/schedules?limit=50").get("items", [])

    def cancel_schedule(self, schedule_id: str) -> None:
        self._req("DELETE", f"/schedules/{schedule_id}")


# ---------------------------------------------------------------- publish

def publish(req: PostRequest, video_path: str | Path, thumb_path: str | Path | None = None,
            live: bool = False, client: Blotato | None = None, warmup: dict | None = None,
            post_now: bool = False) -> dict:
    """Gate, then dry-run or post. Returns a result dict; raises PublishRefused on gate failure."""
    req.live = live
    errs = check(req, live_env=os.environ.get("LIVE"), warmup=warmup)
    if errs:
        raise PublishRefused("; ".join(errs))
    when = None if post_now else req.scheduled_for
    if not live:
        body = build_payload(req.platform, req.clip, "<account id>", "<uploaded video url>", when,
                             "<uploaded thumbnail url>" if thumb_path and req.platform == "youtube" else None)
        return {"dry_run": True, "platform": req.platform, "payload": body}
    client = client or Blotato()
    account = client.account_id(req.platform)
    media_url = client.upload(video_path)
    thumb_url = client.upload(thumb_path) if thumb_path and req.platform == "youtube" else None
    body = build_payload(req.platform, req.clip, account, media_url, when, thumb_url)
    res = client.create_post(body)
    return {"dry_run": False, "platform": req.platform, "postSubmissionId": res.get("postSubmissionId"),
            "scheduledTime": res.get("scheduledTime")}
