import { isAuthed } from "../lib/auth.js";

export default function handler(req, res) {
  res.status(200).json({
    signedIn: isAuthed(req),
    live: process.env.LIVE === "true",
    blotatoConfigured: !!process.env.BLOTATO_API_KEY,
    passwordConfigured: !!(process.env.DASHBOARD_PASSWORD || process.env.SCHEDULER_PASSWORD),
  });
}
