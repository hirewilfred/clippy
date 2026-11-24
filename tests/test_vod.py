import json

import pytest

from scripts import vod
from scripts.vod import VodError, download_cmd, parse_section, parse_url, streamer_errors

META = {"title": "GTA 6 day 3", "streamer": "squeex", "streamer_display": "Squeex", "duration_s": 4 * 3600}


def test_parse_url_accepts_vods():
    assert parse_url("https://www.twitch.tv/videos/2301234567") == ("twitch", "2301234567")
    assert parse_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5") == ("youtube", "dQw4w9WgXcQ")
    assert parse_url("https://youtu.be/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")
    assert parse_url("https://www.youtube.com/live/dQw4w9WgXcQ") == ("youtube", "dQw4w9WgXcQ")


@pytest.mark.parametrize("url", ["https://www.twitch.tv/squeex", "https://clips.twitch.tv/FunnyClip",
                                 "https://www.twitch.tv/squeex/clip/FunnyClip", "https://example.com/v.mp4"])
def test_parse_url_refuses_non_vods(url):
    with pytest.raises(VodError):
        parse_url(url)


def test_parse_section():
    assert parse_section("01:10:00-01:25:00") == (4200, 5100)
    assert parse_section("10:00-12:30") == (600, 750)
    for bad in ("01:00:00", "5:00-4:00", "00:00:00-00:31:00", "aa-bb"):
        with pytest.raises(VodError):
            parse_section(bad)


def test_blocked_streamer_refused():
    permitted = {"streamers": [{"handle": "Squeex", "permission": "blocked"}]}
    assert streamer_errors("squeex", permitted) == ["@squeex is blocked"]
    assert streamer_errors("xqc", permitted) == []
    assert streamer_errors(None, permitted)                  # unknown streamer = no credit = refuse


def test_download_cmd_uses_sections():
    cmd = download_cmd("https://www.twitch.tv/videos/1", 4200, 5100, vod.OUT_DIR / "x.mp4")
    assert cmd[0] == "yt-dlp" and "--no-playlist" in cmd
    assert cmd[cmd.index("--download-sections") + 1] == "*01:10:00-01:25:00"


def test_fetch_dry_run_runs_nothing(tmp_path):
    ran = []
    res = vod.fetch("https://www.twitch.tv/videos/99", "00:10:00-00:20:00", dry_run=True, out_dir=tmp_path,
                    permitted={"streamers": []}, meta_fn=lambda u: META, run=lambda *a, **k: ran.append(a))
    assert res["dry_run"] is True and res["streamer"] == "squeex" and ran == []
    assert not list(tmp_path.iterdir())


def test_fetch_blocked_never_downloads(tmp_path):
    ran = []
    with pytest.raises(VodError, match="blocked"):
        vod.fetch("https://www.twitch.tv/videos/99", "00:10:00-00:20:00", out_dir=tmp_path,
                  permitted={"streamers": [{"handle": "squeex", "permission": "blocked"}]},
                  meta_fn=lambda u: META, run=lambda *a, **k: ran.append(a))
    assert ran == []


def test_fetch_section_past_end(tmp_path):
    with pytest.raises(VodError, match="VOD is"):
        vod.fetch("https://www.twitch.tv/videos/99", "04:10:00-04:20:00", out_dir=tmp_path,
                  permitted={"streamers": []}, meta_fn=lambda u: META)


def test_fetch_writes_sidecar(tmp_path):
    res = vod.fetch("https://www.twitch.tv/videos/99", "00:10:00-00:20:00", out_dir=tmp_path,
                    permitted={"streamers": []}, meta_fn=lambda u: META, run=lambda *a, **k: None)
    side = json.loads((tmp_path / "twitch-99_600-1200.json").read_text())
    assert side["section"] == [600, 1200] and side["streamer"] == "squeex" and res["file"].endswith(".mp4")
