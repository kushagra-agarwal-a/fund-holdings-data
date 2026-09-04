/**
 * Period helpers for repeatable monthly/fortnightly fetches (YYYY-MM).
 */

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

const MONTH_ABBR = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

/**
 * @param {string} period YYYY-MM
 */
export function parsePeriod(period) {
  const m = /^(\d{4})-(\d{2})$/.exec(period);
  if (!m) throw new Error(`Invalid period "${period}"; expected YYYY-MM`);
  const year = Number(m[1]);
  const month = Number(m[2]);
  if (month < 1 || month > 12) throw new Error(`Invalid month in "${period}"`);
  return {
    period,
    year,
    month,
    monthName: MONTH_NAMES[month - 1],
    monthAbbr: MONTH_ABBR[month - 1],
    mm: String(month).padStart(2, "0"),
    monthEndDay: new Date(year, month, 0).getDate(),
  };
}

/**
 * Build loose filename matchers for a calendar month portfolio.
 * Includes month-end and mid-month (15th) forms used in fortnightly packs.
 * @param {ReturnType<typeof parsePeriod>} p
 */
export function periodMatchers(p) {
  const { year, month, monthName, monthAbbr, mm, monthEndDay } = p;
  const y = String(year);
  const yy = y.slice(2);
  const dd = String(monthEndDay).padStart(2, "0");
  const mid = "15";
  // Keep '-' at end of character classes to avoid invalid ranges.
  const sep = "[\\s_.,-]";
  const ddmmyyyy = `${dd}${sep}?${mm}${sep}?${y}`;
  const midDdmmyyyy = `${mid}${sep}?${mm}${sep}?${y}`;
  const yyyymmdd = `${y}${mm}${dd}`;
  const midYyyymmdd = `${y}${mm}${mid}`;
  const compactEnd = `${dd}${mm}(?:${y}|${yy})`;
  const compactMid = `${mid}${mm}(?:${y}|${yy})`;
  const yyyymm = `${y}${mm}`;
  const monthYear =
    `(?:${monthName}|${monthAbbr}|${monthName.slice(0, 3)})` +
    `${sep}*\\d{0,2}${sep}*${y}`;
  const yearMonth = `${y}${sep}*(?:${monthName}|${monthAbbr})`;
  const dayMonthYear =
    `(?:0?1[35]|${dd}|${mid})(?:st|nd|rd|th)?${sep}*(?:${monthName}|${monthAbbr})${sep}*${y}` +
    `|(?:${monthName}|${monthAbbr})${sep}*(?:0?1[35]|${dd}|${mid})(?:st|nd|rd|th)?${sep}*${y}`;

  const periodRe = new RegExp(
    [
      monthYear,
      yearMonth,
      dayMonthYear,
      ddmmyyyy,
      midDdmmyyyy,
      yyyymmdd,
      midYyyymmdd,
      compactEnd,
      compactMid,
      `(?:^|[^\\d])${mm}${sep}${y}(?:[^\\d]|$)`,
      yyyymm,
    ].join("|"),
    "i",
  );

  const oldYearRe = new RegExp(
    `(?:${monthName}|${monthAbbr})${sep}*${year - 1}|${year - 1}${sep}?(?:${monthName}|${monthAbbr}|0?${month})`,
    "i",
  );

  return { periodRe, oldYearRe, yyyymmdd, ddmmyyyy: `${dd}-${mm}-${y}` };
}

export function financialYearLabel(p) {
  const start = p.month >= 4 ? p.year : p.year - 1;
  return `${start}-${String(start + 1).slice(2)}`;
}
