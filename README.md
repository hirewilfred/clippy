# Clippy Club

An automated clipping page for GTA 6 streamer moments on TikTok and YouTube Shorts (@clippyclub67).

A scheduled agent finds funny streamer moments on Twitch, checks they're fresh and really GTA 6, cuts them into vertical clips that are edited payoff-first, and queues them for human approval. Approved clips are posted through Blotato. The agent also tracks results and runs one experiment at a time.

## How it runs

```
every 4h (moving to 2h later):  ANALYZE → HEALTH CHECK → LEARN → SCOUT → VERIFY → CUT → QA
                                → QUEUE FOR APPROVAL → STRATEGIZE → DASHBOARD → CLEAN UP → REPORT
human:                          approves clips in the portal (approval locks the exact video + caption)
publisher:                      posts approved clips via Blotato, only if every gate passes and LIVE=true
```

`playbook.md` holds the rules the agent reads on every run. `CLAUDE.md` holds the hard rules for every Claude session.

## Layout

| Path | What |
|---|---|
| `playbook.md` | Per-run routine and content rules for the agent |
| `config/` | Warm-up ramp, streamer allowlist, posting slots |
| `supabase/migrations/` | Database schema: clips, approvals, posts, metrics, experiments, takedowns |
| `tracking/` | Human-readable logs (learning, experiments, takedowns) |
| `scripts/` | Pipeline scripts (scout, contact sheet, clip render, publisher) |
| `docs/` | Handoff notes and sales material |

## Setup

1. Copy `.env.example` to `.env` and fill it in. Cloud sessions get keys from the environment's credential store instead.
2. Apply `supabase/migrations/` to the Supabase project.
3. Requires `ffmpeg`, `yt-dlp` and Python 3.11 or newer.

## Status

Scaffold only. Pipeline scripts are being built. `LIVE` stays `false` until the end-to-end dry run is approved.
