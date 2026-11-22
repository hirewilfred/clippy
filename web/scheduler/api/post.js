import { guard, run, body } from "../lib/http.js";
import { verify } from "../lib/auth.js";
import { cleanPost, contentErrors, cadenceErrors, lockHash, buildPayload, resolveWhen, nextSlot } from "../lib/gates.js";
import { listSchedules, createPost, accountId } from "../lib/blotato.js";
import { PLATFORMS } from "../lib/config.js";

const APPROVAL_TTL_MS = 24 * 3600 * 1000;

export default async function handler(req, res) {
  if (!guard(req, res, ["POST"])) return;
  await run(res, async () => {
    const b = body(req);
    if (b.confirm !== true) return res.status(400).json({ error: "Confirm on the review screen first" });
    const post = cleanPost(b.post);
    const a = verify(b.token);
    if (!a || a.k !== "approval") return res.status(403).json({ error: "Not approved" });
    if (a.h !== lockHash(post)) return res.status(403).json({ error: "The video or text changed after approval. Approve again." });
    if (Date.now() - Date.parse(a.at) > APPROVAL_TTL_MS) return res.status(403).json({ error: "Approval expired (24h). Approve again." });
    const platforms = [...new Set(Array.isArray(b.platforms) ? b.platforms : [])].filter((p) => PLATFORMS.includes(p));
    if (!platforms.length) return res.status(400).json({ error: "Pick at least one platform" });
    const mode = b.mode === "now" ? "now" : b.mode === "schedule" ? "schedule" : "next";
    const scheduled = await listSchedules();
    let when;
    if (mode === "next") {
      when = nextSlot(platforms, scheduled);
      if (!when) return res.status(422).json({ error: "No free 2-hour slot in the next 14 days (warm-up caps are full)" });
    } else {
      when = resolveWhen(mode, b.scheduledFor);
      if (mode === "schedule" && when.getTime() < Date.now() + 2 * 60_000) return res.status(400).json({ error: "Schedule at least 2 minutes ahead, or use Post now" });
    }

    const errors = Object.fromEntries(platforms.map((p) => [p, [...contentErrors(post, p), ...cadenceErrors(p, when, scheduled)]]));
    if (Object.values(errors).some((e) => e.length)) return res.status(422).json({ error: "Blocked by posting rules", errors });

    const live = process.env.LIVE === "true";
    const results = {};
    for (const p of platforms) {
      const payload = buildPayload(p, post, live ? accountId(p) : "<account id>", mode === "now" ? null : when);
      if (!live) { results[p] = { dryRun: true, payload }; continue; }
      try {
        const r = await createPost(payload);
        results[p] = { ok: true, postSubmissionId: r.postSubmissionId, scheduledTime: r.scheduledTime || null };
      } catch (e) {
        results[p] = { ok: false, error: e.message };
      }
    }
    res.status(200).json({ live, results, when: mode === "now" ? null : when.toISOString() });
  });
}
