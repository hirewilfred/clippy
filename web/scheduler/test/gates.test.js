import test from "node:test";
import assert from "node:assert/strict";
import { contentErrors, cadenceErrors, dailyCap, lockHash, buildPayload, cleanPost } from "../lib/gates.js";

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
  // 7:45 PM and 10:00 PM are 2h15m apart: blocked; 10:15 PM passes
  const tonight = [{ platform: "youtube", scheduledAt: "2026-11-22T00:45:00Z" }];
  assert.ok(cadenceErrors("youtube", at("2026-11-22T03:00:00Z"), tonight, now).some((e) => e.includes("within")));
  assert.deepEqual(cadenceErrors("youtube", at("2026-11-22T03:15:00Z"), tonight, now), []);
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
