import test from "node:test";
import assert from "node:assert/strict";
import { contentErrors, cadenceErrors, dailyCap, lockHash, buildPayload, cleanPost, nextSlot } from "../lib/gates.js";

const base = () => cleanPost({
  mediaUrl: "https://database.blotato.io/x.mp4", streamer: "@HutchMF", durationSec: 15.6,
  caption: "he trusted the bushes 💀\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #hutchmf #gaming #funnymoments",
  ytTitle: "He said aim for the bushes in GTA 6 #gta6",
  ytDescription: "Credit: @hutchmf (Twitch)\nhttps://www.twitch.tv/hutchmf\n\n#gta6 #hutchmf #gaming\n\nFan page. Not affiliated with Rockstar Games.",
});
const at = (s) => new Date(s);

test("good post passes both platforms", () => {
  assert.deepEqual(contentErrors(base(), "tiktok"), []);
  assert.deepEqual(contentErrors(base(), "youtube"), []);
});

test("caption rules", () => {
  const bad = (caption, needle) => {
    const e = contentErrors({ ...base(), caption }, "tiktok");
    assert.ok(e.some((x) => x.includes(needle)), `${needle} not in ${JSON.stringify(e)}`);
  };
  bad("funny\n\n#gta6 #a #b", "missing credit line");
  bad("funny\n\nCredit: @other (Twitch)\n\n#gta6 #a #b", "does not name");
  bad("funny\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a", "2 hashtags");
  bad("funny\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a #b #c #d #e", "6 hashtags");
  bad("did he?\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a #b", "question");
  bad("official clip\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a #b", "official");
  bad("a — b\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a #b", "dash");
  bad("one two three four five six seven\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a #b", "words");
  bad("lol 💀😂\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a #b", "emoji");
  bad("x\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #GTA6 #b", "duplicate");
  assert.equal(contentErrors({ ...base(), caption: "leaky roof\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #a #b" }, "tiktok").filter((x) => x.includes("banned")).length, 0);
});

test("youtube rules and duration", () => {
  assert.ok(contentErrors({ ...base(), ytTitle: "x".repeat(61) }, "youtube").some((e) => e.includes("max 60")));
  assert.ok(contentErrors({ ...base(), ytDescription: "Credit: @hutchmf (Twitch)" }, "youtube").some((e) => e.includes("disclaimer")));
  assert.ok(contentErrors({ ...base(), durationSec: 11.9 }, "tiktok").some((e) => e.includes("need 12-20")));
  assert.ok(contentErrors({ ...base(), durationSec: NaN }, "tiktok").some((e) => e.includes("unknown")));
});

test("blocked streamer and paused platform via env", () => {
  process.env.BLOCKED_STREAMERS = "hutchmf"; process.env.PAUSED_PLATFORMS = "youtube";
  assert.ok(contentErrors(base(), "tiktok").some((e) => e.includes("blocked")));
  assert.ok(contentErrors(base(), "youtube").some((e) => e.includes("paused")));
  delete process.env.BLOCKED_STREAMERS; delete process.env.PAUSED_PLATFORMS;
});

test("media must come from Blotato storage", () => {
  assert.throws(() => cleanPost({ mediaUrl: "https://evil.example/x.mp4" }), /uploaded through this page/);
});

test("warm-up caps (Eastern calendar days)", () => {
  assert.equal(dailyCap(at("2026-11-22T00:45:00Z")), 2);  // 7:45 PM Nov 21 ET = day 1 (raised to 2)
  assert.equal(dailyCap(at("2026-11-22T18:00:00Z")), 2);  // Nov 22 = day 2
  assert.equal(dailyCap(at("2026-11-24T15:00:00Z")), 4);
  assert.equal(dailyCap(at("2027-01-01T15:00:00Z")), 8);
  assert.equal(dailyCap(at("2026-11-20T15:00:00Z")), 0);
});

