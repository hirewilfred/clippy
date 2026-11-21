# Clippy Schedule

A password-protected page to see, approve and schedule TikTok + YouTube posts through Blotato.
Deployed as the Vercel project `clippy-schedule` (root directory `web/scheduler`). No build step: static `index.html` plus Node functions in `api/`.

## Environment variables (Vercel > clippy-schedule > Settings > Environment Variables)
| Name | Required | What |
|---|---|---|
| `SCHEDULER_PASSWORD` | yes | The sign-in password. You choose it. |
| `BLOTATO_API_KEY` | yes | Blotato > Settings > API. Never commit it. |
| `BLOTATO_TIKTOK_ACCOUNT_ID` | yes | `70702` (@clippyclubgta6) |
| `BLOTATO_YOUTUBE_ACCOUNT_ID` | yes | `58996` (ClippyclubGTA6) |
| `LIVE` | no | `true` to post for real. Anything else = dry-run. |
| `BLOCKED_STREAMERS` | no | Comma list of handles that must never be posted. |
| `PAUSED_PLATFORMS` | no | `tiktok`, `youtube` or `all`. |
| `SESSION_SECRET` | no | Overrides the session key (otherwise derived from the Blotato key). |

Redeploy after changing any of them.

## Flow
Choose clip, fill text, generate thumbnail, pick platforms and time. Live gate check shows what's blocked.
**Approve** uploads the clip to Blotato storage and returns a signed approval locked to the exact video URL and text.
**Review** shows exactly what goes out. **Schedule / Post now** re-checks every rule on the server before calling Blotato.

## Rules enforced server-side
Same as `scripts/gates.py`: credit line, 3-5 hashtags, banned words, no question/em dash, max 1 emoji, 1-6 word caption line,
YouTube title of 60 characters or fewer, credit and Rockstar disclaimer, clip length 12-20 s, warm-up daily cap (Eastern days), 2.5 h gap, max 2/hour,
blocked streamers and paused platforms. Cadence counts the posts currently scheduled in Blotato; already-published posts
are not visible to it.

## Test
`npm test` runs the gate tests and the handler tests (stubbed Blotato, never posts). `node dev-server.js` runs it locally.
