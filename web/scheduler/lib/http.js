import { isAuthed } from "./auth.js";

/** Method check, same-origin check on writes, and sign-in check. Returns false if it already responded. */
export function guard(req, res, methods, { auth = true } = {}) {
  if (!methods.includes(req.method)) { res.status(405).json({ error: "Method not allowed" }); return false; }
  if (req.method !== "GET") {
    const origin = req.headers.origin;
    const host = req.headers["x-forwarded-host"] || req.headers.host;
    if (origin) {
      let oh = "";
      try { oh = new URL(origin).host; } catch { /* bad origin */ }
      if (oh !== host) { res.status(403).json({ error: "Cross-origin request refused" }); return false; }
    }
  }
  if (auth && !isAuthed(req)) { res.status(401).json({ error: "Sign in first" }); return false; }
  return true;
}

export async function run(res, fn) {
  try { await fn(); }
  catch (e) { res.status(e.status || 500).json({ error: e.message || "Server error" }); }
}

export const body = (req) => (req.body && typeof req.body === "object" ? req.body : {});
