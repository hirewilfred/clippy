// Local test server that mimics Vercel's Node runtime (static index.html + /api/* handlers). Not deployed.
import http from "node:http";
import fs from "node:fs";
const port = Number(process.env.PORT || 8790);
http.createServer(async (req, res) => {
  res.status = (c) => { res.statusCode = c; return res; };
  res.json = (o) => { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(o)); };
  const url = new URL(req.url, "http://x");
  if (!url.pathname.startsWith("/api/")) { res.setHeader("Content-Type", "text/html"); return res.end(fs.readFileSync("index.html")); }
  let raw = ""; for await (const c of req) raw += c;
  try { req.body = raw ? JSON.parse(raw) : {}; } catch { req.body = {}; }
  const name = url.pathname.slice(5).replace(/[^a-z-]/g, "");
  if (!fs.existsSync(`api/${name}.js`)) { res.statusCode = 404; return res.end("{}"); }
  const mod = await import(`./api/${name}.js?${Date.now()}`);
  await mod.default(req, res);
}).listen(port, "127.0.0.1", () => console.log("dev on", port));
