# Open-source tools: yt-dlp, ClipsAI, Postiz

How the three tools from the "3 open-source repos before you start clipping" video fit Clippy Club. The video's version is "yt-dlp feeds ClipsAI feeds Postiz, posts every clip". **Ours keeps a person and the gates between every step.** Auto-posting every detected clip breaks the hard rules (approval hash, allowlist, warm-up caps) and gets new accounts flagged.

```
scout.py (Twitch clips) ─┐
                         ├─> sheet / moments contact sheet ─> VERIFY (by eye) ─> clip.py ─> QA ─> approval ─> gates ─> Blotato | Postiz
vod.py (VOD section) ─> moments.py ─┘
```

## 1. yt-dlp: download

| | |
|---|---|
| Already used by | `scout.py` (clip metadata), `sheet.py` (clip download) |
| New | `scripts/vod.py`: one section of a finished Twitch VOD or YouTube stream |
| Run | `python -m scripts.vod https://www.twitch.tv/videos/<id> --section 01:10:00-01:25:00 --dry-run` |
| Limits | Finished VODs only (no live recording). A section is required, 30 min max. Streamers marked `blocked` are refused before anything downloads. Writes a `.json` sidecar with the streamer and VOD offset, so credits and timestamps carry through. |

**Use it for retainer streamers first.** They asked to be clipped, so mining their VODs is the service. For anyone else, it's the same rights position as clipping their Twitch clips, with the same gates.

## 2. ClipsAI: find moments

| | |
|---|---|
| New | `scripts/moments.py` |
| Run | `python -m scripts.moments work/vods/<file>.mp4 --cut` |
| Output | `work/moments/<stem>_mN.spec.json` (draft `clip.py` spec), plus `_mN.mp4` and `_mN.jpg` (short source and contact sheet) with `--cut` |

**Default detector: `loudness`** (ffmpeg only). It finds reaction spikes (screams, laughs, crashes) at least 8 dB above the recording's median, builds a 12 s setup + 3 s reaction window, and drafts a 3 s payoff-first teaser.

**ClipsAI is optional** (`--detector clipsai` or `both`). Reasons:
- By its own README, it's built for podcasts and interviews. It segments by transcript topic, which isn't what makes a GTA reaction clip land. In `both` mode it only boosts loudness peaks that sit inside a ClipsAI segment.
- Its 9:16 resize (speaker tracking, needs a Hugging Face token) isn't used. `clip.py`'s facecam-over-gameplay stack is our format.
- It's heavy (torch, pyannote, WhisperX), unmaintained since Jan 2024, and **doesn't install on Python 3.12+**. Use a Python 3.10/3.11 venv: see `requirements-clipsai.txt`.

**Draft specs are blocked by design.** They leave out `facecam`, `game` and `teaser_text`, so `clip.py` refuses to render until someone has checked the contact sheet and filled them in. Every candidate still goes through VERIFY: GTA 6 proof frame, real facecam, nothing banned, dedupe, and the send test.

## 3. Postiz: schedule and post

| | |
|---|---|
| New | `scripts/postiz.py`, selected with `PUBLISHER=postiz` (default stays `blotato`) |
| Same as Blotato | `gates.check` runs first, and the same way for both backends. Dry-run unless `LIVE=true` and the caller passes `live`. Account must match `@clippyclubgta6` exactly once, or be pinned by env. |
| Payload | TikTok: `DIRECT_POST`, public, comments on, AI label off, branded off. YouTube: public, made for kids "no", made with AI false, optional thumbnail. |
| Desk | `desk.py` records which backend sent each post. Refresh reads Postiz post state (`QUEUE`/`PUBLISHED`/`ERROR`). |

**Why it matters now:** the Blotato trial ends around Nov 28. Postiz is the free path if it's self-hosted.

**Decisions for the owner (I've done none of these):**
1. **Postiz Cloud is a paid plan.** Self-hosting is free, but you need a server (Docker) and public HTTPS media storage. TikTok rejects non-public media URLs, so use Cloudflare R2 or similar.
2. **Self-hosted TikTok needs your own TikTok developer app.** Until TikTok audits that app for the Content Posting API, posts are limited to private (`SELF_ONLY`). Expect `DIRECT_POST` public posts to fail until the audit passes. This is the biggest blocker, and the reason Blotato stays the default.
3. **Self-hosted YouTube needs a Google Cloud OAuth app.** Upload quota is enough for our caps.
4. Connecting @clippyclubgta6 to Postiz is an account action, so you do it. Don't edit the profile while connecting: a profile edit starts the 2-hour posting quarantine.

**Still on Blotato:** the Vercel portal (`web/scheduler`). Porting it means a Postiz version of `lib/blotato.js`, and its cadence gate has to read Postiz's `GET /posts` instead of Blotato schedules. Do that only if you switch for good.
