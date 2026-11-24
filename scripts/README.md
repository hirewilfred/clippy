# Scripts

Run tests: `python3 -m pytest -q` from the repo root.

| Script | Status |
|---|---|
| `gates.py` | Done: 31 tests |
| `scout.py` | Done: public Twitch pages, no API key. Watchlist in `config/watchlist.json` |
| `sheet.py` | Done: download + timestamped contact sheet |
| `thumb.py` | Done: 1280x720 + 1080x1920 YouTube thumbnails, auto frame pick, under 2 MB |
| `publish.py` | Done: Blotato v2 (presigned upload, TikTok/YouTube targets, thumbnailUrl), gated, dry-run default |
| `desk.py` | Done: local Post desk (add, edit, thumbnail, approve, review, post/schedule). See docs/POST-DESK.md |
| `clip.py` | Done: 18 tests incl. an end-to-end render. `python -m scripts.clip spec.json --preview` |
| `vod.py` | Done: yt-dlp section download of a finished Twitch/YouTube VOD (max 30 min, blocked streamers refused). `--dry-run` |
| `moments.py` | Done: finds reaction spikes in a VOD section, drafts clip.py specs + contact sheets. Optional ClipsAI detector |
| `postiz.py` | Done: Postiz publisher backend, picked with `PUBLISHER=postiz`; same gates, dry-run default |

See `docs/OPEN-SOURCE-TOOLS.md` for how yt-dlp, ClipsAI and Postiz fit the pipeline.

Still to build:

| Script | Job |
|---|---|
| `scout.py` | Twitch Helix top GTA 6 clips, last 3-24h, English; writes candidates to `clips` |
| `sheet.py` | Contact sheet (1 frame / 2 s) per candidate for visual verification |
| `clip.py` | JSON spec to 1080x1920 30fps payoff-first render via ffmpeg |
| `gates.py` | Publisher gates: approval hash match, allowlist, warm-up cap, gap, pauses, caption rules |
| `publish.py` | Blotato publisher; dry-run unless LIVE=true |
| `metrics.py` | Pulls post stats into `metrics` |
