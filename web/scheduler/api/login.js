import { checkPassword, sessionCookie, clearCookie } from "../lib/auth.js";
import { guard, run, body } from "../lib/http.js";

export default async function handler(req, res) {
  if (!guard(req, res, ["POST", "DELETE"], { auth: false })) return;
  await run(res, async () => {
    if (req.method === "DELETE") { res.setHeader("Set-Cookie", clearCookie()); return res.status(200).json({ ok: true }); }
    if (!(process.env.DASHBOARD_PASSWORD || process.env.SCHEDULER_PASSWORD) || !process.env.BLOTATO_API_KEY)
      return res.status(503).json({ error: "Not set up yet: add DASHBOARD_PASSWORD (same as the Clippy portal) and BLOTATO_API_KEY in Vercel > clippy-schedule > Settings > Environment Variables, then redeploy." });
    await new Promise((r) => setTimeout(r, 400)); // slow down guessing
    if (!checkPassword(body(req).password)) return res.status(401).json({ error: "Wrong password" });
    res.setHeader("Set-Cookie", sessionCookie());
    res.status(200).json({ ok: true });
  });
}
