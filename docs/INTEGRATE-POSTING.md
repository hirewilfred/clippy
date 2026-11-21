# Add posting to the Clip desk (gta-click-club)

Brief for the Claude session that maintains the `gta-click-club` Next.js app. Today the Review tab can approve drafts, but the app "does not post to social media". This adds a **Post** step to the Review tab: TikTok + YouTube Shorts through Blotato, with a YouTube thumbnail. Every rule below is enforced on the server, not just in the UI.

Reference implementation (Python, tested) lives in this repo, `hirewilfred/clippy`, branch `pipeline/gates-and-clip`. Port the logic; don't call the Python.
- `scripts/gates.py` + `tests/test_gates.py`: every rule, and 31 test cases to port.
- `scripts/publish.py` + `tests/test_publish.py`: the exact Blotato v2 requests.
- `scripts/thumb.py`: thumbnail layout (optional; see step 5).

## 1. Approval lock (change the existing approve action)
When a draft's status is set to `approved`, store on the draft:
- `approval.videoSha256`: sha256 of the stored video bytes;
- `approval.textSha256`: sha256 of `caption + "\n\x1e\n" + ytTitle + "\n\x1e\n" + ytDescription`;
- `approval.approvedAt`.

Any later edit to the video, caption, title or description must clear `approval` and set the status back to `draft`. The Post route recomputes both hashes and refuses on any mismatch.

## 2. New draft fields (if missing)
`caption` (TikTok), `ytTitle`, `ytDescription`, `streamerHandle`, `durationSec`, `thumbnail` (stored image, 1280x720 JPEG, under 2 MB), and `posts: { tiktok?: PostRecord, youtube?: PostRecord }`, where `PostRecord = { status: "scheduled"|"submitted"|"published"|"failed"|"cancelled", scheduledFor: string|null, postSubmissionId?: string, publicUrl?: string, error?: string, dryRun: boolean }`.

Pre-fill on draft creation:
```
caption        = "<hook, lower case, 1-6 words>\n\nCredit: @<handle> (Twitch)\n\n#gta6 #<handle> #gaming #funnymoments"
ytTitle        = "<Hook> #gta6"   (max 60 chars)
ytDescription  = "Credit: @<handle> (Twitch)\nhttps://www.twitch.tv/<handle>\n\n#gta6 #<handle> #gaming\n\nFan page. Not affiliated with Rockstar Games."
```

## 3. Gates (server-side; port `gates.py` exactly)
Refuse the post and return the list of reasons when any of these fail:
- the status is not `approved`, or either approval hash mismatches;
- the streamer is blocked (keep a `blocked` flag on the watch-list entry);
- the caption lacks the line `Credit: @<the draft's handle> (Twitch)`, has anything other than 3-5 hashtags or duplicate hashtags, ends with `?`, contains an em/en dash, has more than one emoji, or has a first line outside 1-6 words;
- the caption, title or description contains a banned word (whole word): `official`, `leak`, `early access`, `free copy`, `giveaway`;
- YouTube only: the title is empty or over 60 characters, or the description is missing the credit line or "Not affiliated with Rockstar Games";
- the duration is outside 12-20 s;
- the draft has already been posted or scheduled on that platform (unless that attempt failed or was cancelled);
- the platform is paused;
- less than 120 minutes have passed since a profile edit (keep a `lastProfileEditAt` setting);
- **cadence, per platform**: the daily cap from the warm-up table below would be exceeded (counted in America/Toronto calendar days), or another live post is within 150 minutes. Dry-runs never count.

Warm-up table (account day 1 = 2026-11-21):

| Day | Cap per platform per day |
|---|---|
| 1 | 0 |
| 2-3 | 2 |
| 4-6 | 4 |
| 7-13 | 6 |
| 14+ | 8 |

Show the gate results live in the Review UI for the chosen time, so the owner sees why a post is blocked before pressing anything.

