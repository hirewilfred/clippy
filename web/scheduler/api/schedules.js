import { guard, run, body } from "../lib/http.js";
import { listSchedules, cancelSchedule } from "../lib/blotato.js";

export default async function handler(req, res) {
  if (!guard(req, res, ["GET", "DELETE"])) return;
  await run(res, async () => {
    if (req.method === "DELETE") {
      const id = String(body(req).id || "");
      if (!/^[A-Za-z0-9_-]{3,100}$/.test(id)) return res.status(400).json({ error: "Bad schedule id" });
      await cancelSchedule(id);
      return res.status(200).json({ ok: true });
    }
    res.status(200).json({ items: await listSchedules() });
  });
}
