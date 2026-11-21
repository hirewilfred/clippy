# Clippy Club: rules for every Claude session

Read `playbook.md` before doing any pipeline work. Read `docs/HANDOFF.md` for project history and decisions.

## Hard rules (never break these)
- This repo is **public**. Never commit secrets, `.env`, video files, or personal file paths. Keys come from environment variables only, and must never be printed or logged.
- **Never post** unless all of these are true: `LIVE=true`; the clip has a valid approval whose video hash and caption hash match what is being posted; every publisher gate passes.
- Never pay for, sign up for or upgrade anything. Never DM or message anyone. Never change a social profile. Always ask the human first.
- Never automate engagement (scrolling, liking, following, commenting). Warm-up is done by hand.
- Takedown request: remove the post the same day, log it in `takedowns`, set the streamer to `blocked`, and tell the human.
- Never imply we're official. No publisher logos or key art, and never the word "official" in captions.
- Never post sexual content, animal harm, slurs, leaks or datamines, story spoilers, or AI-generated gameplay.
- The Blotato connector may be used to read status or cancel a post. It must never be used to post outside the pipeline unless the human names the exact clip and caption in that message.

## Posting limits
- At most 2 posts per hour per platform, and posts at least 2.5 hours apart.
- Follow the daily cap in `config/warmup.json`.
- No posting within 2 hours of any profile edit.
- Streamers with `allowlisted` permission may be auto-queued. `credit_only` needs per-clip human approval. `blocked` is never posted.

## Engineering
- Scripts are deterministic and testable. The agent makes decisions; scripts carry out the actions and enforce the gates.
- Anything that has side effects defaults to dry-run.
- Add a test whenever you add a gate.
