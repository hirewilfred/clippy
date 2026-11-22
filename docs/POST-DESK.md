# Post desk: setup on Windows (about 10 minutes, once)

The Post desk is a page on your own PC for posting clips by hand. Every rule still applies: you approve the exact video and text, the gates check it, then you confirm.

## 1. Install (once)
Open **PowerShell** and run each line:
```
winget install Python.Python.3.12
winget install Gyan.FFmpeg
winget install Git.Git
```
Close and reopen PowerShell after installing.

## 2. Get the code (once)
The repo is public, so no GitHub login is needed:
```
cd $HOME
git clone https://github.com/hirewilfred/clippy
cd clippy
python -m pip install -r requirements.txt
```
To update later: `cd $HOME\clippy` then `git pull`.

## 3. Start the desk
```
cd $HOME\clippy
python -m scripts.desk
```
Open **http://127.0.0.1:8765** in your browser. Leave PowerShell open while you use it.

## 4. Post a clip
1. Put an `.mp4` (vertical, 12-20 s) into the `clippy\inbox` folder and refresh the page.
2. **Add clip**: pick it, type the streamer's Twitch handle and the hook.
3. Edit the TikTok caption (1-6 words, credit line, 3-5 hashtags) and the YouTube title/description. Click **Save text**.
4. Check the thumbnail; change the text or frame and **Regenerate** if needed.
5. Watch the clip, tick the box, **Approve**.
6. **Post**: pick TikTok/YouTube and Schedule or Post now, then **Review post**, then **Confirm** (or Cancel).

## 5. Turn on real posting
Until you do this, every post is a **dry-run**: the desk shows exactly what it would send, and nothing goes out.

Create a file named `.env` in the `clippy` folder (it's never uploaded to GitHub):
```
BLOTATO_API_KEY=paste-your-key-here
LIVE=true
```
Restart the desk. The banner turns red: **LIVE**.

## Good to know
- **Editing text after approval voids the approval.** Re-approve.
- **Dry-runs don't use up the daily cap.** Only real posts count.
- **YouTube thumbnails** are only applied if your YouTube channel is verified (YouTube Studio > Settings > Channel > Feature eligibility).
- **To cancel a scheduled post**, delete it in Blotato > Scheduled, then click **Mark cancelled** in the desk.
- **The daily caps follow the warm-up plan** in `config/warmup.json`.
