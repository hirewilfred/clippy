import json
import shutil
import subprocess
import sys
import types

import pytest

from scripts import clip, moments
from scripts.moments import draft_spec, find_peaks, merge_clipsai, parse_ebur128


def flat(dur=120.0, level=-30.0, spikes=()):
    s = [(round(i * 0.1, 1), level) for i in range(int(dur * 10))]
    for t, v in spikes:
        i = int(t * 10)
        s[i] = (s[i][0], v)
    return s


def test_parse_ebur128():
    txt = ("frame:0    pts:0       pts_time:0\nlavfi.r128.M=-120.691\n"
           "frame:1    pts:1600    pts_time:0.1\nlavfi.r128.M=-23.5\n")
    assert parse_ebur128(txt) == [(0.0, -120.691), (0.1, -23.5)]


def test_find_peaks_ranks_and_windows():
    peaks = find_peaks(flat(spikes=[(30, -12), (80, -18), (31, -14)]), 120.0)
    assert [p["peak_s"] for p in peaks] == [30.0, 80.0]      # 31 s merged into the 30 s moment
    assert peaks[0]["start_s"] == 18.0 and peaks[0]["end_s"] == 33.0
    assert peaks[0]["score_db"] == 18.0


def test_find_peaks_ignores_small_bumps_and_silence():
    assert find_peaks(flat(spikes=[(40, -25)]), 120.0) == []  # +5 dB is not a reaction
    assert find_peaks([(0.0, -120.0), (0.1, -120.0)], 1.0) == []


def test_window_clamped_to_recording():
    p = find_peaks(flat(dur=20, spikes=[(5, -10), (19.5, -11)]), 20.0)
    assert p[0]["start_s"] == 0.0 and all(x["end_s"] <= 20.0 for x in p)


def test_draft_spec_blocks_render_until_reviewed():
    c = {"peak_s": 130.0, "start_s": 118.0, "end_s": 133.0, "score_db": 12.0, "reasons": ["x"]}
    info = {"streamer": "squeex", "site": "twitch", "section": [4200, 5100], "source_url": "u"}
    spec = draft_spec(c, "piece.mp4", 115.0, "out.mp4", info)
    assert spec["credit"] == "@squeex (Twitch)" and spec["_source"]["vod_time"] == "01:12:10"
    assert spec["keep"] == [[3.0, 18.0]] and spec["teaser"] == {"start": 14.5, "end": 17.5}
    errs = clip.validate(spec, None, None)
    assert {"missing 'facecam'", "missing 'game'", "missing 'teaser_text'"} <= set(errs)
    filled = {**spec, "facecam": [0, 0, 400, 300], "game": [0, 0, 1920, 1080], "teaser_text": "GTA 6 COPS"}
    assert clip.validate(filled, None, None) == []           # 3 s teaser + 15 s keep fits 12-20 s


def test_draft_spec_without_streamer_demands_credit():
    c = {"peak_s": 20.0, "start_s": 8.0, "end_s": 23.0, "score_db": 9.0, "reasons": []}
    spec = draft_spec(c, "s.mp4", 0.0, "o.mp4", None)
    assert "credit" not in spec and any("credit" in t for t in spec["_todo"])


def test_merge_clipsai_boosts_overlap():
    peaks = [{"peak_s": 10.0, "score_db": 9.0, "reasons": []}, {"peak_s": 50.0, "score_db": 10.0, "reasons": []}]
    out = merge_clipsai(peaks, [(5.0, 20.0)])
    assert out[0]["peak_s"] == 10.0 and out[0]["score_db"] == 12.0 and "ClipsAI" in out[0]["reasons"][0]


def test_clipsai_missing_gives_install_hint(monkeypatch):
    monkeypatch.setitem(sys.modules, "clipsai", None)
    with pytest.raises(moments.MomentsError, match="requirements-clipsai.txt"):
        moments.clipsai_segments("x.mp4")


def test_clipsai_adapter_with_fake_module(monkeypatch):
    seg = types.SimpleNamespace(start_time=12.0, end_time=48.5)
    fake = types.SimpleNamespace(
        Transcriber=lambda: types.SimpleNamespace(transcribe=lambda audio_file_path: "T"),
        ClipFinder=lambda: types.SimpleNamespace(find_clips=lambda transcription: [seg]))
    monkeypatch.setitem(sys.modules, "clipsai", fake)
    assert moments.clipsai_segments("x.mp4") == [(12.0, 48.5)]
    c = moments.from_segments([(12.0, 48.5)], 5)[0]
    assert c["end_s"] == 48.5 and c["start_s"] == 33.5 and c["score_db"] is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_end_to_end_finds_the_scream(tmp_path):
    src = tmp_path / "vod.mp4"
    # 60 s of quiet tone with a loud burst at 40-41 s, over a test pattern.
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=30:duration=60",
                    "-f", "lavfi", "-i", "sine=frequency=300:duration=60",
                    "-filter_complex", "[1:a]volume='if(between(t,40,41),1.0,0.03)':eval=frame[a]",
                    "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    str(src)], check=True, timeout=120)
    rep = moments.run(src, top=3, cut=True, out_dir=tmp_path / "m")
    top = rep["candidates"][0]
    assert 39.5 <= top["peak_s"] <= 41.5
    spec = json.loads((tmp_path / "m" / "vod_m1.spec.json").read_text())
    assert (tmp_path / "m" / spec["src"]).exists() and (tmp_path / "m" / "vod_m1.jpg").exists()
