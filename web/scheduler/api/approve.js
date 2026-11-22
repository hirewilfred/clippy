import { guard, run, body } from "../lib/http.js";
import { sign } from "../lib/auth.js";
import { cleanPost, contentErrors, lockHash } from "../lib/gates.js";

export default async function handler(req, res) {
  if (!guard(req, res, ["POST"])) return;
  await run(res, async () => {
    const b = body(req);
    if (b.watched !== true) return res.status(400).json({ error: "Confirm you watched the whole clip" });
    const post = cleanPost(b.post);
    const platforms = Array.isArray(b.platforms) ? b.platforms : [];
    if (!platforms.length) return res.status(400).json({ error: "Pick at least one platform" });
    const errors = Object.fromEntries(platforms.map((p) => [p, contentErrors(post, p)]));
    if (Object.values(errors).some((e) => e.length)) return res.status(422).json({ error: "Fix these first", errors });
    const approvedAt = new Date().toISOString();
    const token = sign({ k: "approval", h: lockHash(post), at: approvedAt });
    res.status(200).json({ token, approvedAt });
  });
}
