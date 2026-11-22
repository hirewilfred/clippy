import json
import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import clip
from scripts.clip import build_timeline, map_interval, validate

BASE = {
    "src": "src.mp4", "out": "out.mp4", "credit": "@ohnepixel (Twitch)", "layout": "stack",
    "facecam": [1440, 0, 480, 360], "game": [0, 0, 1920, 1080],
    "teaser": {"start": 24.6, "end": 27.4}, "teaser_text": "GTA 6 planes have zero chill",
    "keep": [[14.8, 27.8]],
}


def spec(**over):
    s = json.loads(json.dumps(BASE))
    s.update(over)
    return s


def test_timeline_plain():
    segs = build_timeline(spec())
    assert [s.kind for s in segs] == ["teaser", "keep"]
    assert segs[0].out_dur == pytest.approx(2.8)
    assert segs[-1].out_end == pytest.approx(2.8 + 13.0)


def test_timeline_slowmo_splits_keep():
    segs = build_timeline(spec(effect={"type": "slowmo", "start": 24.6, "end": 25.6, "factor": 0.5}))
    assert [round(s.speed, 2) for s in segs] == [1.0, 1.0, 0.5, 1.0]
    assert segs[-1].out_end == pytest.approx(2.8 + 9.8 + 2.0 + 2.2)


def test_timeline_freeze_inserts_hold():
    segs = build_timeline(spec(effect={"type": "freeze", "at": 25.1, "duration": 0.8}))
    assert [s.kind for s in segs] == ["teaser", "keep", "freeze", "keep"]
    assert segs[2].out_dur == pytest.approx(0.8)
    assert segs[-1].out_end == pytest.approx(2.8 + 13.0 + 0.8)


def test_map_interval_skips_teaser_and_scales_slowmo():
    segs = build_timeline(spec(effect={"type": "slowmo", "start": 24.6, "end": 25.6, "factor": 0.5}))
    # 16.0-18.5 source -> 2.8 + 1.2 .. 2.8 + 3.7
    assert map_interval(segs, 16.0, 18.5) == [pytest.approx((4.0, 6.5))]
    # 25.0-26.0 crosses slow-mo boundary: 25.0-25.6 at 0.5x (1.2s) then 25.6-26.0 at 1x
    (a, b), = map_interval(segs, 25.0, 26.0)
    assert a == pytest.approx(2.8 + 9.8 + 0.8) and b == pytest.approx(2.8 + 9.8 + 2.0 + 0.4)
    # teaser footage is excluded unless asked
    assert all(a >= 2.8 for a, _ in map_interval(segs, 24.6, 27.4))
    assert map_interval(segs, 24.6, 27.4, include_teaser=True)[0][0] == pytest.approx(0.0)


def test_validate_ok():
    assert validate(spec(), (1920, 1080), 30.0) == []


@pytest.mark.parametrize("over,needle", [
    ({"teaser": {"start": 24.0, "end": 25.0}}, "teaser is"),
    ({"keep": [[14.8, 18.0]]}, "output is"),
    ({"keep": [[0.0, 27.8]]}, "output is"),
    ({"facecam": [1700, 0, 480, 360]}, "outside the source frame"),
    ({"teaser_text": "LOL 💀"}, "emoji"),
    ({"effect": {"type": "zoom"}}, "effect type"),
    ({"effect": {"type": "slowmo", "start": 25, "end": 26, "factor": 0.1}}, "slowmo factor"),
    ({"effect": {"type": "freeze", "at": 25, "duration": 3}}, "freeze duration"),
    ({"punchlines": [{"start": 1.0, "end": 2.0, "text": "NOPE"}]}, "never appears"),
])
def test_validate_catches(over, needle):
    errs = validate(spec(**over), (1920, 1080), 30.0)
    assert any(needle in e for e in errs), errs


def test_validate_source_too_short():
    assert any("source is" in e for e in validate(spec(), (1920, 1080), 20.0))


def test_srt_parse(tmp_path):
    p = tmp_path / "s.srt"
    p.write_text("1\n00:00:16,000 --> 00:00:18,500\nno way he\njust did that\n\n2\n00:01:02,250 --> 00:01:03,000\nbro\n")
    cues = clip.parse_srt(p)
    assert cues[0] == {"start": 16.0, "end": 18.5, "text": "no way he just did that"}
    assert cues[1]["start"] == pytest.approx(62.25)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_render_end_to_end(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=640x360:r=30:d=20",
                    "-f", "lavfi", "-i", "sine=f=440:d=20", "-c:v", "libx264", "-preset", "ultrafast",
                    "-c:a", "aac", "-shortest", str(src)], check=True)
    s = spec(src=str(src), out=str(tmp_path / "out.mp4"), facecam=[480, 0, 160, 120], game=[0, 0, 640, 360],
             teaser={"start": 15.0, "end": 17.5}, keep=[[4.0, 17.0]],
             effect={"type": "freeze", "at": 15.5, "duration": 0.6},
             punchlines=[{"start": 15.8, "end": 17.0, "text": "CALL DROPPED"}])
    res = clip.render(s, tmp_path, preview=True)
    assert res["duration_s"] == pytest.approx(2.5 + 13.0 + 0.6, abs=0.1)
    assert len(res["sha256"]) == 64 and Path(res["preview"]).exists()
    wh, _, has_audio = clip.probe(res["out"])
    assert wh == (1080, 1920) and has_audio


def test_render_rejects_bad_spec(tmp_path):
    with pytest.raises(clip.SpecError):
        clip.render(spec(src=str(tmp_path / "missing.mp4")), tmp_path)
