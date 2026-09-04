import { fetchText } from "../lib/http.js";
import { parsePeriod } from "../lib/period.js";

const BASE = "https://downloads.njmutualfund.com";
const PAGES = {
  monthly: `${BASE}/njmf_download.php?nme=127`,
  fortnightly: `${BASE}/njmf_download.php?nme=415`,
};

const MONTH_TOKEN = {
  "01": ["january", "jan"],
  "02": ["february", "feb"],
  "03": ["march", "mar"],
  "04": ["april", "apr"],
  "05": ["may"],
  "06": ["june", "jun"],
  "07": ["july", "jul"],
  "08": ["august", "aug"],
  "09": ["september", "sept", "sep"],
  "10": ["october", "oct"],
  "11": ["november", "nov"],
  "12": ["december", "dec"],
};

const LINK_RE =
  /<a\b[^>]*href\s*=\s*["']([^"']+)["'][^>]*>([\s\S]*?)<\/a>/gi;

/**
 * NJ Mutual Fund — downloads.njmutualfund.com (AMC-direct).
 * Links are viewfile.php?file=<asset.xlsx>; filename lives in the query string.
 * Upload timestamps in the basename (…-20260701104221.xlsx) must not drive period
 * matching (that false-positives June as July).
 */
export const njAdapter = {
  id: "nj_downloads",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const pageUrl = PAGES[ctx.type];
    const p = parsePeriod(ctx.period);
    const { res, text } = await fetchText(pageUrl);
    if (!res.ok) return { files: [], notes: `http_${res.status}` };

    const files = [];
    const seen = new Set();
    let m;
    LINK_RE.lastIndex = 0;
    while ((m = LINK_RE.exec(text))) {
      const href = m[1].trim();
      if (!/viewfile\.php\?file=/i.test(href)) continue;
      const url = href.startsWith("http")
        ? href
        : new URL(href, BASE + "/").href;
      const fileName = fileParam(url);
      if (!fileName || !/\.(xlsx?|xlsb)(\?|$)/i.test(fileName)) continue;
      const label = m[2].replace(/<[^>]+>/g, " ").replace(/\s+/g, " ").trim();
      const asof = `${stripUploadStamp(fileName)} ${label}`;
      if (!matchesPeriod(asof, p, ctx.type)) continue;
      if (seen.has(url)) continue;
      seen.add(url);
      files.push({ url, filename: sanitize(fileName) });
    }

    files.sort((a, b) => a.filename.localeCompare(b.filename));
    return {
      files,
      notes: files.length
        ? `nj viewfile ${ctx.type} (${files.length})`
        : `no ${ctx.type} files for ${ctx.period}`,
    };
  },
};

function fileParam(url) {
  try {
    const u = new URL(url);
    const f = u.searchParams.get("file");
    return f ? decodeURIComponent(f) : "";
  } catch {
    const m = /[?&]file=([^&]+)/i.exec(url);
    return m ? decodeURIComponent(m[1]) : "";
  }
}

/** Drop trailing -YYYYMMDDHHMMSS publish stamp before matching month. */
function stripUploadStamp(name) {
  return String(name || "").replace(/-\d{14}(?=\.(xlsx?|xlsb)$)/i, "");
}

function matchesPeriod(text, p, type) {
  const t = String(text || "");
  const lower = t.toLowerCase();
  const yearOk =
    lower.includes(String(p.year)) ||
    new RegExp(`(?:^|[^0-9])${String(p.year).slice(2)}(?:[^0-9]|$)`).test(
      lower,
    );
  if (!yearOk) return false;

  const tokens = MONTH_TOKEN[p.mm] || [];
  const monthOk = tokens.some((tok) => {
    // Require month token adjacent to year or day (avoid random hits).
    return new RegExp(
      `(?:^|[^a-z])${tok}[^a-z]{0,12}(?:\\d{1,2}(?:st|nd|rd|th)?[^a-z]{0,6})?${p.year}` +
        `|(?:^|[^a-z])${tok}[-_ ]+${String(p.year).slice(2)}(?:[^0-9]|$)` +
        `|(?:15|${String(p.monthEndDay).padStart(2, "0")}|30|28|29)[._-]?${tok}` +
        `|${tok}[-_ ]?(?:15|${String(p.monthEndDay).padStart(2, "0")}|30|28|29)`,
      "i",
    ).test(t);
  });
  if (!monthOk) return false;

  if (type === "fortnightly") {
    return /fortnight/i.test(t);
  }
  // monthly: require monthly marker; reject pure fortnightly rows
  if (/fortnight/i.test(t) && !/monthly/i.test(t)) return false;
  return /monthly|portfolio/i.test(t);
}

function sanitize(name) {
  return String(name || "")
    .replace(/[^\w.\-()+ ]+/g, "_")
    .trim()
    .slice(0, 200);
}
