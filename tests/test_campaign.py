import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from scripts import campaign as c
from scripts import moments as m

CFG = c.load_campaign()
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=timezone.utc)
TT = c.terms(CFG, "tiktok")


def caption(platform="tiktok", extra=""):
    tag = "@OriginalFunko" if platform == "youtube" else "@funko"
    return (f"Peanut opening his own Pop is pure joy {extra}\n\n{CFG['funko_line']}\n{tag} @theburntpeanut #ad")


# ---------------------------------------------------------------- money

def test_payout_math():
    assert c.min_views(TT) == 2000
    assert c.max_views(TT) == 266667
    assert c.est_payout(1999, TT) == 0.0
    assert c.est_payout(2000, TT) == 3.0
    assert c.est_payout(100_000, TT) == 150.0
    assert c.est_payout(5_000_000, TT) == 400.0
    assert c.est_payout(None, TT) == 0.0


# ---------------------------------------------------------------- caption gate

def test_good_captions_pass():
    for p in ("tiktok", "instagram", "youtube"):
        assert c.check_caption(caption(p), p, CFG) == [], p


def test_caption_needs_funko_line_and_tags():
    assert any("Funko's caption line" in e for e in c.check_caption("hype @funko @theburntpeanut #ad", "tiktok", CFG))
    no_peanut = caption().replace("@theburntpeanut", "")
    assert any("@theburntpeanut" in e for e in c.check_caption(no_peanut, "tiktok", CFG))


def test_caption_wrong_funko_handle_per_platform():
    assert any("@OriginalFunko" in e for e in c.check_caption(caption("tiktok"), "youtube", CFG))
    assert any("wrong handle @OriginalFunko" in e for e in c.check_caption(caption("youtube"), "tiktok", CFG))


def test_caption_rejects_made_up_facts():
    assert any("$9.99" in e for e in c.check_caption(caption(extra="only $9.99"), "tiktok", CFG))
    assert c.check_caption(caption(extra="$14.99"), "tiktok", CFG) == []
    for claim in ("almost sold out", "drops at 3pm", "only 500 made", "restocking friday", "ships next week"):
        assert any("made-up" in e for e in c.check_caption(caption(extra=claim), "tiktok", CFG)), claim


def test_caption_banned_words_and_disclosure_and_dupes():
    assert any("official" in e for e in c.check_caption(caption(extra="official collab"), "tiktok", CFG))
    undisclosed = caption().replace(" #ad", "")
    assert any("not disclosed" in e for e in c.check_caption(undisclosed, "tiktok", CFG))
    assert c.check_caption(undisclosed, "tiktok", CFG, disclosure_toggle=True) == []
    assert any("already used" in e for e in c.check_caption(caption(), "tiktok", CFG, {c.caption_hash(caption())}))


def test_caption_english_only():
    assert any("English" in e for e in c.check_caption(caption(extra="これは最高のコラボです"), "tiktok", CFG))


# ---------------------------------------------------------------- edit gate

@pytest.fixture
def footage(tmp_path):
    (tmp_path / "reveal.mp4").write_bytes(b"x")
    return tmp_path


def spec(footage, **o):
    s = {"edit_id": "e1", "sources": [str(footage / "reveal.mp4")], "duration_s": 15, "hook_text": "FUNKO COOKED",
         "hook_start_s": 0, "subtitles_full": True, "pop_visible_s": [3.0]}
    s.update(o)
    return s


def test_good_edit_passes(footage):
    assert c.check_edit(spec(footage), CFG, footage_dir=str(footage)) == []


def test_edit_no_pop_no_pay(footage):
    errs = c.check_edit(spec(footage, pop_visible_s=[]), CFG, footage_dir=str(footage))
    assert any("No Pop" in e for e in errs)
    assert c.check_edit(spec(footage, pop_visible_s=[], pop_talk_s=[2.0]), CFG, footage_dir=str(footage)) == []


def test_edit_overlay_blocked_until_owner_approves(footage):
    errs = c.check_edit(spec(footage, pop_visible_s=[], pop_overlay=True), CFG, footage_dir=str(footage))
    assert any("overlay" in e for e in errs)
    ok = {**CFG, "pop_overlay_allowed": True}
    assert c.check_edit(spec(footage, pop_visible_s=[], pop_overlay=True), ok, footage_dir=str(footage)) == []


def test_edit_footage_only(footage, tmp_path_factory):
    other = tmp_path_factory.mktemp("elsewhere") / "twitch.mp4"
    errs = c.check_edit(spec(footage, sources=[str(other)]), CFG, footage_dir=str(footage))
    assert any("outside the campaign footage" in e for e in errs)
    assert any("FUNKO_FOOTAGE_DIR" in e for e in c.check_edit(spec(footage), CFG, footage_dir=""))


def test_edit_hook_subs_length_and_reuse(footage):
    fd = str(footage)
    assert any("hook" in e for e in c.check_edit(spec(footage, hook_text=""), CFG, footage_dir=fd))
    assert any("after 2s" in e for e in c.check_edit(spec(footage, hook_start_s=3), CFG, footage_dir=fd))
    assert any("subtitles" in e for e in c.check_edit(spec(footage, subtitles_full=False), CFG, footage_dir=fd))
    assert any("duration" in e for e in c.check_edit(spec(footage, duration_s=40), CFG, footage_dir=fd))
    assert any("already used" in e for e in c.check_edit(spec(footage), CFG, {"funko cooked"}, footage_dir=fd))
    assert any("official" in e for e in c.check_edit(spec(footage, hook_text="OFFICIAL DROP"), CFG, footage_dir=fd))


