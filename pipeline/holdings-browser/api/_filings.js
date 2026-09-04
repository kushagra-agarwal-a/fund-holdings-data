/** Filing dates, B2 key layout, and previous/next links. */

export const NO_DATA_FOUND = "No Data Found";

export const LINK_KEYS = ["as_of", "href", "message"];

const MONTHS = {
  jan: 1,
  january: 1,
  feb: 2,
  february: 2,
  mar: 3,
  march: 3,
  apr: 4,
  april: 4,
  may: 5,
  jun: 6,
  june: 6,
  jul: 7,
  july: 7,
  aug: 8,
  august: 8,
  sep: 9,
  sept: 9,
  september: 9,
  oct: 10,
  october: 10,
  nov: 11,
  november: 11,
  dec: 12,
  december: 12,
};

const FORTNIGHTLY_RE =
  /\b(debt|liquid|overnight|money\s*market|gilt|credit\s*risk|floater|ultra\s*short|ultrashort|low\s*duration|short\s*duration|medium\s*duration|corporate\s*bond|banking\s*(?:and|&)\s*psu|dynamic\s*bond)\b/i;

export function lastDayOfMonth(year, month) {
  return new Date(Date.UTC(year, month, 0)).getUTCDate();
}

export function formatDate(year, month, day) {
  return `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
}

function parts(asOf) {
  const [y, m, d] = String(asOf).split("-").map(Number);
  return { y, m, d };
}

export function normalizeAsOf(raw) {
  const s = String(raw || "").trim();
  if (!s) return "";
  let m = s.match(/^(\d{4})-(\d{1,2})-(\d{1,2})$/);
  if (m) {
    const y = Number(m[1]);
    const mo = Number(m[2]);
    const d = Number(m[3]);
    if (mo < 1 || mo > 12) return "";
    const dim = lastDayOfMonth(y, mo);
    if (d < 1 || d > dim) return "";
    return formatDate(y, mo, d);
  }
  m = s.match(/^(\d{4})-(\d{1,2})$/);
  if (m) {
    const y = Number(m[1]);
    const mo = Number(m[2]);
    if (mo < 1 || mo > 12) return "";
    return formatDate(y, mo, lastDayOfMonth(y, mo));
  }
  m = s.match(/^(\d{1,2})[\/.\-](\d{1,2})[\/.\-](\d{4})$/);
  if (m) {
    const d = Number(m[1]);
    const mo = Number(m[2]);
    const y = Number(m[3]);
    if (mo < 1 || mo > 12) return "";
    const dim = lastDayOfMonth(y, mo);
    if (d < 1 || d > dim) return "";
    return formatDate(y, mo, d);
  }
  m = s.match(/^(\d{1,2})[\/.\-\s]+([A-Za-z]+)[\/.\-\s,]+(\d{4})$/);
  if (m) {
    const mo = MONTHS[m[2].toLowerCase()];
    if (!mo) return "";
    const d = Number(m[1]);
    const y = Number(m[3]);
    const dim = lastDayOfMonth(y, mo);
    if (d < 1 || d > dim) return "";
    return formatDate(y, mo, d);
  }
  return "";
}

export function isFortnightly(scheme, meta) {
  const dtype = String(meta?.disclosure_type || scheme?.disclosure_type || "").toLowerCase();
  if (dtype === "fortnightly") return true;
  if (dtype === "monthly") return false;
  const blob = `${scheme?.category || ""} ${scheme?.name || ""}`;
  return FORTNIGHTLY_RE.test(blob);
}

export function previousFilingDate(asOf, fortnightly) {
  const { y, m, d } = parts(asOf);
  if (fortnightly) {
    if (d > 15) return formatDate(y, m, 15);
    const py = m === 1 ? y - 1 : y;
    const pm = m === 1 ? 12 : m - 1;
    return formatDate(py, pm, lastDayOfMonth(py, pm));
  }
  const py = m === 1 ? y - 1 : y;
  const pm = m === 1 ? 12 : m - 1;
  return formatDate(py, pm, lastDayOfMonth(py, pm));
}

export function nextFilingDate(asOf, fortnightly) {
  const { y, m, d } = parts(asOf);
  const eom = lastDayOfMonth(y, m);
  if (fortnightly) {
    if (d < 15) return formatDate(y, m, 15);
    if (d < eom) return formatDate(y, m, eom);
    const ny = m === 12 ? y + 1 : y;
    const nm = m === 12 ? 1 : m + 1;
    return formatDate(ny, nm, 15);
  }
  if (d < eom) return formatDate(y, m, eom);
  const ny = m === 12 ? y + 1 : y;
  const nm = m === 12 ? 1 : m + 1;
  return formatDate(ny, nm, lastDayOfMonth(ny, nm));
}

export function datedB2Key(latestKey, asOf) {
  if (!latestKey || !asOf) return "";
  if (latestKey.includes("/holdings/latest/")) {
    return latestKey.replace("/holdings/latest/", `/holdings/${asOf}/`);
  }
  return latestKey.replace(
    /\/holdings\/\d{4}-\d{2}-\d{2}\//,
    `/holdings/${asOf}/`,
  );
}

export function publicOrigin(req) {
  const host =
    req?.headers?.["x-forwarded-host"] ||
    req?.headers?.host ||
    "fund-holdings-browser.vercel.app";
  const proto = req?.headers?.["x-forwarded-proto"] || "https";
  return `${proto}://${host}`;
}

export function amfiHref(req, code, asOf) {
  return `${publicOrigin(req)}/api/amfi/${encodeURIComponent(code)}?as_of=${encodeURIComponent(asOf)}`;
}

function link(asOf, href, available) {
  return {
    as_of: asOf,
    href,
    message: available ? null : NO_DATA_FOUND,
  };
}

export function filingLinks({ req, code, asOf, previousAsOf, nextAsOf, previousAvailable, nextAvailable }) {
  return {
    self: link(asOf, amfiHref(req, code, asOf), true),
    previous: link(previousAsOf, amfiHref(req, code, previousAsOf), previousAvailable),
    next: link(nextAsOf, amfiHref(req, code, nextAsOf), nextAvailable),
  };
}

export function noDataPayload({ scheme, asOf, links }) {
  return {
    error: NO_DATA_FOUND,
    amfi_code: scheme?.amfi_code || null,
    as_of: asOf || null,
    scheme: scheme
      ? {
          amfi_code: scheme.amfi_code || null,
          name: scheme.name || null,
          amc_name: scheme.amc_name || null,
          parent_name: scheme.parent_name || null,
        }
      : null,
    links: links || null,
  };
}
