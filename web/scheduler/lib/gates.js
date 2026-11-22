// Posting rules. Port of scripts/gates.py (same rules, same messages where possible).
import crypto from "node:crypto";
import { WARMUP, TZ, MEDIA_HOST, PLATFORMS, SLOTS_ET, SLOT_LEAD_MINUTES, SLOT_SEARCH_DAYS } from "./config.js";

export const BANNED = ["official", "leak", "early access", "free copy", "giveaway"];
const CREDIT_RE = /^Credit: @[A-Za-z0-9_]{2,25} \((Twitch|Kick|YouTube)\)$/m;
const TAG_RE = /(?<![\p{L}\p{N}_])#[\p{L}\p{N}_]+/gu;
const EMOJI_RE = /[\u{1F000}-\u{1FAFF}\u{2600}-\u{27BF}\u{FE0F}]/gu;
const HANDLE_RE = /^[a-z0-9_]{2,25}$/;
const YT_DISCLAIMER = "not affiliated with rockstar games";
const MIN_LEN = 12, MAX_LEN = 20, YT_TITLE_MAX = 60;

const etFmt = new Intl.DateTimeFormat("en-CA", { timeZone: TZ, year: "numeric", month: "2-digit", day: "2-digit" });
export const etDate = (d) => etFmt.format(d);

export function dailyCap(d) {
  const day = Math.round((Date.parse(etDate(d)) - Date.parse(WARMUP.startDate)) / 864e5) + 1;
  if (day < 1) return 0;
  for (const b of WARMUP.bands) if (day >= b.from && (b.to == null || day <= b.to)) return b.cap;
  return 0;
}

const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
function banned(text, where) {
  const low = (text || "").toLowerCase();
  return BANNED.filter((w) => new RegExp(`\\b${escapeRe(w)}\\b`).test(low)).map((w) => `banned word "${w}" in ${where}`);
}

/** Normalize and type-check what the browser sends. Throws 400 on bad shape. */
export function cleanPost(p) {
  const bad = (m) => { throw Object.assign(new Error(m), { status: 400 }); };
  if (!p || typeof p !== "object") bad("Missing post");
  const s = (v) => (typeof v === "string" ? v.replace(/\r\n/g, "\n").trim() : "");
  const post = {
    mediaUrl: s(p.mediaUrl),
    thumbUrl: s(p.thumbUrl) || null,
    attachThumb: p.attachThumb === true,
    streamer: s(p.streamer).replace(/^@/, "").toLowerCase(),
    durationSec: Number(p.durationSec),
    caption: s(p.caption),
    ytTitle: s(p.ytTitle),
    ytDescription: s(p.ytDescription),
  };
  if (post.mediaUrl && !post.mediaUrl.startsWith(MEDIA_HOST)) bad("Video must be uploaded through this page");
  if (post.thumbUrl && !post.thumbUrl.startsWith(MEDIA_HOST)) bad("Thumbnail must be uploaded through this page");
  return post;
}

/** The exact things an approval locks. Any change to any of them voids it. */
export function lockHash(post) {
  const c = [post.mediaUrl, post.attachThumb ? post.thumbUrl : null, post.attachThumb, post.streamer,
    Math.round(post.durationSec * 10) / 10, post.caption, post.ytTitle, post.ytDescription];
  return crypto.createHash("sha256").update(JSON.stringify(c)).digest("hex");
}

export function contentErrors(post, platform) {
  const e = [];
  if (!PLATFORMS.includes(platform)) return [`unknown platform "${platform}"`];
  if (!post.mediaUrl) e.push("no video uploaded");
  if (!HANDLE_RE.test(post.streamer)) e.push("streamer must be a Twitch handle (letters, numbers, underscore)");
  const blocked = (process.env.BLOCKED_STREAMERS || "").toLowerCase().split(",").map((x) => x.trim()).filter(Boolean);
  if (blocked.includes(post.streamer)) e.push(`streamer "${post.streamer}" is blocked`);
  const paused = (process.env.PAUSED_PLATFORMS || "").toLowerCase().split(",").map((x) => x.trim());
  if (paused.includes(platform) || paused.includes("all")) e.push(`${platform} is paused`);
  if (!Number.isFinite(post.durationSec)) e.push("clip duration unknown");
  else if (post.durationSec < MIN_LEN || post.durationSec > MAX_LEN) e.push(`clip is ${post.durationSec.toFixed(1)}s (need ${MIN_LEN}-${MAX_LEN}s)`);

  if (platform === "tiktok") {
    const cap = post.caption;
    if (!cap) return [...e, "caption is empty"];
    if (!CREDIT_RE.test(cap)) e.push('missing credit line ("Credit: @handle (Twitch)")');
    if (post.streamer && !cap.toLowerCase().includes(`@${post.streamer}`)) e.push("credit line does not name the clip's streamer");
    const tags = cap.match(TAG_RE) || [];
    if (tags.length < 3 || tags.length > 5) e.push(`${tags.length} hashtags (need 3-5)`);
    if (new Set(tags.map((t) => t.toLowerCase())).size !== tags.length) e.push("duplicate hashtags");
    if (/[–—]/.test(cap)) e.push("caption contains an em/en dash");
    const first = cap.split("\n").find((l) => l.trim()) || "";
    if (first.trim().endsWith("?")) e.push("caption ends with a question");
    if ((first.match(EMOJI_RE) || []).length > 1) e.push("more than one emoji in caption line");
    const words = first.replace(EMOJI_RE, "").trim().split(/\s+/).filter(Boolean).length;
    if (words < 1 || words > 6) e.push(`caption line has ${words} words (need 1-6)`);
    e.push(...banned(cap, "caption"));
  } else {
    if (!post.ytTitle) e.push("YouTube title is empty");
    else if (post.ytTitle.length > YT_TITLE_MAX) e.push(`YouTube title is ${post.ytTitle.length} chars (max ${YT_TITLE_MAX})`);
    if (!CREDIT_RE.test(post.ytDescription)) e.push("YouTube description missing credit line");
    if (post.streamer && !post.ytDescription.toLowerCase().includes(`@${post.streamer}`)) e.push("YouTube credit does not name the clip's streamer");
    if (!post.ytDescription.toLowerCase().includes(YT_DISCLAIMER)) e.push("YouTube description missing Rockstar disclaimer");
    e.push(...banned(post.ytTitle, "YouTube title"), ...banned(post.ytDescription, "YouTube description"));
    if (post.attachThumb && !post.thumbUrl) e.push("thumbnail attached but not uploaded");
  }
  return e;
}

