from datetime import datetime, timedelta, timezone

from scripts.scout import evaluate
from scripts.sheet import clip_id

CFG = {"game_category": "Grand Theft Auto VI", "launch_utc": "2026-11-18T11:00:00Z", "max_age_hours": 24,
       "min_source_seconds": 14, "max_source_seconds": 60, "min_views": 500}
NOW = datetime(2026, 11, 21, 18, 0, tzinfo=timezone.utc)


def meta(**o):
    m = {"category": "Grand Theft Auto VI", "created_at": (NOW - timedelta(hours=3)).isoformat(),
         "duration_s": 25, "views": 2000, "title": "he lost all of it"}
    m.update(o)
    return m


def test_good_clip_passes():
    assert evaluate(meta(), CFG, NOW) == []


def test_rejections():
    assert evaluate(meta(category="Grand Theft Auto V"), CFG, NOW)
    assert evaluate(meta(created_at=(NOW - timedelta(hours=25)).isoformat()), CFG, NOW)
    assert evaluate(meta(created_at="2026-11-17T10:00:00+00:00"), CFG, NOW)
    assert evaluate(meta(duration_s=6), CFG, NOW)
    assert evaluate(meta(duration_s=90), CFG, NOW)
    assert evaluate(meta(views=100), CFG, NOW)
    assert evaluate(meta(title="ohne tries the strip club"), CFG, NOW)
    assert evaluate(meta(title="GTA 6 ENDING reaction"), CFG, NOW)
    assert evaluate(meta(created_at=None), CFG, NOW)


def test_clip_id():
    assert clip_id("https://www.twitch.tv/lacy/clip/RichVainScallion-AYQx_8") == "RichVainScallion-AYQx_8"
    assert clip_id("https://clips.twitch.tv/AbcDef-12") == "AbcDef-12"
