import { httpFetch } from "../lib/http.js";
import { parsePeriod } from "../lib/period.js";

const MONTH_NAMES = [
  "January",
  "February",
  "March",
  "April",
  "May",
  "June",
  "July",
  "August",
  "September",
  "October",
  "November",
  "December",
];

/**
 * UTI fortnightly API expects e.g. "1-15 July", not bare "july".
 * @param {string | undefined} storageKey YYYY-MM-DD folder key
 */
function utiFortnightlyMonthParam(storageKey) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(storageKey || ""));
  if (!m) return null;
  const month = Number(m[2]);
  const day = Number(m[3]);
  const monthName = MONTH_NAMES[month - 1];
  if (!monthName) return null;
  return day <= 15 ? `1-15 ${monthName}` : `16-31 ${monthName}`;
}

async function fetchUtiApi(endpoint, month, year, referer) {
  const url =
    `https://www.utimf.com/api/${endpoint}` +
    `?year=${year}&month=${encodeURIComponent(month)}`;
  const res = await httpFetch(url, {
    headers: { accept: "application/json, text/plain, */*", referer },
  });
  if (!res.ok) return [];
  let payload;
  try {
    payload = JSON.parse(await res.text());
  } catch {
    return [];
  }
  return payload?.rows || [];
}

/**
 * UTI — consolidated portfolio disclosures via JSON API.
 *
 * Monthly (all schemes):
 *   GET /api/get-consolidate-portfolio-disclosure?year=2026&month=July
 *
 * Fortnightly (mid-month + month-end packs):
 *   GET /api/get-consolidate-debt-portfolio-disclosure?year=2026&month=1-15%20July
 *   GET /api/get-consolidate-debt-portfolio-disclosure?year=2026&month=16-31%20July
 */
export const utiAdapter = {
  id: "uti_api",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const p = parsePeriod(ctx.period);
    const month = p.monthName;

    let rows = [];
    if (ctx.type === "fortnightly") {
      const fnMonth = utiFortnightlyMonthParam(ctx.storageKey);
      if (!fnMonth) {
        return {
          files: [],
          notes: "fortnightly requires storageKey YYYY-MM-DD (e.g. 2026-07-15)",
        };
      }
      rows = await fetchUtiApi(
        "get-consolidate-debt-portfolio-disclosure",
        fnMonth,
        p.year,
        "https://www.utimf.com/downloads/consolidate-debt-portfolio-disclosure",
      );
    } else {
      rows = await fetchUtiApi(
        "get-consolidate-portfolio-disclosure",
        month,
        p.year,
        "https://www.utimf.com/downloads/consolidate-all-portfolio-disclosure",
      );
    }

    const files = [];
    for (const row of rows) {
      const raw = row.url || row.doc;
      if (!raw) continue;
      files.push({
        url: raw,
        filename:
          decodeURIComponent(new URL(raw).pathname.split("/").pop() || "") ||
          String(row.name || "uti.zip"),
      });
    }
    return {
      files,
      notes: ctx.type === "fortnightly" ? `fortnightly=${utiFortnightlyMonthParam(ctx.storageKey)}` : `month=${month}`,
    };
  },
};
