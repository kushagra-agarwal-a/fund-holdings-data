import { httpFetch } from "../lib/http.js";
import { parsePeriod } from "../lib/period.js";

const BASE = "https://quantmutual.com";
const PAGE_URL = `${BASE}/statutory-disclosures`;
const METHOD_URL = `${BASE}/statutorydisclosures.aspx/displaydisclouser`;

/** Categories match onclick handlers on the statutory disclosures page. */
const CAT = {
  monthly: "MONTHLY PORTFOLIO",
  fortnightly: "FORTNIGHTLY PORTFOLIO",
};

/**
 * Filenames look like:
 *   Monthly_Portfolio_July26.xlsx
 *   Portfolio_Debt_31072026.xlsx / Debt_Portfolio_15072026.xlsx
 */
const MONTH_TOKEN = {
  "01": "jan",
  "02": "feb",
  "03": "mar",
  "04": "apr",
  "05": "may",
  "06": "jun",
  "07": "jul",
  "08": "aug",
  "09": "sep",
  "10": "oct",
  "11": "nov",
  "12": "dec",
};

/**
 * quant Mutual Fund — ASP.NET PageMethods on statutory disclosures (AMC-direct).
 * POST /statutorydisclosures.aspx/displaydisclouser with {id: year, cat: category}
 * (jQuery-style JSON body). Returns HTML snippets with /Admin/disclouser/*.xlsx links.
 */
export const quantAdapter = {
  id: "quant_aspx",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const p = parsePeriod(ctx.period);
    const cat = CAT[ctx.type];
    const html = await fetchCategoryHtml(String(p.year), cat);
    if (html == null) return { files: [], notes: "pageMethods failed" };
    if (!html) return { files: [], notes: "empty category html" };

    const hrefs = [...html.matchAll(/href=['"]([^'"]+)['"]/gi)].map((m) =>
      m[1].trim()
    );
    const files = [];
    const seen = new Set();

    for (const href of hrefs) {
      if (!/\.(xlsx?|xlsb)(\?|$)/i.test(href)) continue;
      const url = absUrl(href);
      if (!url || seen.has(url)) continue;
      const base = filenameFromUrl(url);
      if (!matchesPeriod(base, p, ctx.type)) continue;
      seen.add(url);
      files.push({ url, filename: base });
    }

    files.sort((a, b) => a.filename.localeCompare(b.filename));
    return {
      files,
      notes: files.length
        ? `aspx ${ctx.type} (${files.length})`
        : `no ${ctx.type} match for ${ctx.period}`,
    };
  },
};

async function fetchCategoryHtml(year, cat) {
  // Site sends non-strict JSON: {id:'2026',cat:'FORTNIGHTLY PORTFOLIO'}
  const body = `{id:'${year}',cat:'${String(cat).replace(/'/g, "\\'")}'}`;
  const res = await httpFetch(METHOD_URL, {
    method: "POST",
    headers: {
      "content-type": "application/json; charset=utf-8",
      accept: "application/json, text/javascript, */*; q=0.01",
      "x-requested-with": "XMLHttpRequest",
      origin: BASE,
      referer: PAGE_URL,
    },
    body,
  });
  const text = await res.text();
  if (!res.ok) return null;
  let json;
  try {
    json = JSON.parse(text);
  } catch {
    return null;
  }
  return typeof json?.d === "string" ? json.d : "";
}

function absUrl(href) {
  try {
    return new URL(href, BASE).href;
  } catch {
    return null;
  }
}

function filenameFromUrl(url) {
  try {
    return decodeURIComponent(new URL(url).pathname.split("/").pop() || "");
  } catch {
    return "";
  }
}

function matchesPeriod(filename, p, type) {
  const name = String(filename || "");
  const yy = String(p.year % 100).padStart(2, "0");
  const yyyy = String(p.year);
  const mm = p.mm;
  const mon = MONTH_TOKEN[mm];

  if (type === "monthly") {
    // Monthly_Portfolio_July26.xlsx / monthly_portfolio_june_30062026.xlsx /
    // Monthly_Portfolio_30042026.xlsx / Monthly_Portfolio_March2026.xlsx
    const monthNameRe = new RegExp(
      `(?:^|[^a-z])${p.monthName}|${mon}`,
      "i"
    );
    if (
      new RegExp(`${mm}${yyyy}`, "i").test(name) ||
      new RegExp(`${yyyy}${mm}`, "i").test(name)
    ) {
      return /month/i.test(name) || /portfolio/i.test(name);
    }
    if (monthNameRe.test(name) && (name.includes(yy) || name.includes(yyyy))) {
      return true;
    }
    // July26 / Jul26 / July2026
    if (
      new RegExp(`${p.monthName}\\s*${yy}\\b`, "i").test(name) ||
      new RegExp(`${mon}\\s*${yy}\\b`, "i").test(name) ||
      new RegExp(`${p.monthName}\\s*${yyyy}\\b`, "i").test(name)
    ) {
      return true;
    }
    return false;
  }

  // Fortnightly debt: Portfolio_Debt_DDMMYYYY.xlsx / Debt_Portfolio_DDMMYYYY.xlsx
  const m = name.match(/(\d{2})(\d{2})(\d{4})/);
  if (m) {
    const [, , fileMm, fileYyyy] = m;
    return fileMm === mm && fileYyyy === yyyy;
  }
  // Fallback: 15 / 31 + month token + yy
  if (
    /(?:^|[^a-z])(?:15|31)[^a-z]{0,6}/i.test(name) &&
    monthNameReSafe(name, p, mon) &&
    (name.includes(yy) || name.includes(yyyy))
  ) {
    return true;
  }
  return false;
}

function monthNameReSafe(name, p, mon) {
  return new RegExp(`(?:^|[^a-z])${p.monthName}|${mon}`, "i").test(name);
}
