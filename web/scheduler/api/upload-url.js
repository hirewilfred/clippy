import { guard, run, body } from "../lib/http.js";
import { presign } from "../lib/blotato.js";

const OK = /^[A-Za-z0-9._ -]{1,120}\.(mp4|mov|jpg|jpeg|png)$/i;

export default async function handler(req, res) {
  if (!guard(req, res, ["POST"])) return;
  await run(res, async () => {
    const name = String(body(req).filename || "").split(/[\\/]/).pop();
    if (!OK.test(name)) return res.status(400).json({ error: "Only .mp4/.mov videos and .jpg/.png images" });
    const r = await presign(name.replace(/\s+/g, "-"));
    res.status(200).json({ presignedUrl: r.presignedUrl, publicUrl: r.publicUrl });
  });
}
