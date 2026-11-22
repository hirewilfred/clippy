import { guard, run, body } from "../lib/http.js";
import { cleanPost, contentErrors, cadenceErrors, resolveWhen, nextSlot } from "../lib/gates.js";
import { listSchedules } from "../lib/blotato.js";
import { PLATFORMS } from "../lib/config.js";

export default async function handler(req, res) {
  if (!guard(req, res, ["POST"])) return;
  await run(res, async () => {
    const b = body(req);
    const post = cleanPost(b.post);
    let scheduled = [], note = null;
    try { scheduled = await listSchedules(); } catch (e) { note = e.message; }
    const picked = [...new Set(Array.isArray(b.platforms) ? b.platforms : PLATFORMS)].filter((p) => PLATFORMS.includes(p));
    const out = {};
    if (b.mode === "now" || b.mode === "schedule") {
      const when = resolveWhen(b.mode, b.scheduledFor);
      for (const p of PLATFORMS) out[p] = [...contentErrors(post, p), ...cadenceErrors(p, when, scheduled)];
      return res.status(200).json({ results: out, when: when.toISOString(), note });
    }
    const when = nextSlot(picked.length ? picked : PLATFORMS, scheduled);
    for (const p of PLATFORMS) out[p] = [...contentErrors(post, p), ...(when ? [] : ["no free 2-hour slot in the next 14 days"])];
    res.status(200).json({ results: out, when: when ? when.toISOString() : null, note });
  });
}