test("cadence: gap, daily cap, other platform ignored", () => {
  const now = at("2026-11-21T23:00:00Z");
  const sched = [{ platform: "tiktok", scheduledAt: "2026-11-22T09:30:00Z" }, { platform: "tiktok", scheduledAt: "2026-11-22T18:00:00Z" }];
  assert.deepEqual(cadenceErrors("tiktok", at("2026-11-22T00:45:00Z"), sched, now), []);        // 7:45 PM Nov 21
  assert.ok(cadenceErrors("tiktok", at("2026-11-22T22:00:00Z"), sched, now).some((e) => e.includes("daily cap")));  // 3rd on Nov 22
  assert.ok(cadenceErrors("tiktok", at("2026-11-22T08:00:00Z"), sched, now).some((e) => e.includes("within")));     // 1.5h before 4:30
  assert.deepEqual(cadenceErrors("youtube", at("2026-11-22T22:00:00Z"), sched, now), []);
  assert.ok(cadenceErrors("tiktok", at("2026-11-21T20:00:00Z"), [], now).some((e) => e.includes("past")));
  // 2 h minimum gap: 7:45 PM then 9:30 PM (1h45m) is blocked; 9:45 PM (exactly 2 h) passes
  const tonight = [{ platform: "youtube", scheduledAt: "2026-11-22T00:45:00Z" }];
  assert.ok(cadenceErrors("youtube", at("2026-11-22T02:30:00Z"), tonight, now).some((e) => e.includes("within")));
  assert.deepEqual(cadenceErrors("youtube", at("2026-11-22T02:45:00Z"), tonight, now), []);
});

test("lock hash changes with any locked field", () => {
  const h = lockHash(base());
  for (const k of ["mediaUrl", "streamer", "caption", "ytTitle", "ytDescription"]) assert.notEqual(lockHash({ ...base(), [k]: base()[k] + "x" }), h, k);
  assert.notEqual(lockHash({ ...base(), durationSec: 15.9 }), h);
  assert.notEqual(lockHash({ ...base(), attachThumb: true, thumbUrl: "https://database.blotato.io/t.jpg" }), h);
  assert.equal(lockHash(base()), h);
});

test("payloads match Blotato v2 and put scheduledTime at the root", () => {
  const t = buildPayload("tiktok", base(), "70702", at("2026-11-22T00:45:00Z"));
  assert.equal(t.scheduledTime, "2026-11-22T00:45:00Z");
  assert.equal(t.post.scheduledTime, undefined);
  assert.equal(t.post.target.isAiGenerated, false);
  assert.equal(t.post.content.text, base().caption);
  const y = buildPayload("youtube", { ...base(), attachThumb: true, thumbUrl: "https://database.blotato.io/t.jpg" }, "58996", null);
  assert.equal(y.scheduledTime, undefined);
  assert.equal(y.post.target.isMadeForKids, false);
  assert.equal(y.post.target.thumbnailUrl, "https://database.blotato.io/t.jpg");
  assert.equal(y.post.content.text, base().ytDescription);
  assert.equal(buildPayload("youtube", base(), "1", null).post.target.thumbnailUrl, undefined);
});

test("nextSlot: even Eastern hours, 2 h apart, respects caps", () => {
  const sch = (iso, platform = "tiktok") => ({ platform, scheduledAt: iso });
  // Sun Nov 22 2026, 11:20 AM ET (EST = UTC-5). Day 2 cap is 2 per platform.
  const now = at("2026-11-22T16:20:00Z");
  assert.equal(nextSlot(["tiktok"], [], now).toISOString(), "2026-11-22T17:00:00.000Z");            // 12:00 PM ET
  // 12:00 PM taken -> 2:00 PM (exactly 2 h later is allowed)
  assert.equal(nextSlot(["tiktok"], [sch("2026-11-22T17:00:00Z")], now).toISOString(), "2026-11-22T19:00:00.000Z");
  // 12:00 and 2:00 PM taken -> day 2 cap (2) is full -> next day 8:00 AM ET
  const two = [sch("2026-11-22T17:00:00Z"), sch("2026-11-22T19:00:00Z")];
  assert.equal(nextSlot(["tiktok"], two, now).toISOString(), "2026-11-23T13:00:00.000Z");
  // YouTube is free, but both platforms together must share one slot
  assert.equal(nextSlot(["youtube"], two, now).toISOString(), "2026-11-22T17:00:00.000Z");
  assert.equal(nextSlot(["tiktok", "youtube"], two, now).toISOString(), "2026-11-23T13:00:00.000Z");
  // 11:55 AM: the 12:00 slot is inside the 10-minute lead, so 2:00 PM
  assert.equal(nextSlot(["tiktok"], [], at("2026-11-22T16:55:00Z")).toISOString(), "2026-11-22T19:00:00.000Z");
  // 11 PM: next slot is 8:00 AM tomorrow, never overnight
  assert.equal(nextSlot(["tiktok"], [], at("2026-11-23T04:00:00Z")).toISOString(), "2026-11-23T13:00:00.000Z");
});
