import {
  cors,
  holdingsPayload,
  json,
  loadPortfolio,
  normalizeAmfi,
  schemeFor,
  schemeForKey,
  serveAmfiHoldings,
} from "./_lib.js";

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

  const amfi = normalizeAmfi(req.query.amfi || req.query.code);
  const key = String(req.query.key || "").trim();

  if (amfi) {
    const scheme = schemeFor(amfi);
    if (!scheme) {
      json(res, 404, { error: "Unknown AMFI code", amfi_code: amfi });
      return;
    }
    await serveAmfiHoldings(req, res, scheme, req.query);
    return;
  }

  if (!key) {
    json(res, 400, { error: "Pass amfi=<AMFI code> or key=<b2 key>" });
    return;
  }

  try {
    const portfolio = await loadPortfolio(key);
    json(res, 200, holdingsPayload(schemeForKey(key), portfolio), true);
  } catch (err) {
    const status = err.status || err?.$metadata?.httpStatusCode || 502;
    json(res, status === 404 || status === 400 ? status : 502, {
      error:
        status === 400
          ? "Invalid holdings key"
          : status === 404
            ? "No Data Found"
            : "Could not load holdings",
    });
  }
}