/** scheduled: normalized Blotato schedules (any platform). when: Date. */
export function cadenceErrors(platform, when, scheduled, now = new Date()) {
  const e = [];
  if (!(when instanceof Date) || Number.isNaN(+when)) return ["invalid time"];
  if (when.getTime() < now.getTime() - 60_000) e.push("time is in the past");
  const mine = scheduled.filter((s) => s.platform === platform).map((s) => new Date(s.scheduledAt)).filter((d) => !Number.isNaN(+d));
  const cap = dailyCap(when);
  const sameDay = mine.filter((d) => etDate(d) === etDate(when)).length;
  if (sameDay >= cap) e.push(`daily cap reached on ${platform}: ${sameDay}/${cap} for ${etDate(when)}`);
  const gap = WARMUP.minGapMinutes * 60_000;
  const close = mine.filter((d) => Math.abs(d - when) < gap);
  if (close.length) e.push(`another ${platform} post within ${WARMUP.minGapMinutes / 60}h (${close.map((d) => d.toISOString()).join(", ")})`);
  const inHour = mine.filter((d) => Math.abs(d - when) < 3_600_000).length;
  if (inHour + 1 > WARMUP.maxPerHour) e.push(`more than ${WARMUP.maxPerHour} ${platform} posts in an hour`);
  return e;
}

export function buildPayload(platform, post, accountId, scheduledFor) {
  let content, target;
  if (platform === "tiktok") {
    content = { text: post.caption, mediaUrls: [post.mediaUrl], platform: "tiktok" };
    target = { targetType: "tiktok", privacyLevel: "PUBLIC_TO_EVERYONE", disabledComments: false, disabledDuet: false,
      disabledStitch: false, isBrandedContent: false, isYourBrand: false, isAiGenerated: false };
  } else {
    content = { text: post.ytDescription, mediaUrls: [post.mediaUrl], platform: "youtube" };
    target = { targetType: "youtube", title: post.ytTitle, privacyStatus: "public", shouldNotifySubscribers: true,
      isMadeForKids: false, containsSyntheticMedia: false };
    if (post.attachThumb && post.thumbUrl) target.thumbnailUrl = post.thumbUrl;
  }
  const body = { post: { accountId: String(accountId), content, target } };
  if (scheduledFor) body.scheduledTime = scheduledFor.toISOString().replace(/\.\d{3}Z$/, "Z");
  return body;
}

const etHourFmt = new Intl.DateTimeFormat("en-US", { timeZone: TZ, hour: "numeric", minute: "numeric", hourCycle: "h23" });
function etHourMinute(d) {
  const parts = Object.fromEntries(etHourFmt.formatToParts(d).map((x) => [x.type, x.value]));
  return [Number(parts.hour), Number(parts.minute)];
}

/**
 * Earliest Eastern slot (SLOTS_ET, every 2 h) at least SLOT_LEAD_MINUTES from now that passes
 * cadenceErrors (daily warm-up cap, 2 h gap, max per hour) for every platform. null if none in range.
 */
export function nextSlot(platforms, scheduled, now = new Date()) {
  const from = now.getTime() + SLOT_LEAD_MINUTES * 60_000;
  let t = Math.ceil(from / 3_600_000) * 3_600_000;           // next whole hour (ET offsets are whole hours)
  const end = from + SLOT_SEARCH_DAYS * 864e5;
  for (; t <= end; t += 3_600_000) {
    const d = new Date(t);
    const [h, m] = etHourMinute(d);
    if (m !== 0 || !SLOTS_ET.includes(h)) continue;
    if (platforms.every((p) => cadenceErrors(p, d, scheduled, now).length === 0)) return d;
  }
  return null;
}

export function resolveWhen(mode, scheduledFor) {
  if (mode === "now") return new Date();
  if (mode === "next") throw Object.assign(new Error("next slot is resolved by the server"), { status: 500 });
  const d = new Date(String(scheduledFor || ""));
  if (Number.isNaN(+d)) throw Object.assign(new Error("Pick a valid date and time"), { status: 400 });
  return d;
}
