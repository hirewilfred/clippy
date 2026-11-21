from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from scripts import gates
from scripts.gates import PostRequest, caption_sha256, check

ET = ZoneInfo("America/Toronto")
WARMUP = {
    "start_date": "2026-11-21",
    "timezone": "America/Toronto",
    "daily_cap_by_day": [
        {"from_day": 1, "to_day": 1, "cap": 0},
        {"from_day": 2, "to_day": 3, "cap": 2},
        {"from_day": 4, "to_day": 6, "cap": 4},
        {"from_day": 7, "to_day": None, "cap": 8},
    ],
    "min_gap_minutes": 150,
    "max_posts_per_hour": 2,
    "profile_edit_quarantine_minutes": 120,
}
SHA = "a" * 64
CAPTION = "lucia already has a record 💀\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #ohnepixel #lucia #gaming"
YT_TITLE = "GTA 6 Lucia already has a record #gta6"
YT_DESC = "Credit: @ohnepixel (Twitch)\ntwitch.tv/ohnepixel\n\nFan page. Not affiliated with Rockstar Games."
T = datetime(2026, 11, 22, 13, 0, tzinfo=ET)


def make(**over) -> PostRequest:
    clip = {
        "id": "c1", "status": "approved", "streamer": "ohnepixel", "render_sha256": SHA,
        "caption": CAPTION, "yt_title": YT_TITLE, "yt_description": YT_DESC,
    }
    clip.update(over.pop("clip", {}))
    approval = {
        "clip_id": "c1", "render_sha256": SHA,
        "caption_sha256": caption_sha256(CAPTION, YT_TITLE, YT_DESC), "revoked_at": None,
    }
    if "approval" in over:
        approval = over.pop("approval")
    base = dict(
        platform="youtube", scheduled_for=T, clip=clip, render_bytes_sha256=SHA, approval=approval,
        streamer={"handle": "ohnepixel", "permission": "credit_only"}, duration_s=15.0,
    )
    base.update(over)
    return PostRequest(**base)


def run(req, live_env=None):
    return check(req, live_env=live_env, warmup=WARMUP)


def test_happy_path_passes():
    assert run(make()) == []
    assert run(make(platform="tiktok")) == []


def test_live_requires_flag():
    assert any("LIVE" in e for e in run(make(live=True)))
    assert run(make(live=True), live_env="true") == []
    assert any("LIVE" in e for e in run(make(live=True), live_env="TRUE"))


def test_clip_must_be_approved_status():
    assert any("status" in e for e in run(make(clip={"status": "queued"})))


def test_missing_or_revoked_approval():
    assert "no approval on record" in run(make(approval=None))
    a = {"clip_id": "c1", "render_sha256": SHA, "caption_sha256": caption_sha256(CAPTION, YT_TITLE, YT_DESC),
         "revoked_at": "2026-11-22T00:00:00Z"}
    assert "approval was revoked" in run(make(approval=a))


def test_video_change_invalidates_approval():
    errs = run(make(render_bytes_sha256="b" * 64))
    assert any("render hash mismatch" in e for e in errs)


def test_caption_change_invalidates_approval():
    errs = run(make(clip={"caption": CAPTION.replace("record", "rap sheet")}))
    assert any("caption hash mismatch" in e for e in errs)


def test_title_change_invalidates_approval():
    errs = run(make(clip={"yt_title": "GTA 6 different title"}))
    assert any("caption hash mismatch" in e for e in errs)


def test_approval_for_other_clip():
    a = {"clip_id": "OTHER", "render_sha256": SHA,
         "caption_sha256": caption_sha256(CAPTION, YT_TITLE, YT_DESC), "revoked_at": None}
    assert "approval belongs to a different clip" in run(make(approval=a))


def test_streamer_rules():
    assert any("blocked" in e for e in run(make(streamer={"handle": "ohnepixel", "permission": "blocked"})))
    assert "streamer not in `streamers` table" in run(make(streamer=None))
    assert run(make(streamer={"handle": "ohnepixel", "permission": "allowlisted"})) == []


@pytest.mark.parametrize("caption,needle", [
    ("funny moment\n\n#gta6 #a #b", "missing credit line"),
    ("funny moment\n\nCredit: @someoneelse (Twitch)\n\n#gta6 #a #b", "does not name the clip's streamer"),
    ("funny moment\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a", "2 hashtags"),
    ("funny moment\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b #c #d #e", "6 hashtags"),
    ("did he really do that?\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b", "ends with a question"),
    ("official gta 6 clip\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b", "banned word 'official'"),
    ("gta 6 leak moment\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b", "banned word 'leak'"),
    ("lucia — legend\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b", "em/en dash"),
    ("this is way too many words for one caption\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b", "words"),
    ("lol 💀😂\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b", "more than one emoji"),
    ("same tag\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #GTA6 #b", "duplicate hashtags"),
])
def test_caption_rules(caption, needle):
    req = make(clip={"caption": caption}, platform="tiktok")
    assert any(needle in e for e in gates.gate_caption(req)), gates.gate_caption(req)


