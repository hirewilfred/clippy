# Clip workflow in the Clippy portal

One dashboard (gta-click-club). Every clip moves through these stages, and each move is logged with who did it, when and why.

```
FOUND ──► REVIEW ──► APPROVED QUEUE ──► SCHEDULED ──► POSTED
  (clipper box)   (watch, approve)   (pick slot)    (Blotato)   (live URL)
                    └─► REJECTED (with reason)        └─► FAILED / CANCELLED
```

## Stages
| Stage | How a clip gets here | Shown in | Actions |
|---|---|---|---|
| **Found** | The clipper box creates a draft (existing worker API). Video and thumbnail are stored in Blob. | Review tab | (automatic) |
| **Review** | The draft has a finished video | Review tab | Watch, edit caption/title/description/thumbnail text, **Approve**, **Reject** (reason required) |
| **Approved queue** | Owner approved. The approval locks a hash of the exact video bytes and text. Any edit returns the clip to Review. | **Queue** tab (new) | **Schedule** at the suggested slot or a chosen slot, **Post now**, **Back to review** |
| **Scheduled** | Blotato accepted it. Store `postSubmissionId` per platform. | **Scheduled** tab (new) | **Cancel** (Blotato `DELETE /v2/schedules/{id}`; the clip returns to the Approved queue), **Refresh status** |
| **Posted** | Blotato status `published`. Store `publicUrl`. | Scheduled tab, "Posted" section | Open the live post |
| **Failed** | Blotato status `failed`. Store `errorMessage`. | Scheduled tab, with error | **Retry** (back to the Approved queue) |

Statuses on the draft: `draft → approved → scheduled → posted`, plus `rejected`, `failed` and `cancelled`.
Each platform (TikTok, YouTube) is tracked separately under `posts.{platform}`.

## "Why this time": the schedule reason
When the owner opens Schedule, the server suggests the next valid slot and explains it. The explanation is saved with the post and shown on the Scheduled card.

Suggestion algorithm (per platform, Eastern time):
1. Candidate slots: 04:30, 07:30, 10:30, 13:00, 15:30, 18:00, 20:30, 22:15, from now onward.
2. Drop slots that break a rule: daily warm-up cap, less than 2.5 h from another post on that platform, more than 2 per hour, a paused platform, or within 2 h of a profile edit.
3. Prefer, in order: the 04:30 breakout window → filling the biggest empty gap (overnight first, since the audience is global) → the earliest.
4. Avoid 23:30-01:00 ET (the weakest slot in the source data).

Example reasons saved:
- "4:30 AM: strongest breakout window for gaming clips (reaches EU morning and US early risers); 3h after your last TikTok post; post 1 of 2 allowed today."
- "1:00 PM: fills the 8.5h gap after the 4:30 AM post; post 2 of 2 allowed today (warm-up day 2)."
- "Chosen by you: 8:00 PM. Passes all rules; 2h45m after the last post."

If the owner picks a time that breaks a rule, show the reason it's blocked and the next valid alternative. Don't silently move it.

## Rules (server-side, on Approve and on Schedule)
Port `scripts/gates.py` / `web/scheduler/lib/gates.js` exactly: credit line, 3-5 hashtags, banned words, no question or em dash, max 1 emoji, 1-6 word caption line, YouTube title of 60 characters or fewer with the credit and Rockstar disclaimer, 12-20 s, warm-up cap, 2.5 h gap, max 2 per hour, blocked streamers, paused platforms.
Cadence counts both Blotato's pending schedule and the portal's own posted and scheduled records (so already-published posts count too).

## Blotato (server-side only)
- Upload: `POST /v2/media/uploads {filename}` → `PUT` bytes to `presignedUrl` → use `publicUrl`.
- Post: `POST /v2/posts` with `scheduledTime` at the **root** (TikTok/YouTube payloads as in `docs/INTEGRATE-POSTING.md`).
- Status: `GET /v2/posts/{postSubmissionId}`. Poll from the Scheduled tab and on a 10-minute cron.
- Cancel: `DELETE /v2/schedules/{id}`.
- Env: `BLOTATO_API_KEY`, `BLOTATO_TIKTOK_ACCOUNT_ID=70702`, `BLOTATO_YOUTUBE_ACCOUNT_ID=58996`, `LIVE`.

## Tests required before deploy
- An edit after approval returns the clip to Review.
- A swapped video is refused.
- The worker token can't approve or schedule.
- The suggested slot always passes the rules, and its reason matches the rule that chose it.
- A dry-run never calls `POST /v2/posts`.
- Cancel returns the clip to the queue.
- A published post moves to Posted with its URL.
