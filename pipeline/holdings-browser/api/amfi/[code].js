import {
  cors,
  json,
  normalizeAmfi,
  schemeFor,
  serveAmfiHoldings,
} from "../_lib.js";

export default async function handler(req, res) {
  cors(res);
  if (req.method === "OPTIONS") {
    res.status(204).end();
    return;
  }
  if (req.method !== "GET") {
    res.setHeader("Allow", "GET, OPTIONS");
    json(res, 405, { error: "Method not allowed" });
    return;
  }

  const code = normalizeAmfi(req.query.code);
  if (!code) {
    json(res, 400, { error: "Enter a valid AMFI scheme code" });
    return;
  }

  const scheme = schemeFor(code);
  if (!scheme) {
    json(res, 404, { error: "Unknown AMFI code", amfi_code: code });
    return;
  }

  await serveAmfiHoldings(req, res, scheme, req.query);
}
