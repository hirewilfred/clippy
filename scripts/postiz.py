"""postiz.py: Postiz publisher backend (open-source scheduler, cloud or self-hosted).

publish.py picks this backend when PUBLISHER=postiz. It is called only after
gates.check passes; nothing in this module bypasses the gates.

Postiz Public API (docs.postiz.com/public-api):
  base          https://api.postiz.com/public/v1  (cloud)
                https://<your-domain>/api/public/v1 (self-hosted)  -> POSTIZ_API_URL
  auth header   Authorization: <api key>            (no "Bearer" prefix)
  channels      GET    /integrations -> [{id, name, identifier, profile, disabled}]
  upload        POST   /upload (multipart, field "file") -> {id, path, status}
  post          POST   /posts {type: now|schedule|draft, date, shortLink, tags, posts:[...]}
                -> [{postId, integration}]
  list          GET    /posts?startDate=&endDate= -> {posts:[{id, publishDate, state, releaseURL, error, integration}]}
  delete        DELETE /posts/{id}   (deletes the whole group; 404 = already gone)
Rate limits: create-post about 90-100/hour; other endpoints about 30/hour.
The API key is read from POSTIZ_API_KEY and never printed.
"""
from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

CLOUD_BASE = "https://api.postiz.com/public/v1"
HANDLE = "clippyclubgta6"

# Postiz state -> the status words desk.py already uses.
STATE_MAP = {"QUEUE": "scheduled", "PUBLISHED": "published", "ERROR": "failed", "DRAFT": "draft"}


class PostizError(RuntimeError):
    pass


# ---------------------------------------------------------------- payloads (pure)

def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def build_payload(platform: str, clip: dict, integration_id: str, media: dict,
                  scheduled_for: datetime | None, thumbnail: dict | None = None,
                  now: datetime | None = None) -> dict:
    """One Postiz create-post body for one platform. `media` and `thumbnail` are {id, path} from /upload."""
    if scheduled_for is not None and scheduled_for.tzinfo is None:
        raise ValueError("scheduled_for must be timezone-aware")
    if platform == "tiktok":
        text = clip["caption"]
        settings = {
            "__type": "tiktok",
            "title": "",
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "duet": True,
            "stitch": True,
            "comment": True,                    # playbook: comments on
            "autoAddMusic": "no",
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "video_made_with_ai": False,        # real gameplay; never label as AI
            "content_posting_method": "DIRECT_POST",   # UPLOAD reports success but never publishes
        }
    elif platform == "youtube":
        text = clip["yt_description"]
        settings = {
            "__type": "youtube",
            "title": clip["yt_title"],
            "type": "public",
            "selfDeclaredMadeForKids": "no",
            "made_with_ai": False,
            "tags": [],
        }
        if thumbnail:
            settings["thumbnail"] = {"id": thumbnail["id"], "path": thumbnail["path"]}
    else:
        raise ValueError(f"unknown platform {platform}")
    when = scheduled_for or now or datetime.now(timezone.utc)
    return {
        "type": "schedule" if scheduled_for is not None else "now",
        "date": _utc(when),                     # required even for "now"; ignored by Postiz then
        "shortLink": False,
        "tags": [],
        "posts": [{
            "integration": {"id": str(integration_id)},
            "value": [{"content": text, "image": [{"id": media["id"], "path": media["path"]}]}],
            "settings": settings,
        }],
    }


def multipart(field: str, filename: str, data: bytes, ctype: str) -> tuple[bytes, str]:
    boundary = f"----clippy{uuid.uuid4().hex}"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode()
    return head + data + f"\r\n--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


# ---------------------------------------------------------------- HTTP client

class Postiz:
    def __init__(self, api_key: str | None = None, base: str | None = None, opener=None):
        self.key = api_key or os.environ.get("POSTIZ_API_KEY") or ""
        self.base = (base or os.environ.get("POSTIZ_API_URL") or CLOUD_BASE).rstrip("/")
        self.open = opener or urllib.request.urlopen

    def _send(self, req: urllib.request.Request, timeout: int = 60):
        if not self.key:
            raise PostizError("POSTIZ_API_KEY is not set")
        req.add_header("Authorization", self.key)
        try:
            with self.open(req, timeout=timeout) as r:
                raw = r.read()
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:300]
            raise PostizError(f"{req.get_method()} {urllib.parse.urlparse(req.full_url).path} -> HTTP {e.code}: {detail}") from None

    def _req(self, method: str, path: str, body: dict | None = None, timeout: int = 60):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(f"{self.base}{path}", data=data, method=method,
                                     headers={"Content-Type": "application/json"})
        return self._send(req, timeout)

    def integrations(self) -> list[dict]:
        r = self._req("GET", "/integrations")
        return r if isinstance(r, list) else r.get("integrations", [])

    def integration_id(self, platform: str, handle: str = HANDLE) -> str:
        env = os.environ.get(f"POSTIZ_{platform.upper()}_INTEGRATION_ID")
        if env:
            return env
        match = [i for i in self.integrations()
                 if i.get("identifier") == platform and not i.get("disabled")
                 and ((i.get("profile") or "").lstrip("@").lower() == handle
                      or handle in (i.get("name") or "").lower())]
        if len(match) != 1:
            found = [f"{i.get('identifier')}:{i.get('profile') or i.get('name')}" for i in self.integrations()]
            raise PostizError(f"expected exactly one {platform} channel '@{handle}', found {found}")
        return str(match[0]["id"])

    def upload(self, path: str | Path) -> dict:
        path = Path(path)
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body, ct = multipart("file", path.name, path.read_bytes(), ctype)
        req = urllib.request.Request(f"{self.base}/upload", data=body, method="POST", headers={"Content-Type": ct})
        r = self._send(req, timeout=600)
        if not r.get("id") or not r.get("path"):
            raise PostizError("upload returned no id/path")
        return {"id": r["id"], "path": r["path"]}

    def create_post(self, body: dict) -> list[dict]:
        r = self._req("POST", "/posts", body)
        return r if isinstance(r, list) else [r]

    def list_posts(self, start: datetime, end: datetime) -> list[dict]:
        q = urllib.parse.urlencode({"startDate": _utc(start), "endDate": _utc(end)})
        return self._req("GET", f"/posts?{q}").get("posts", [])

    def find_post(self, post_id: str, around: datetime) -> dict | None:
        for p in self.list_posts(around - timedelta(days=2), around + timedelta(days=2)):
            if p.get("id") == post_id:
                return p
        return None

    def delete_post(self, post_id: str) -> None:
        try:
            self._req("DELETE", f"/posts/{urllib.parse.quote(post_id)}")
        except PostizError as e:
            if "HTTP 404" not in str(e):          # 404 = already deleted
                raise
