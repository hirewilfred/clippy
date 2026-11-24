import io
import json
from datetime import datetime, timezone

import pytest

from scripts import postiz, publish
from scripts.postiz import Postiz, PostizError, build_payload
from scripts.publish import PublishRefused
from tests.test_gates import CAPTION, T, WARMUP, YT_DESC, YT_TITLE, make

MEDIA = {"id": "m1", "path": "https://uploads.postiz.com/clip.mp4"}


class FakeResp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_tiktok_payload_schedule():
    b = build_payload("tiktok", {"caption": CAPTION}, "int_tt", MEDIA, T)
    assert b["type"] == "schedule" and b["date"] == "2026-11-22T18:00:00.000Z"
    assert b["shortLink"] is False and b["tags"] == []
    post = b["posts"][0]
    assert post["integration"] == {"id": "int_tt"}
    assert post["value"] == [{"content": CAPTION, "image": [MEDIA]}]
    s = post["settings"]
    assert s["__type"] == "tiktok" and s["privacy_level"] == "PUBLIC_TO_EVERYONE"
    assert s["content_posting_method"] == "DIRECT_POST"      # UPLOAD would silently never publish
    assert s["video_made_with_ai"] is False and s["brand_content_toggle"] is False
    assert s["comment"] is True


def test_youtube_payload_now_with_thumbnail():
    clip = {"yt_title": YT_TITLE, "yt_description": YT_DESC}
    now = datetime(2026, 11, 22, 12, tzinfo=timezone.utc)
    b = build_payload("youtube", clip, "int_yt", MEDIA, None, {"id": "t1", "path": "https://u/t.jpg"}, now=now)
    assert b["type"] == "now" and b["date"] == "2026-11-22T12:00:00.000Z"
    s = b["posts"][0]["settings"]
    assert s["__type"] == "youtube" and s["title"] == YT_TITLE and s["type"] == "public"
    assert s["selfDeclaredMadeForKids"] == "no" and s["made_with_ai"] is False
    assert s["thumbnail"] == {"id": "t1", "path": "https://u/t.jpg"}
    assert b["posts"][0]["value"][0]["content"] == YT_DESC


def test_naive_schedule_rejected():
    with pytest.raises(ValueError):
        build_payload("tiktok", {"caption": CAPTION}, "i", MEDIA, datetime(2026, 11, 22, 13))


def test_unknown_backend_refused():
    with pytest.raises(PublishRefused, match="unknown PUBLISHER"):
        publish.publish(make(), "x.mp4", warmup=WARMUP, backend="buffer")


def test_postiz_dry_run_by_default(monkeypatch):
    monkeypatch.setenv("PUBLISHER", "postiz")
    res = publish.publish(make(), "x.mp4", warmup=WARMUP)
    assert res["dry_run"] is True and res["backend"] == "postiz"
    assert res["payload"]["posts"][0]["settings"]["__type"] == "youtube"


def test_postiz_gates_still_refuse(monkeypatch):
    monkeypatch.setenv("PUBLISHER", "postiz")
    with pytest.raises(PublishRefused, match="render hash mismatch"):
        publish.publish(make(render_bytes_sha256="b" * 64), "x.mp4", warmup=WARMUP)


def test_postiz_live_refused_without_env(monkeypatch):
    monkeypatch.delenv("LIVE", raising=False)
    calls = []
    client = Postiz(api_key="k", opener=lambda *a, **k: calls.append(a))
    with pytest.raises(PublishRefused, match="LIVE"):
        publish.publish(make(), "x.mp4", live=True, client=client, warmup=WARMUP, backend="postiz")
    assert calls == []                                       # no network before the gates pass


def test_postiz_live_flow_with_fake_http(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE", "true")
    monkeypatch.delenv("POSTIZ_YOUTUBE_INTEGRATION_ID", raising=False)
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"video")
    calls = []

    def opener(req, timeout=60):
        calls.append((req.get_method(), req.full_url, req.headers.get("Authorization"), req.data,
                      req.headers.get("Content-type")))
        url = req.full_url
        if url.endswith("/integrations"):
            return FakeResp(json.dumps([
                {"id": "tt", "identifier": "tiktok", "profile": "clippyclubgta6"},
                {"id": "yt", "identifier": "youtube", "name": "Clippyclub (ClippyclubGTA6)", "profile": "UCxyz"},
            ]).encode())
        if url.endswith("/upload"):
            return FakeResp(json.dumps(MEDIA).encode())
        if url.endswith("/posts"):
            return FakeResp(json.dumps([{"postId": "p_1", "integration": "yt"}]).encode())
        raise AssertionError(url)

    client = Postiz(api_key="k", base="https://postiz.example/api/public/v1", opener=opener)
    res = publish.publish(make(), vid, live=True, client=client, warmup=WARMUP, backend="postiz")
    assert res["dry_run"] is False and res["backend"] == "postiz" and res["postSubmissionId"] == "p_1"
    assert all(c[2] == "k" for c in calls)                   # raw key, no "Bearer"
    up = [c for c in calls if c[1].endswith("/upload")][0]
    assert up[4].startswith("multipart/form-data; boundary=") and b'filename="clip.mp4"' in up[3]
    body = json.loads([c for c in calls if c[1].endswith("/posts")][0][3])
    assert body["posts"][0]["integration"] == {"id": "yt"}
    assert body["posts"][0]["value"][0]["image"] == [MEDIA]


def test_integration_must_be_unique_handle(monkeypatch):
    monkeypatch.delenv("POSTIZ_TIKTOK_INTEGRATION_ID", raising=False)
    opener = lambda req, timeout=60: FakeResp(json.dumps(
        [{"id": "1", "identifier": "tiktok", "profile": "someoneelse"}]).encode())
    with pytest.raises(PostizError, match="clippyclubgta6"):
        Postiz(api_key="k", opener=opener).integration_id("tiktok")


def test_disabled_channel_is_ignored(monkeypatch):
    monkeypatch.delenv("POSTIZ_TIKTOK_INTEGRATION_ID", raising=False)
    opener = lambda req, timeout=60: FakeResp(json.dumps(
        [{"id": "1", "identifier": "tiktok", "profile": "clippyclubgta6", "disabled": True}]).encode())
    with pytest.raises(PostizError):
        Postiz(api_key="k", opener=opener).integration_id("tiktok")


def test_transport_error_is_publisher_error(monkeypatch, tmp_path):
    monkeypatch.setenv("LIVE", "true")
    vid = tmp_path / "clip.mp4"
    vid.write_bytes(b"v")
    client = Postiz(api_key="", opener=lambda *a, **k: None)
    with pytest.raises(publish.PublisherError, match="POSTIZ_API_KEY is not set"):
        publish.publish(make(), vid, live=True, client=client, warmup=WARMUP, backend="postiz")


def test_find_post_maps_state():
    payload = {"posts": [{"id": "p_1", "state": "PUBLISHED", "releaseURL": "https://youtube.com/shorts/x"}]}
    client = Postiz(api_key="k", opener=lambda req, timeout=60: FakeResp(json.dumps(payload).encode()))
    p = client.find_post("p_1", T)
    assert postiz.STATE_MAP[p["state"]] == "published" and p["releaseURL"].endswith("/x")
    assert client.find_post("nope", T) is None
