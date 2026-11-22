# Clippy Club: handoff to a new cloud session

Paste everything below the line into a new Claude Code session that has `hirewilfred/clippy` selected.

---

Context: Clippy Club is a faceless GTA 6 streamer-clip page (TikTok + YouTube Shorts, handle @clippyclubgta6). GTA 6 launched Nov 19, 2026. The model to follow is Nate Herk's "Faceless Clipping Page Run by Grok Bot" guide (AI Automation Society): a 2-hour CEO routine that analyzes, checks for stuck posts, learns, scouts, verifies, cuts, QAs, schedules, strategizes, updates the dashboard, cleans up, and reports. We're porting it from Grok Bot to Claude Code cloud agents.

Current state:
- Accounts created Nov 21. Blotato 7-day trial started Nov 21 (ends around Nov 28), with both accounts connected.
- First 4 clips are scheduled in Blotato for Nov 22–23 at 4:30 AM and 1:00 PM ET. They were posted by hand, outside the pipeline.
- A local Claude Code session on my PC has been working on: hard-coded paths, the upload bug that hid errors, real hooks, approval that locks the exact video and caption plus a Post now / Cancel step, Blotato/YouTube/TikTok connectors (dry-run by default), guardrails (facecam only, credit required, max 2 posts/hour, takedowns log), a warm-up ramp, a streamer allowlist gate, tests, and handoff notes.

Decisions already made:
- Posting goes through Blotato (API key in .env, never committed). Native APIs stay dry-run only.
- Start at 2 posts per platform per day, ramp to 4–6 by Nov 26. Keep posts at least 2.5h apart. 10 minutes a day of manual warm-up scrolling; never automate engagement.
- Allowlist: permitted streamers can be auto-approved; everyone else needs manual approval.
- YouTube: no custom thumbnails (frame 1 carries the hook). Title is the hook plus a keyword. Made for kids = No. Altered content = No. Description has the credit, the streamer link and "Fan page. Not affiliated with Rockstar Games."
- Revenue goal: $5K/month by about day 90. Primary engine is clipping retainers sold to streamers ($500 / $1,000 / $1,500 per month tiers, $150 14-day pilot); paid clipping campaigns come second. YouTube ad revenue is assumed to be $0 (reused-content policy). We build the system first, then do outreach.

Build next, in order:
1. Read the whole repo and summarize its state against the list above.
2. Supabase schema: clips, approvals, posts, scoreboard, experiments, takedowns, plus a storage bucket for renders. Point the portal at it.
3. Cloud environment: setup script for ffmpeg and yt-dlp; network access to the Twitch API/clip hosts and the Blotato API. Request keys through the secure credential form (Twitch client ID/secret, Blotato).
4. playbook.md adapted from the guide's routine prompt for TikTok + YouTube Shorts. Scheduled routine every 4h (move to 2h once share rate justifies it), quiet 11 PM–7 AM ET with a 7 AM recap. Every clip still needs approval in the portal.
5. One end-to-end dry run while I watch, then go live.

Rules: never print or commit secrets. Never post, pay, message anyone or change a profile without my explicit OK. LIVE stays false until I say so.
