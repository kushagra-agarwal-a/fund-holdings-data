import { httpFetch, absUrl } from "../lib/http.js";
import { parsePeriod } from "../lib/period.js";

/**
 * SBI MF Sitefinity XHR portfolio sheets.
 * POST /ajaxcall/CMS/GetSchemePortfolioSheets → HTML table fragment with .xlsx links
 */
export const sbiAdapter = {
  id: "sbi_sitefinity",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const p = parsePeriod(ctx.period);
    const endpoint =
      "https://www.sbimf.com/ajaxcall/CMS/GetSchemePortfolioSheets";
    const body = new URLSearchParams({
      FundId: "0",
      PSYear: String(p.year),
      PSMonth: p.monthName,
      PSFrequency: ctx.type === "fortnightly" ? "Fortnightly" : "Monthly",
    });

    const res = await httpFetch(endpoint, {
      method: "POST",
      headers: {
        "content-type": "application/x-www-form-urlencoded; charset=UTF-8",
        "x-requested-with": "XMLHttpRequest",
        accept: "text/html, */*; q=0.01",
        referer: "https://www.sbimf.com/portfolios",
        origin: "https://www.sbimf.com",
      },
      body,
    });
    const text = await res.text();
    if (!res.ok) return { files: [], notes: `http_${res.status}` };

    const files = [];
    const seen = new Set();
    const re = /href\s*=\s*["']([^"']+\.xlsx?(?:\?[^"']*)?)["']/gi;
    let m;
    while ((m = re.exec(text))) {
      // Sitefinity HTML-encodes apostrophes (Children&#39;s) which breaks downloads.
      const href = m[1]
        .replace(/&amp;/g, "&")
        .replace(/&#39;/g, "'")
        .replace(/&apos;/g, "'")
        .replace(/&quot;/g, '"')
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">");
      const url = absUrl(href, "https://www.sbimf.com");
      if (!url || seen.has(url)) continue;
      seen.add(url);
      files.push({
        url,
        filename: decodeURIComponent(url.split("/").pop().split("?")[0]),
      });
    }

    return {
      files,
      notes: files.length
        ? `PSYear=${p.year} PSMonth=${p.monthName}`
        : "empty sheet list",
    };
  },
};
