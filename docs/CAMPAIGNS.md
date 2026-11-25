# Paid clipping campaigns

Campaigns are the second revenue engine, after streamer retainers (see HANDOFF.md). Every rule in `CLAUDE.md` and `playbook.md` still applies. Where a campaign brief is stricter, the brief wins. Where a brief conflicts with a hard rule, the owner decides; the agent never does.

## Campaigns on file
| Campaign | Config | Status |
|---|---|---|
| ClipFarm x Funko x TheBurntPeanut | `config/campaigns/clipfarm-funko-burntpeanut.json` | joined |
| Dasgasd3m (Dom) GTA 6 clips, Content Rewards | `config/campaigns/contentrewards-dasgasd3m.json` | pending_join: $2.00/1K, $150 max per clip, $1,000 budget, TikTok + Shorts. Normal GTA 6 clips of Dom with the usual credit line. Brief not re-read (Content Rewards is blocked from the cloud session). |

`python -m scripts.campaign report --fetch` runs every campaign that isn't `ended`.

## ClipFarm x Funko x TheBurntPeanut
Terms: `config/campaigns/clipfarm-funko-burntpeanut.json`. Ledger: `tracking/campaigns/funko-burntpeanut.csv`.

### How the money works
| Lever | Number | What it means |
|---|---|---|
| CPM | $1.50 / 1K views, same on TikTok, Reels and Shorts | 100K views = $150 |
| Minimum | $3.00 = **2,000 views** | A post under 2K views earns **$0** |
| Cap | $400 per post = **266,667 views** | Views past the cap earn nothing, so make a new edit instead |
| Budget | $12,000 total, $11,930 left on Nov 25, 11 clippers | Paid until the pool is gone, so speed matters |
| Unit | One edit, posted once per platform | Every new post needs a new edit, hook and caption |

**Revenue = sum over posts of min(views × $0.0015, $400), counting only posts that reach 2K views and are approved.**
So we maximize dollars by:
1. **Getting more posts over 2K views.** Each post that clears the floor pays at least $3, and the floor is where most new-account posts die.
2. **Making more distinct edits.** We have 2 platforms (TikTok, Shorts; no Instagram account), so each edit is 2 paid posts.
3. **Shipping early,** before the budget runs out.
4. **Getting zero rejections.** No Pop, a wrong caption line, hidden likes, under 0.5% engagement, or under 40% tier-1 audience each pay $0. The gates exist for this.

Honest expectation: on a 4-day-old account, most posts will land under or near 2K views. Assume about $3–15 per post, with an occasional breakout. Treat this as cash flow plus a test of the format, not a core income line.

### The routine (the agent runs this; the human submits)
1. **SOURCE:** only the campaign footage (Dropbox folder, downloaded by the owner into `$FUNKO_FOOTAGE_DIR`). Never clip the live stream. Never scout Twitch for this campaign.
2. **FIND:** `python -m scripts.moments <video> [--subs reveal.srt]` ranks 12–20 s windows by reaction spike plus Pop mentions, with the payoff at about 75% of the window. Then `scripts.sheet` builds a contact sheet. Confirm the Pop is visible or talked about, and record the timestamps (`pop_visible_s` / `pop_talk_s`). Reject any window where you can't confirm it.
3. **CUT:** playbook CUT rules, plus a hook in the first 2 s, subtitles for the **whole** clip, and the stream+chat side-by-side layout from the brief. One angle per edit: the reveal, Peanut's reaction, chat losing it, a joke about the Pop.
4. **GATE:** `python -m scripts.campaign check-edit edit.json` and `check-caption caption.txt --platform tiktok|youtube`. Both must print OK.
5. **QUEUE:** for human approval in the portal. Campaign captions don't fit the GTA publisher gates (credit line, Rockstar disclaimer), so the owner posts these by hand until a campaign mode is approved for `gates.py`.
6. **LOG:** add a row to the ledger with the post URL, `posted_at`, `hook` and `caption_sha256`.
7. **WATCH:** `python -m scripts.campaign report --fetch` refreshes public stats with yt-dlp and writes `tracking/campaigns/funko-burntpeanut-report.md`.
   - **SUBMIT NOW** means at least 2K views, at least 0.5% engagement, likes visible and not yet submitted. The owner submits on Whop, then sends the demographics screenshot.
   - Momentum: any post at 3x the median or more triggers a **new** edit in the same style within 24 h.
   - Never delete a campaign post within 30 days.

### Caption template
```
<your own short line about the clip> #ad

TheBurntPeanut just got his own Funko POP! Available exclusively for pre-order at Funko.com for a limited time!
@funko @theburntpeanut
```
On YouTube, use `@OriginalFunko` instead of `@funko`. The only facts allowed are $14.99, Funko.com, pre-order and limited time. Never write stock levels, drop times, "sold out" or "official".

### Risks and owner decisions
| Risk | Impact | Mitigation / decision needed |
|---|---|---|
| **Off-niche on @clippyclubgta6** (Peanut isn't GTA, and the content is an ad) | Muddies the niche signal during warm-up, so GTA reach may drop | Owner: post on the GTA page (max 1 per platform per day, counts toward the warm-up cap) or a separate account (needs a signup, owner only) |
| **Paid-promo disclosure** (FTC; Canada Competition Act) | Legal and platform strike risk | The gate requires `#ad` or the branded-content toggle. May cost some reach, but it's required |
| **40% tier-1 audience** | $0 if missed | Post campaign clips in US/CA prime time (12–9 PM ET), **not** the 4:30 AM global slot |
| **Pop image overlay vs. CLAUDE.md "no logos/key art"** | Fewer usable windows | `pop_overlay_allowed: false` until the owner says yes |
| Caption format vs. publisher gates | The pipeline can't auto-post these | Manual posting, or approve a campaign mode in `gates.py` |
| Budget drains early | Late posts earn nothing | Front-load edits this week; recheck the budget on Whop before each batch |
| Footage on Dropbox | The cloud agent can't fetch it | Owner downloads it into `$FUNKO_FOOTAGE_DIR`, or approves the Dropbox host |
| Never comment on or DM Funko or Peanut | Rejection; also CLAUDE.md | Already a hard rule |
