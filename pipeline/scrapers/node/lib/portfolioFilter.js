/**
 * Decide if a URL/text looks like a monthly portfolio disclosure for a period.
 */

const FILE_EXT = /\.(xlsx|xls|csv|zip|xlsm)(?:\?|#|$)/i;

const EXCLUDE =
  /aaum|aauum|\baum\b|complaint|proxy|voting|tracking[\s_-]?error|risk[\s_-]?param|portfolio[\s_-]?overlap|overlap|transaction[\s_-]?report|investor[\s_-]?complaint|\bir_|\bsebi\b|product[\s_-]?dashboard|scheme[\s_-]?dashboard|dashboard|constituent|fund[\s_-]?performance|quarterly[\s_-]?aum|disclosure[\s_-]?of[\s_-]?aum|top\s*\d+\s*holdings(?:\s+by\s+issuer)?|holdings\s+by\s+issuer/i;

const PORTFOLIO =
  /portfolio|holdings|month[\s_-]?end[\s_-]?portfolio|monthend[\s_-]?portfolio|(?:^|[\s\/])(?:MP|FN)[\s_-]/i;

/**
 * @param {string} url
 * @param {string} [text]
 * @param {{ periodRe: RegExp, oldYearRe: RegExp }} matchers
 * @param {'monthly'|'fortnightly'} [type]
 */
export function isPeriodPortfolioFile(url, text, matchers, type = "monthly") {
  const blob = `${decodeURIComponent(url)} ${text || ""}`;
  if (!FILE_EXT.test(url) && !FILE_EXT.test(text || "")) return false;
  if (EXCLUDE.test(blob)) return false;
  // Reject weekly-only basenames; do not exclude parent folders named
  // "...Monthly--Fortnightly--Weekly-Portfolio-of-Scheme(s)/..." (e.g. Shriram).
  const base =
    decodeURIComponent(String(url).split(/[?#]/)[0]).split("/").pop() || "";
  if (/weekly/i.test(base) && !/monthly|fortnight/i.test(base)) return false;
  if (matchers.oldYearRe.test(blob) && !matchers.periodRe.test(blob))
    return false;
  if (!matchers.periodRe.test(blob)) return false;

  if (type === "monthly") {
    if (/fortnightly/i.test(blob) && !/monthly/i.test(blob)) return false;
  } else if (type === "fortnightly") {
    // Debt schemes disclose as of the 15th AND month-end (both "fortnightly").
    // Reject pure monthly-labelled packs; accept fortnight/mid-month markers,
    // day-15 filenames, or debt/liquid/overnight portfolios dated for the period.
    if (/\bmonthly\b/i.test(blob) && !/fortnight/i.test(blob) && !/mid[\s_-]?month/i.test(blob)) {
      return false;
    }
    const tagged =
      /fortnightly|mid[\s_-]?month|\bFN[-_]|15[\s_.-]?(?:st|th)?(?:[\s_.-]|$)|debt[\s_-]?(?:portfolio|fund)|liquid[\s_-]?(?:portfolio|fund)|overnight|money[\s_-]?market[\s_-]?(?:portfolio|fund)|consolidate[ds]?[\s_-]?debt/i.test(
        blob,
      );
    if (!tagged) return false;
  }

  if (!PORTFOLIO.test(blob) && !/monthend-portfolios/i.test(blob)) return false;
  return true;
}

export { FILE_EXT };