## 4. Post route: `POST /api/drafts/[id]/post`
- Owner-only, with the same `rejectCrossOrigin` + `requireOwner` as the PATCH route. **Never reachable with the worker token.**
- Body: `{ platforms: ("tiktok"|"youtube")[], mode: "now"|"schedule", scheduledFor?: ISO string with offset, confirm: true }`.
- Run the gates. If any fail: 422 with the reasons.
- If `LIVE !== "true"`: return the exact Blotato payloads (dry-run) and record `dryRun: true`.
- Otherwise, for each platform, using the Blotato REST v2 API (`https://backend.blotato.com/v2`, header `blotato-api-key: $BLOTATO_API_KEY`, server-side only):
  1. Resolve the account: `GET /users/me/accounts?platform=<p>`. Use the env `BLOTATO_TIKTOK_ACCOUNT_ID` (= `70702`, @clippyclubgta6) and `BLOTATO_YOUTUBE_ACCOUNT_ID` (= `58996`, "Clippyclub (ClippyclubGTA6)"). If they're unset, there must be exactly one account whose `username` is `clippyclubgta6` or whose `fullname` contains `(ClippyclubGTA6)`. Otherwise refuse.
  2. Upload the video: `POST /media/uploads {filename}` → `{presignedUrl, publicUrl}`, then `PUT` the bytes to `presignedUrl` with `Content-Type: video/mp4`. **Don't send the API key to the presigned URL.** Do the same for the thumbnail (YouTube only).
  3. `POST /posts` with body:
     - TikTok:
       ```json
       {"post":{"accountId":"<id>","content":{"text":"<caption>","mediaUrls":["<publicUrl>"],"platform":"tiktok"},
         "target":{"targetType":"tiktok","privacyLevel":"PUBLIC_TO_EVERYONE","disabledComments":false,"disabledDuet":false,
         "disabledStitch":false,"isBrandedContent":false,"isYourBrand":false,"isAiGenerated":false}},
        "scheduledTime":"<UTC ISO, omit for now>"}
       ```
     - YouTube:
       ```json
       {"post":{"accountId":"<id>","content":{"text":"<ytDescription>","mediaUrls":["<publicUrl>"],"platform":"youtube"},
         "target":{"targetType":"youtube","title":"<ytTitle>","privacyStatus":"public","shouldNotifySubscribers":true,
         "isMadeForKids":false,"containsSyntheticMedia":false,"thumbnailUrl":"<thumb publicUrl>"}},
        "scheduledTime":"<UTC ISO, omit for now>"}
       ```
     `scheduledTime` goes at the **root**, not inside `post` (inside `post`, Blotato ignores it and posts immediately).
  4. Save `postSubmissionId`. Set the draft status to `posted` once at least one platform succeeded.
- Rate limits: 30 posts/min, 120 uploads/min. Fine at our volume.

Status refresh: `GET /posts/{postSubmissionId}` returns `in-progress|scheduled|published|failed`, plus `publicUrl` or `errorMessage`. Poll no more than every 10 s.

Cancel a scheduled post: `DELETE /schedules/{scheduleId}`. The schedule id (`sch_...`) differs from the `postSubmissionId`; find it with `GET /schedules`. Until that mapping is verified, the UI says "Cancel in Blotato > Scheduled" and offers **Mark cancelled**.

## 5. YouTube thumbnail
- Build it from the draft (best reaction frame plus 2-5 words of hook text, heavy outline, last line yellow), 1280x720 JPEG under 2 MB. The clipper box can make it with `scripts/thumb.py` (ffmpeg) and upload it with the draft, or reuse the existing thumbnail route if it already composes text.
- Show it in Review, with a field to edit the text and a regenerate action (which voids nothing: thumbnails aren't part of the approval hash, but the owner sees them before confirming).
- YouTube only applies `thumbnailUrl` on verified channels.

## 6. Review UI
On an approved draft, add a **Post** panel:
- TikTok / YouTube checkboxes, **Schedule** (date/time, Eastern) or **Post now**;
- live gate results per platform;
- **Review** → a confirmation screen with the video, the exact caption, YouTube title and description, the thumbnail, and the time → **Confirm** or **Cancel**;
- a banner: green "DRY-RUN" unless `LIVE=true`, red "LIVE" when it is.
- Per-platform status rows with **Refresh status** and **Mark cancelled**.

## 7. Env (Vercel project settings, never in code)
`BLOTATO_API_KEY`, `LIVE` (`true` only when ready), `BLOTATO_TIKTOK_ACCOUNT_ID=70702` and `BLOTATO_YOUTUBE_ACCOUNT_ID=58996`.

## 8. Tests (required before deploy)
Port every case in `tests/test_gates.py` and `tests/test_publish.py`. At minimum:
- editing text after approval blocks the post;
- a swapped video blocks it;
- the worker token can't call the Post route;
- a dry-run never calls Blotato;
- `scheduledTime` is at the root of the body.

## 9. Rollout
Deploy with `LIVE` unset → dry-run one draft per platform and compare the payloads against this doc → set `LIVE=true` → post one clip → check it on both apps → then use it normally.