def test_leaky_is_not_leak():
    req = make(clip={"caption": "gta 6 leaky roof\n\nCredit: @ohnepixel (Twitch)\n\n#gta6 #a #b"}, platform="tiktok")
    assert not any("banned" in e for e in gates.gate_caption(req))


def test_youtube_fields():
    req = make(clip={"yt_title": "x" * 61})
    assert any("max 60" in e for e in gates.gate_youtube_fields(req))
    req = make(clip={"yt_description": "Credit: @ohnepixel (Twitch)"})
    assert any("disclaimer" in e for e in gates.gate_youtube_fields(req))
    req = make(clip={"yt_description": "Fan page. Not affiliated with Rockstar Games."})
    assert any("credit" in e for e in gates.gate_youtube_fields(req))
    # TikTok ignores YouTube fields
    assert gates.gate_youtube_fields(make(platform="tiktok", clip={"yt_title": ""})) == []


def test_duration():
    assert run(make(duration_s=11.9))
    assert run(make(duration_s=20.1))
    assert run(make(duration_s=None))
    assert run(make(duration_s=12.0)) == []


def test_ledger_blocks_double_post():
    posts = [{"clip_id": "c1", "status": "scheduled", "scheduled_for": T - timedelta(days=1)}]
    assert any("already posted" in e for e in run(make(platform_posts=posts)))
    posts = [{"clip_id": "c1", "status": "failed", "scheduled_for": T - timedelta(days=1)}]
    assert run(make(platform_posts=posts)) == []


def test_pause_blocks():
    p = [{"platform": "youtube", "paused_until": T + timedelta(hours=1), "reason": "2 stuck"}]
    assert any("paused" in e for e in run(make(pauses=p)))
    p = [{"platform": "all", "paused_until": (T + timedelta(hours=1)).isoformat(), "reason": "both stuck"}]
    assert any("paused" in e for e in run(make(platform="tiktok", pauses=p)))
    p = [{"platform": "tiktok", "paused_until": T + timedelta(hours=1)}]
    assert run(make(pauses=p)) == []          # other platform unaffected
    p = [{"platform": "youtube", "paused_until": T - timedelta(minutes=1)}]
    assert run(make(pauses=p)) == []          # expired


def test_profile_quarantine():
    assert any("profile edit" in e for e in run(make(last_profile_edit=T - timedelta(minutes=119))))
    assert run(make(last_profile_edit=T - timedelta(minutes=121))) == []


def test_warmup_caps():
    day1 = datetime(2026, 11, 21, 15, 0, tzinfo=ET)
    assert gates.daily_cap(WARMUP, day1) == 0
    assert any("daily cap" in e for e in run(make(scheduled_for=day1)))
    assert gates.daily_cap(WARMUP, T) == 2
    assert gates.daily_cap(WARMUP, datetime(2026, 11, 24, 9, tzinfo=ET)) == 4
    assert gates.daily_cap(WARMUP, datetime(2027, 1, 1, 9, tzinfo=ET)) == 8
    assert gates.daily_cap(WARMUP, datetime(2026, 11, 20, 9, tzinfo=ET)) == 0


def test_daily_cap_counts_other_posts_same_local_day():
    posts = [
        {"clip_id": "x1", "status": "scheduled", "scheduled_for": datetime(2026, 11, 22, 4, 30, tzinfo=ET)},
        {"clip_id": "x2", "status": "scheduled", "scheduled_for": datetime(2026, 11, 22, 8, 0, tzinfo=ET)},
    ]
    assert any("daily cap" in e for e in run(make(platform_posts=posts)))


def test_min_gap():
    posts = [{"clip_id": "x1", "status": "scheduled", "scheduled_for": T - timedelta(minutes=149)}]
    assert any("within" in e for e in run(make(platform_posts=posts)))
    posts = [{"clip_id": "x1", "status": "scheduled", "scheduled_for": T - timedelta(minutes=150)}]
    assert run(make(platform_posts=posts)) == []


def test_naive_datetime_rejected():
    assert run(make(scheduled_for=datetime(2026, 11, 22, 13, 0))) == ["scheduled_for must be timezone-aware"]


def test_real_warmup_config_loads():
    w = gates.load_warmup()
    assert gates.daily_cap(w, datetime(2026, 11, 22, 12, tzinfo=ET)) == 2
