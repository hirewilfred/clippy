const BASE = "https://backend.blotato.com/v2";

export class BlotatoError extends Error {
  constructor(message, status = 502) { super(message); this.status = status; }
}

async function call(method, path, payload) {
  const key = process.env.BLOTATO_API_KEY;
  if (!key) throw new BlotatoError("BLOTATO_API_KEY is not set. Add it in Vercel > clippy-schedule > Settings > Environment Variables, then redeploy.", 503);
  const r = await fetch(BASE + path, {
    method,
    headers: { "blotato-api-key": key, "Content-Type": "application/json" },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  const text = await r.text();
  let j = {};
  try { j = text ? JSON.parse(text) : {}; } catch { /* non-JSON */ }
  if (!r.ok) throw new BlotatoError(`Blotato ${method} ${path.split("?")[0]} failed (${r.status}): ${String(j.message || text || "").slice(0, 200)}`);
  return j;
}

export function accountId(platform) {
  const v = process.env[platform === "tiktok" ? "BLOTATO_TIKTOK_ACCOUNT_ID" : "BLOTATO_YOUTUBE_ACCOUNT_ID"];
  if (!v) throw new BlotatoError(`Blotato account id for ${platform} is not set`, 503);
  return v;
}

export function normalizeSchedule(s) {
  const d = s.draft || {};
  const c = d.content || {};
  const t = d.target || {};
  return {
    id: s.id,
    scheduledAt: s.scheduledAt,
    platform: c.platform || t.targetType || (s.account && s.account.platform) || "unknown",
    text: c.text || "",
    title: t.title || null,
    mediaUrls: c.mediaUrls || [],
  };
}

export async function listSchedules() {
  const out = [];
  let cursor;
  for (let i = 0; i < 10; i++) {
    const j = await call("GET", `/schedules?limit=50${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`);
    out.push(...(j.items || []));
    cursor = j.nextCursor || j.cursor || null;
    if (!cursor || !(j.items || []).length) break;
  }
  return out.map(normalizeSchedule).sort((a, b) => String(a.scheduledAt).localeCompare(String(b.scheduledAt)));
}

export const cancelSchedule = (id) => call("DELETE", `/schedules/${encodeURIComponent(id)}`);
export const presign = (filename) => call("POST", "/media/uploads", { filename });
export const createPost = (payload) => call("POST", "/posts", payload);
