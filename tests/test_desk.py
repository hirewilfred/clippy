"""Desk logic on throwaway data in a temp folder (never the real desk)."""
import json
import shutil
import subprocess
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from scripts import desk, gates

ET = ZoneInfo("America/Toronto")
WHEN = datetime(2026, 11, 24, 10, 30, tzinfo=ET)   # day 4: cap 4


@pytest.fixture
def tmp_desk(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    monkeypatch.setattr(desk, "INBOX", tmp_path / "inbox")
    monkeypatch.setattr(desk, "DESK", tmp_path / "desk")
    monkeypatch.delenv("LIVE", raising=False)
    (tmp_path / "inbox").mkdir()
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=270x480:r=30:d=15",
                    "-f", "lavfi", "-i", "sine=d=15", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-shortest", str(tmp_path / "inbox" / "t.mp4")], check=True)
    return tmp_path


def errs(item, plat="youtube", when=WHEN):
    return [e for e in gates.check(desk.post_request(item, plat, when)) if not e.startswith("LIVE")]


def test_add_prefills_and_moves_file(tmp_desk):
    item = desk.add_item("t.mp4", "@Squeex", "GTA 6 wheelies have consequences")
    assert item["streamer"] == "squeex"
    assert not (tmp_desk / "inbox" / "t.mp4").exists()
    assert "Credit: @squeex (Twitch)" in item["caption"] and "Rockstar" in item["yt_description"]
    assert (tmp_desk / "desk" / item["id"] / item["thumbs"]["landscape"]).exists()


def test_add_rejects_bad_input(tmp_desk):
    with pytest.raises(ValueError):
        desk.add_item("../../etc/passwd", "squeex", "x")
    with pytest.raises(ValueError):
        desk.add_item("t.mp4", "bad handle!", "x")


def test_unapproved_is_blocked_then_approval_unblocks(tmp_desk):
    item = desk.add_item("t.mp4", "squeex", "GTA 6 wheelies have consequences")
    desk.save_text(item, "gta 6 said no wheelies\n\nCredit: @squeex (Twitch)\n\n#gta6 #squeex #gaming",
                   item["yt_title"], item["yt_description"])
    assert "no approval on record" in errs(item)
    desk.approve_item(item)
    assert desk.approval_state(item) == "valid"
    assert errs(item) == [] and errs(item, "tiktok") == []


def test_edit_after_approval_voids_it(tmp_desk):
    item = desk.add_item("t.mp4", "squeex", "GTA 6 wheelies")
    desk.save_text(item, "gta 6 said no\n\nCredit: @squeex (Twitch)\n\n#gta6 #squeex #gaming",
                   item["yt_title"], item["yt_description"])
    desk.approve_item(item)
    desk.save_text(item, item["caption"].replace("said no", "said nope"), item["yt_title"], item["yt_description"])
    assert item["approval"]["revoked_at"]
    assert desk.approval_state(item) == "none"
    assert errs(item)


def test_swapped_video_blocks(tmp_desk):
    item = desk.add_item("t.mp4", "squeex", "GTA 6 wheelies")
    desk.save_text(item, "gta 6 said no\n\nCredit: @squeex (Twitch)\n\n#gta6 #squeex #gaming",
                   item["yt_title"], item["yt_description"])
    desk.approve_item(item)
    desk.save(item)
    (tmp_desk / "desk" / item["id"] / "clip.mp4").write_bytes(b"different video")
    assert any("render hash mismatch" in e for e in errs(item))
    with pytest.raises(ValueError):
        desk.approve_item(item)


def test_dry_run_post_records_payload_and_does_not_count(tmp_desk):
    item = desk.add_item("t.mp4", "squeex", "GTA 6 wheelies")
    desk.save_text(item, "gta 6 said no\n\nCredit: @squeex (Twitch)\n\n#gta6 #squeex #gaming",
                   item["yt_title"], item["yt_description"])
    desk.approve_item(item)
    desk.save(item)
    msg = desk.do_post(item["id"], ["tiktok", "youtube"], "schedule", "2026-11-24T10:30")
    assert "dry-run OK" in msg
    saved = desk.load(item["id"])
    assert saved["posts"]["youtube"]["dry_run"] is True
    assert saved["posts"]["youtube"]["payload"]["post"]["target"]["isMadeForKids"] is False
    assert desk.platform_posts("youtube") == []          # dry-runs never use up the daily cap


def test_blocked_post_is_recorded_as_failed(tmp_desk):
    item = desk.add_item("t.mp4", "squeex", "GTA 6 wheelies")
    msg = desk.do_post(item["id"], ["tiktok"], "schedule", "2026-11-24T10:30")
    assert "failed" in msg and "no approval" in msg
    assert desk.load(item["id"])["posts"]["tiktok"]["status"] == "failed"
