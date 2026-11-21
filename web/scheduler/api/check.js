import { guard, run, body } from "../lib/http.js";
import { cleanPost, contentErrors, cadenceErrors, resolveWhen } from "../lib/gates.js";
import { listSchedules } from "../lib/blotato.js";
import { PLATFORMS } from "../lib/config.js";

export default async function handler(req, res) {
  if (!guard(req, res, ["POST"])) return;
  await run(res, async () => {
    const b = body(req);
    const post = cleanPost(b.post);
    const when = resolveWhen(b.mode, b.scheduledFor);
    let scheduled = [], note = null;
    try { scheduled = await listSchedules(); } catch (e) { note = e.message; }
    const out = {};
    for (const p of PLATFORMS) out[p] = [...contentErrors(post, p), ...cadenceErrors(p, when, scheduled)];
    res.status(200).json({ results: out, when: when.toISOString(), note });
  });
}
