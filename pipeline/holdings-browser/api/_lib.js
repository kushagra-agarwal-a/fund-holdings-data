import { createRequire } from "node:module";
import { GetObjectCommand, HeadObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { shapeHoldingsPayload } from "./_contract.js";
import {
  datedB2Key,
  filingLinks,
  isFortnightly,
  nextFilingDate,
  noDataPayload,
  NO_DATA_FOUND,
  normalizeAsOf,
  previousFilingDate,
} from "./_filings.js";

const KEY_RE =
  /^fund-disclosures\/holdings\/(?:latest|\d{4}-\d{2}-\d{2})\/[a-z0-9.-]+\/[^/]+\/portfolio\.json$/;
const require = createRequire(import.meta.url);
const lookup = require("./amfi-lookup.json");

let s3 = null;

export function cors(res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");
}

export function json(res, status, body, cache = false) {
  cors(res);
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader(
    "Cache-Control",
    cache ? "public, s-maxage=300, stale-while-revalidate=86400" : "no-store",
  );
  res.status(status).json(body);
}

export function loadLookup() {
  return lookup;
}

export function normalizeAmfi(raw) {
  const code = String(raw || "").trim();
  if (!/^\d{4,8}$/.test(code)) return "";
  return code;
}

export function schemeFor(code) {
  return loadLookup()[code] || null;
}

export function schemeForKey(key) {
  if (!key) return null;
  for (const row of Object.values(loadLookup())) {
    if (row?.b2_key === key) return row;
  }
  return null;
}

function client() {
  if (s3) return s3;
  const keyId = process.env.B2_KEY_ID;
  const appKey = process.env.B2_APPLICATION_KEY;
  if (!keyId || !appKey) {
    throw new Error("B2 credentials are not configured");
  }
  s3 = new S3Client({
    region: process.env.B2_REGION || "us-east-005",
    endpoint: process.env.B2_ENDPOINT || "https://s3.us-east-005.backblazeb2.com",
    credentials: { accessKeyId: keyId, secretAccessKey: appKey },
    forcePathStyle: true,
  });
  return s3;
}

function isMissing(err) {
  const status = err?.$metadata?.httpStatusCode;
  const name = `${err?.name || ""} ${err?.Code || err?.code || ""}`;
  return status === 404 || /NotFound|NoSuchKey/i.test(name);
}

export async function loadPortfolio(key) {
  if (!key || key.includes("..") || !KEY_RE.test(key)) {
    const err = new Error("Invalid holdings key");
    err.status = 400;
    throw err;
  }
  try {
    const got = await client().send(
      new GetObjectCommand({
        Bucket: process.env.B2_BUCKET || "pocketedge",
        Key: key,
      }),
    );
    const buf = Buffer.from(await got.Body.transformToByteArray());
    return JSON.parse(buf.toString("utf8"));
  } catch (err) {
    if (isMissing(err)) {
      const miss = new Error(NO_DATA_FOUND);
      miss.status = 404;
      throw miss;
    }
    throw err;
  }
}

export async function portfolioExists(key) {
  if (!key || !KEY_RE.test(key)) return false;
  try {
    await client().send(
      new HeadObjectCommand({
        Bucket: process.env.B2_BUCKET || "pocketedge",
        Key: key,
      }),
    );
    return true;
  } catch (err) {
    if (isMissing(err)) return false;
    return false;
  }
}

export function holdingsPayload(scheme, portfolio) {
  return shapeHoldingsPayload(scheme || {}, portfolio || {});
}

function requestedAsOf(query) {
  return normalizeAsOf(query?.as_of || query?.date || query?.asOf || "");
}

function filingKey(scheme, asOf) {
  const latestKey = scheme?.b2_key || "";
  const latestAsOf = normalizeAsOf(scheme?.as_of) || scheme?.as_of || "";
  if (!asOf || asOf === latestAsOf) return latestKey;
  return datedB2Key(latestKey, asOf);
}

async function neighborAvailable(scheme, asOf) {
  const latestAsOf = normalizeAsOf(scheme?.as_of) || scheme?.as_of || "";
  if (asOf && asOf === latestAsOf && scheme?.b2_key) return true;
  const known = (scheme?.filings || []).map((f) => normalizeAsOf(f.as_of || f) || f.as_of);
  if (known.includes(asOf)) return true;
  return portfolioExists(datedB2Key(scheme?.b2_key, asOf));
}

async function linksFor(req, scheme, asOf, meta) {
  const fortnightly = isFortnightly(scheme, meta);
  const prev = previousFilingDate(asOf, fortnightly);
  const next = nextFilingDate(asOf, fortnightly);
  const [previousAvailable, nextAvailable] = await Promise.all([
    neighborAvailable(scheme, prev),
    neighborAvailable(scheme, next),
  ]);
  return filingLinks({
    req,
    code: scheme.amfi_code,
    asOf,
    previousAsOf: prev,
    nextAsOf: next,
    previousAvailable,
    nextAvailable,
  });
}

export async function serveAmfiHoldings(req, res, scheme, query) {
  const rawDate = query?.as_of || query?.date || query?.asOf || "";
  const asOf = requestedAsOf(query);
  if (rawDate && !asOf) {
    json(res, 400, {
      error: "Enter a valid date (YYYY-MM-DD)",
      amfi_code: scheme.amfi_code,
    });
    return;
  }

  if (!scheme.has_holdings || !scheme.b2_key) {
    json(res, 404, {
      error: NO_DATA_FOUND,
      amfi_code: scheme.amfi_code,
      as_of: asOf || scheme.as_of || null,
      scheme: {
        name: scheme.name,
        amc_name: scheme.amc_name,
        parent_name: scheme.parent_name,
      },
    });
    return;
  }

  const want = asOf || normalizeAsOf(scheme.as_of) || scheme.as_of;
  const key = filingKey(scheme, asOf);
  try {
    const portfolio = await loadPortfolio(key);
    const payload = holdingsPayload(scheme, portfolio);
    const filingAsOf = payload.meta?.as_of || want;
    if (asOf && filingAsOf && filingAsOf !== asOf) {
      const links = await linksFor(req, scheme, asOf, payload.meta);
      json(res, 404, noDataPayload({ scheme, asOf, links }), true);
      return;
    }
    const links = await linksFor(req, scheme, filingAsOf, payload.meta);
    json(res, 200, { ...payload, links }, true);
  } catch (err) {
    const status = err.status || err?.$metadata?.httpStatusCode || 502;
    if (status === 404) {
      const links = want
        ? await linksFor(req, scheme, want, { disclosure_type: scheme.disclosure_type })
        : null;
      json(res, 404, noDataPayload({ scheme, asOf: want, links }), true);
      return;
    }
    json(res, status === 400 ? 400 : 502, {
      error: status === 400 ? "Invalid holdings key" : "Could not load holdings",
      amfi_code: scheme.amfi_code,
      as_of: asOf || null,
    });
  }
}
