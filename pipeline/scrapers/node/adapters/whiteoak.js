import { httpFetch } from "../lib/http.js";
import { parsePeriod, periodMatchers } from "../lib/period.js";

const CMS = "https://cms.whiteoakamc.com";
const PAGE = "https://mf.whiteoakamc.com/regulatory-disclosures/scheme-portfolios";
const API = `${CMS}/api/scheme-portfolios`;

/**
 * WhiteOak Capital — Strapi CMS (AMC-direct).
 * GET cms.whiteoakamc.com/api/scheme-portfolios?filters[period]=Monthly|Fortnightly
 * Files on content.whiteoakamc.com via doc_file.url. Per-scheme only (no consolidated).
 *
 * Important: `published_date` is host date, not as-of date. June monthlies are often
 * published in early July — match month/year tokens in doc_name / filename.
 */
export const whiteoakAdapter = {
  id: "whiteoak_cms",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const p = parsePeriod(ctx.period);
    const matchers = periodMatchers(p);
    const periodLabel = ctx.type === "fortnightly" ? "Fortnightly" : "Monthly";

    const rows = await fetchPeriodRows(periodLabel, p);
    const files = [];
    const seen = new Set();

    for (const row of rows) {
      const attrs = row?.attributes || {};
      const docName = String(attrs.doc_name || "");
      const scheme = String(attrs.scheme_name || "");
      const fileAttrs = attrs.doc_file?.data?.attributes || {};
      const url = String(fileAttrs.url || attrs.doc_link || "").trim();
      if (!url || !/\.(xlsx?|xlsb)(\?|$)/i.test(url)) continue;

      const blob = `${docName}\n${scheme}\n${fileAttrs.name || ""}\n${url}`;
      if (!matchers.periodRe.test(blob)) continue;
      if (matchers.oldYearRe.test(blob) && !matchers.periodRe.test(docName + fileAttrs.name)) {
        continue;
      }
      // Extra guard: require target month name/abbr or ddmmYYYY-style in doc/file name
      // (avoids June files whose published_date falls in July).
      if (!textMatchesTargetMonth(docName, fileAttrs.name || "", p)) continue;

      if (seen.has(url)) continue;
      seen.add(url);

      const filename =
        filenameFromUrl(url) ||
        fileAttrs.name ||
        sanitize(`${scheme}-${docName}.xlsx`);
      files.push({ url, filename: sanitize(filename) });
    }

    files.sort((a, b) => a.filename.localeCompare(b.filename));
    return {
      files,
      notes: files.length
        ? `cms scheme-portfolios ${periodLabel.toLowerCase()} (${files.length})`
        : `no ${periodLabel.toLowerCase()} files for ${ctx.period}`,
    };
  },
};

async function fetchPeriodRows(periodLabel, p) {
  // Prefer server-side name filter (as-of month lives in doc_name, not published_date).
  const needles = [
    `${p.monthName} ${p.year}`,
    `${p.monthAbbr} ${p.year}`,
    `${p.monthName}${p.year}`,
  ];
  const seenIds = new Set();
  const out = [];
  for (const needle of needles) {
    const batch = await fetchPaged((url) => {
      url.searchParams.set("filters[period][$eq]", periodLabel);
      url.searchParams.set("filters[doc_name][$containsi]", needle);
    });
    for (const row of batch) {
      if (seenIds.has(row.id)) continue;
      seenIds.add(row.id);
      out.push(row);
    }
  }
  if (out.length) return out;

  // Fallback: page recent published rows and filter locally.
  return fetchPaged((url) => {
    url.searchParams.set("filters[period][$eq]", periodLabel);
    // Host dates can trail the as-of month by a few weeks.
    url.searchParams.set(
      "filters[published_date][$gte]",
      `${p.year}-${p.mm}-01`
    );
    const end = new Date(p.year, p.month + 1, 20); // ~20 days into next month
    const yyyy = end.getFullYear();
    const mm = String(end.getMonth() + 1).padStart(2, "0");
    const dd = String(end.getDate()).padStart(2, "0");
    url.searchParams.set("filters[published_date][$lte]", `${yyyy}-${mm}-${dd}`);
  });
}

async function fetchPaged(applyFilters) {
  const out = [];
  let page = 1;
  let pageCount = 1;
  while (page <= pageCount && page <= 40) {
    const url = new URL(API);
    applyFilters(url);
    url.searchParams.set("pagination[page]", String(page));
    url.searchParams.set("pagination[pageSize]", "100");
    url.searchParams.set("sort", "published_date:desc");
    url.searchParams.set("populate", "*");

    const res = await httpFetch(url.href, {
      headers: {
        accept: "application/json",
        origin: "https://mf.whiteoakamc.com",
        referer: PAGE,
      },
    });
    const text = await res.text();
    if (!res.ok) throw new Error(`whiteoak cms http_${res.status}`);
    let json;
    try {
      json = JSON.parse(text);
    } catch {
      throw new Error("whiteoak cms non-json");
    }
    const batch = Array.isArray(json?.data) ? json.data : [];
    out.push(...batch);
    pageCount = Number(json?.meta?.pagination?.pageCount || 1);
    if (!batch.length) break;
    page++;
  }
  return out;
}

function textMatchesTargetMonth(docName, fileName, p) {
  const text = `${docName} ${fileName}`;
  const monthRe = new RegExp(
    `\\b(?:${p.monthName}|${p.monthAbbr}|${p.monthName.slice(0, 3)})\\b`,
    "i",
  );
  const yearRe = new RegExp(`\\b${p.year}\\b|\\b${String(p.year).slice(2)}\\b`);
  if (monthRe.test(text) && yearRe.test(text)) return true;
  // compact: 15thJuly2026 / 31-07-2026 / July2026 / 15072026
  const compact = new RegExp(
    [
      `${p.monthName}\\s*${p.year}`,
      `${p.monthAbbr}\\s*${p.year}`,
      `(?:15|${String(p.monthEndDay).padStart(2, "0")})[-_/]?${p.mm}[-_/]?${p.year}`,
      `${p.mm}${p.year}`,
      `${p.year}${p.mm}`,
    ].join("|"),
    "i",
  );
  return compact.test(text.replace(/\s+/g, ""));
}

function filenameFromUrl(url) {
  try {
    return decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
  } catch {
    return "";
  }
}

function sanitize(name) {
  return String(name || "")
    .replace(/[^\w.\-()+ ]+/g, "_")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 180);
}
