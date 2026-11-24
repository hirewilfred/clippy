import io
import json
from datetime import datetime, timedelta, timezone

import pytest

from scripts import publish
from scripts.gates import caption_sha256
from scripts.publish import Blotato, PublishRefused, build_payload
from tests.test_gates import CAPTION, SHA, T, WARMUP, YT_DESC, YT_TITLE, make


def test_tiktok_payload():
    b = build_payload("tiktok", {"caption": CAPTION}, "111", "https://m/v.mp4", T)
    assert b["post"]["accountId"] == "111"
    assert b["post"]["content"] == {"text": CAPTION, "mediaUrls": ["https://m/v.mp4"], "platform": "tiktok"}
    t = b["post"]["target"]
    assert t["targetType"] == "tiktok" and t["privacyLevel"] == "PUBLIC_TO_EVERYONE"
    assert t["isAiGenerated"] is False and t["isBrandedContent"] is False
    assert b["scheduledTime"] == "2026-11-22T18:00:00Z"   # 1 PM ET in UTC, at root level
    assert "scheduledTime" not in b["post"]


def test_youtube_payload_and_thumbnail():
    clip = {"yt_title": YT_TITLE, "yt_description": YT_DESC}
    b = build_payload("youtube", clip, "222", "https://m/v.mp4", None, "https://m/t.jpg")
    t = b["post"]["target"]
    assert t["title"] == YT_TITLE and t["privacyStatus"] == "public"
    assert t["isMadeForKids"] is False and t["containsSyntheticMedia"] is False
    assert t["thumbnailUrl"] == "https://m/t.jpg"
    assert b["post"]["content"]["text"] == YT_DESC
    assert "scheduledTime" not in b                        # post now


def test_naive_schedule_rejected():
    with pytest.raises(ValueError):
        build_payload("tiktok", {"caption": CAPTION}, "1", "u", datetime(2026, 11, 22, 13))


def test_dry_run_by_default():
    res = publish.publish(make(), "x.mp4", warmup=WARMUP)
    assert res["dry_run"] is True and res["payload"]["post"]["target"]["targetType"] == "youtube"


def test_live_refused_without_env(monkeypatch):
    monkeypatch.delenv("LIVE", raising=False)
    with pytest.raises(PublishRefused, match="LIVE"):
        publish.publish(make(), "x.mp4", live=True, warmup=WARMUP)


def test_gate_failure_refuses_even_dry_run():
    with pytest.raises(PublishRefused, match="render hash mismatch"):
        publish.publish(make(render_bytes_sha256="b" * 64), "x.mp4", warmup=WARMUP)


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_live_flow_with_fake_http(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE", "true")
    monkeypatch.delenv("BLOTATO_YOUTUBE_ACCOUNT_ID", raising=False)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"video")
    calls = []

    def opener(req, timeout=60):
        calls.append((req.get_method(), req.full_url, req.headers.get("Blotato-api-key"), req.data))
        url = req.full_url
        if "/users/me/accounts" in url:
            return FakeResp(json.dumps({"items": [{"id": "77", "username": "clippyclubgta6"}]}).encode())
        if url.endswith("/media/uploads"):
            return FakeResp(json.dumps({"presignedUrl": "https://s3/put", "publicUrl": "https://cdn/clip.mp4"}).encode())
        if url == "https://s3/put":
            return FakeResp(b"")
        if url.endswith("/posts"):
            return FakeResp(json.dumps({"postSubmissionId": "ps_1", "scheduledTime": "2026-11-22T18:00:00Z"}).encode())
        raise AssertionError(url)

    client = Blotato(api_key="k", opener=opener)
    res = publish.publish(make(), vid, live=True, client=client, warmup=WARMUP)
    assert res == {"dry_run": False, "backend": "blotato", "platform": "youtube", "postSubmissionId": "ps_1",
                   "scheduledTime": "2026-11-22T18:00:00Z"}
    post = [c for c in calls if c[1].endswith("/posts")][0]
    body = json.loads(post[3])
    assert body["post"]["accountId"] == "77" and body["post"]["content"]["mediaUrls"] == ["https://cdn/clip.mp4"]
    assert all(c[2] == "k" for c in calls if "blotato" in c[1])
    assert not any(c[2] for c in calls if "s3" in c[1])        # key never sent to the upload host


def test_account_must_be_unique_handle(monkeypatch):
    monkeypatch.delenv("BLOTATO_TIKTOK_ACCOUNT_ID", raising=False)

    def opener(req, timeout=60):
        return FakeResp(json.dumps({"items": [{"id": "1", "username": "someoneelse"}]}).encode())

    with pytest.raises(publish.BlotatoError, match="clippyclubgta6"):
        Blotato(api_key="k", opener=opener).account_id("tiktok")


def test_missing_key():
    with pytest.raises(publish.BlotatoError, match="not set"):
        Blotato(api_key="", opener=lambda *a, **k: None)._req("GET", "/x")