# ---------------------------------------------------------------- payout watch

def row(**o):
    r = {"post_id": "p1", "platform": "tiktok", "url": "https://tiktok.com/x", "posted_at": "2026-11-24T10:00:00Z",
         "views": "10000", "likes": "80", "comments": "5", "shares": "3", "likes_visible": "true",
         "tier1_share": "", "submitted_at": "", "paid_usd": "", "status": "live", "hook": "h"}
    r.update(o)
    return r


@pytest.mark.parametrize("over,status", [
    ({}, "SUBMIT"),
    ({"views": ""}, "NEEDS_STATS"),
    ({"views": "1500"}, "BELOW_MIN"),
    ({"likes": "20", "comments": "0", "shares": "0"}, "HOLD_ENGAGEMENT"),
    ({"likes_visible": "false"}, "FIX_LIKES"),
    ({"tier1_share": "0.30"}, "HOLD_AUDIENCE"),
    ({"tier1_share": "55"}, "SUBMIT"),
    ({"submitted_at": "2026-11-25"}, "SUBMITTED"),
    ({"views": "300000", "likes": "9000"}, "MAXED_SUBMIT"),
    ({"views": "300000", "likes": "9000", "submitted_at": "x"}, "MAXED"),
    ({"paid_usd": "15"}, "PAID"),
    ({"status": "deleted"}, "REMOVED"),
])
def test_classify(over, status):
    assert c.classify(row(**over), CFG, NOW)["status"] == status


def test_removed_early_is_flagged():
    assert "30-day" in c.classify(row(status="deleted"), CFG, NOW)["detail"]


def test_report_flags_submit_and_momentum():
    rows = [row(post_id=f"p{i}", url=f"u{i}", views=str(v), likes=str(v // 50))
            for i, v in enumerate([1000, 1200, 1500, 50000])]
    txt = c.report(rows, CFG, NOW)
    assert "Ready to submit: **1**" in txt
    assert "| tiktok | u3 | 50,000" in txt
    assert "Momentum" in txt and "u3" in txt.split("Momentum")[1]


def test_ledger_roundtrip(tmp_path):
    p = tmp_path / "l.csv"
    c.write_ledger([row()], p)
    assert c.read_ledger(p)[0]["views"] == "10000"


# ---------------------------------------------------------------- moments

def test_parse_subs_and_pop_hits():
    srt = "1\n00:00:01,000 --> 00:00:03,000\nlook at this Funko\n\n2\n00:00:05,000 --> 00:00:06,000\nhello chat\n"
    cues = m.parse_subs(srt)
    assert len(cues) == 2 and cues[0][2] == "look at this Funko"
    assert m.pop_hits(cues, CFG["pop_keywords"]) == [(1.0, "look at this Funko")]


def test_rank_puts_payoff_late_and_prefers_pop():
    rms = [-40.0] * 200                      # 100 s
    rms[60] = -5.0                           # loud at 30 s, no Pop
    rms[160] = -8.0                          # slightly quieter at 80 s, with Pop mention
    wins = m.rank(rms, [(78.0, "my funko pop")], length=16, top=2)
    assert wins[0]["payoff_s"] == 80.0 and not wins[0]["needs_pop_check"]
    assert wins[0]["start_s"] == pytest.approx(80 - 12, abs=0.1)
    assert wins[1]["needs_pop_check"]
    assert wins[0]["end_s"] <= wins[1]["start_s"] or wins[0]["start_s"] >= wins[1]["end_s"]


def test_rms_series_on_real_audio(tmp_path):
    f = tmp_path / "t.mp4"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "sine=f=440:d=3", str(f)], check=True)
    vals = m.rms_series(f)
    assert 5 <= len(vals) <= 7 and max(vals) > -30


# ---------------------------------------------------------------- multi-campaign

def test_every_campaign_config_loads_and_has_paths():
    ids = c.campaign_ids()
    assert "clipfarm-funko-burntpeanut" in ids and "contentrewards-dasgasd3m" not in ids
    assert "contentrewards-dasgasd3m" in c.campaign_ids(include_ended=True)
    for cid in ids:
        cfg = c.load_campaign(cid)
        assert c.ledger_path(cfg).name.endswith(".csv")
        for p in cfg["platforms"]:
            c.terms(cfg, p)


def test_dom_campaign_math_and_caption():
    dom = c.load_campaign("contentrewards-dasgasd3m")
    t = c.terms(dom, "tiktok")
    assert c.est_payout(10_000, t) == 20.0
    assert c.est_payout(1_000_000, t) == 150.0
    ok = "dom sent it off the pier\n\nCredit: @dasgasd3m (Twitch)\n\n#gta6 #dasgasd3m #gaming"
    assert c.check_caption(ok, "tiktok", dom) == []
    assert any("@dasgasd3m" in e for e in c.check_caption(ok.replace("@dasgasd3m", "@someone"), "tiktok", dom))


def test_ended_campaign_report_says_so():
    dom = c.load_campaign("contentrewards-dasgasd3m")
    assert "ended" in c.report([], dom, NOW)


def test_report_cli_runs_every_campaign(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(c, "ROOT", tmp_path)
    (tmp_path / "tracking" / "campaigns").mkdir(parents=True)
    assert c.main(["report"]) == 0
    out = capsys.readouterr().out
    assert "Funko" in out and "Dom" not in out
