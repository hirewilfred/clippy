// Handler-level tests with throwaway data, LIVE unset, Blotato stubbed. Never calls the real API.
import test from "node:test";
import assert from "node:assert/strict";

process.env.SCHEDULER_PASSWORD = "pw-for-tests";
process.env.BLOTATO_API_KEY = "stub";
delete process.env.LIVE;

const calls = [];
global.fetch = async (url, opts = {}) => {
  calls.push({ url, method: opts.method });
  if (url.includes("/schedules")) return new Response(JSON.stringify({ items: [] }), { status: 200 });
  if (url.endsWith("/posts")) return new Response(JSON.stringify({ postSubmissionId: "ps_test" }), { status: 201 });
  return new Response("{}", { status: 404 });
};

const { sessionCookie } = await import("../lib/auth.js");
const approve = (await import("../api/approve.js")).default;
const post = (await import("../api/post.js")).default;
const login = (await import("../api/login.js")).default;

function call(handler, { method = "POST", body = {}, authed = true, origin } = {}) {
  return new Promise((resolve) => {
    const headers = { host: "app.test", ...(origin ? { origin } : {}) };
    if (authed) headers.cookie = sessionCookie().split(";")[0];
    const req = { method, body, headers };
    const res = { code: 200, hdr: {}, status(c) { this.code = c; return this; }, setHeader(k, v) { this.hdr[k] = v; },
      json(o) { resolve({ code: this.code, body: o, hdr: this.hdr }); } };
    handler(req, res);
  });
}
const P = {
  mediaUrl: "https://database.blotato.io/x.mp4", streamer: "hutchmf", durationSec: 15.6,
  caption: "he trusted the bushes\n\nCredit: @hutchmf (Twitch)\n\n#gta6 #hutchmf #gaming",
  ytTitle: "He said aim for the bushes in GTA 6 #gta6",
  ytDescription: "Credit: @hutchmf (Twitch)\n\nFan page. Not affiliated with Rockstar Games.",
};
const future = new Date(Date.now() + 3 * 86400e3).toISOString();

test("login: wrong password refused, right one sets a cookie", async () => {
  assert.equal((await call(login, { authed: false, body: { password: "nope" } })).code, 401);
  const ok = await call(login, { authed: false, body: { password: "pw-for-tests" } });
  assert.equal(ok.code, 200); assert.match(ok.hdr["Set-Cookie"], /HttpOnly; Secure; SameSite=Strict/);
});

test("signed-out and cross-origin requests are refused", async () => {
  assert.equal((await call(approve, { authed: false, body: { post: P, platforms: ["tiktok"], watched: true } })).code, 401);
  assert.equal((await call(approve, { origin: "https://evil.example", body: { post: P, platforms: ["tiktok"], watched: true } })).code, 403);
});

test("approve requires the watched box and passing content", async () => {
  assert.equal((await call(approve, { body: { post: P, platforms: ["tiktok"] } })).code, 400);
  const bad = await call(approve, { body: { post: { ...P, caption: "no credit" }, platforms: ["tiktok"], watched: true } });
  assert.equal(bad.code, 422);
});

test("dry-run post: never calls Blotato /posts; edits after approval are refused", async () => {
  const a = await call(approve, { body: { post: P, platforms: ["tiktok", "youtube"], watched: true } });
  assert.equal(a.code, 200);
  const before = calls.filter((c) => c.url.endsWith("/posts")).length;
  const r = await call(post, { body: { post: P, token: a.body.token, platforms: ["tiktok", "youtube"], mode: "schedule", scheduledFor: future, confirm: true } });
  assert.equal(r.code, 200, JSON.stringify(r.body));
  assert.equal(r.body.live, false);
  assert.equal(r.body.results.tiktok.dryRun, true);
  assert.equal(calls.filter((c) => c.url.endsWith("/posts")).length, before);
  const edited = await call(post, { body: { post: { ...P, caption: P.caption.replace("trusted", "believed") }, token: a.body.token, platforms: ["tiktok"], mode: "schedule", scheduledFor: future, confirm: true } });
  assert.equal(edited.code, 403);
  const forged = await call(post, { body: { post: P, token: a.body.token + "x", platforms: ["tiktok"], mode: "schedule", scheduledFor: future, confirm: true } });
  assert.equal(forged.code, 403);
  const noConfirm = await call(post, { body: { post: P, token: a.body.token, platforms: ["tiktok"], mode: "schedule", scheduledFor: future } });
  assert.equal(noConfirm.code, 400);
});

test("live post calls Blotato once per platform", async () => {
  process.env.LIVE = "true"; process.env.BLOTATO_TIKTOK_ACCOUNT_ID = "70702"; process.env.BLOTATO_YOUTUBE_ACCOUNT_ID = "58996";
  const a = await call(approve, { body: { post: P, platforms: ["tiktok", "youtube"], watched: true } });
  const before = calls.filter((c) => c.url.endsWith("/posts")).length;
  const r = await call(post, { body: { post: P, token: a.body.token, platforms: ["tiktok", "youtube"], mode: "schedule", scheduledFor: future, confirm: true } });
  assert.equal(r.code, 200, JSON.stringify(r.body));
  assert.equal(r.body.results.youtube.postSubmissionId, "ps_test");
  assert.equal(calls.filter((c) => c.url.endsWith("/posts")).length, before + 2);
  delete process.env.LIVE;
});
