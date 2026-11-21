import crypto from "node:crypto";

const COOKIE = "clippy_sched";
const DAY = 86400;

// Signing key: SESSION_SECRET if set, otherwise derived from the Blotato key (already a server-only secret).
// Changing either one signs everyone out and voids pending approvals.
function secret() {
  const s = process.env.SESSION_SECRET;
  if (s && s.length >= 16) return s;
  const k = process.env.BLOTATO_API_KEY;
  if (!k) throw Object.assign(new Error("Server not configured: set BLOTATO_API_KEY in Vercel"), { status: 503 });
  return crypto.createHash("sha256").update("clippy-schedule/session/v1:" + k).digest("hex");
}

export function sign(payload) {
  const body = Buffer.from(JSON.stringify(payload)).toString("base64url");
  const mac = crypto.createHmac("sha256", secret()).update(body).digest("base64url");
  return `${body}.${mac}`;
}

export function verify(token) {
  if (typeof token !== "string") return null;
  const [body, mac] = token.split(".");
  if (!body || !mac) return null;
  const want = crypto.createHmac("sha256", secret()).update(body).digest("base64url");
  const a = Buffer.from(mac), b = Buffer.from(want);
  if (a.length !== b.length || !crypto.timingSafeEqual(a, b)) return null;
  try { return JSON.parse(Buffer.from(body, "base64url").toString("utf8")); } catch { return null; }
}

export function checkPassword(pw) {
  const real = process.env.SCHEDULER_PASSWORD || "";
  if (!real) return false;
  const a = crypto.createHash("sha256").update(String(pw ?? "")).digest();
  const b = crypto.createHash("sha256").update(real).digest();
  return crypto.timingSafeEqual(a, b);
}

export const sessionCookie = () =>
  `${COOKIE}=${sign({ k: "session", exp: Date.now() + 30 * DAY * 1000 })}; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=${30 * DAY}`;
export const clearCookie = () => `${COOKIE}=; Path=/; HttpOnly; Secure; SameSite=Strict; Max-Age=0`;

export function isAuthed(req) {
  const m = (req.headers.cookie || "").match(/(?:^|;\s*)clippy_sched=([^;]+)/);
  let p = null;
  try { p = m && verify(m[1]); } catch { return false; }
  return !!(p && p.k === "session" && p.exp > Date.now());
}
