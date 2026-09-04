/** Match disclosure filenames/URLs to a calendar as-of day (mid-month vs month-end). */

const AS_OF_RE = /^(\d{4})-(\d{2})-(\d{2})$/;
const MONTH_NAMES = [
  "january",
  "february",
  "march",
  "april",
  "may",
  "june",
  "july",
  "august",
  "september",
  "october",
  "november",
  "december",
];
const MONTH_SHORT = MONTH_NAMES.map((n) => n.slice(0, 3));

export function parseStorageKeyDay(storageKey) {
  const m = AS_OF_RE.exec(String(storageKey || ""));
  if (!m) return null;
  return Number(m[3]);
}

function monthMeta(storageKey) {
  const m = AS_OF_RE.exec(String(storageKey || ""));
  if (!m) return null;
  const year = Number(m[1]);
  const month = Number(m[2]);
  const day = Number(m[3]);
  const lastDay = new Date(Date.UTC(year, month, 0)).getUTCDate();
  const mm = String(month).padStart(2, "0");
  return {
    year,
    month,
    day,
    lastDay,
    mm,
    monthShort: MONTH_SHORT[month - 1],
    monthLong: MONTH_NAMES[month - 1],
  };
}

/** Month-end date hints for a specific calendar month (handles 28–31). */
function monthEndHints(meta) {
  if (!meta) return [];
  const { lastDay, mm, monthShort, monthLong, year } = meta;
  const d = String(lastDay);
  const patterns = [
    new RegExp(`\\b${d}(?:st|th|nd|rd)?\\s*${monthLong}\\b`, "i"),
    new RegExp(`\\b${d}(?:st|th|nd|rd)?\\s*${monthShort}\\b`, "i"),
    new RegExp(`\\b${monthLong}\\s+${d}(?:st|th|nd|rd)?\\b`, "i"),
    new RegExp(`\\b${monthShort}\\s+${d}(?:st|th|nd|rd)?\\b`, "i"),
    new RegExp(`${d}[-_./]${mm}[-_./]${year}`, "i"),
    new RegExp(`${d}(?:st|th|nd|rd)?-${monthShort}(?:-${year})?`, "i"),
    new RegExp(`${d}(?:st|th|nd|rd)?-${monthLong}(?:-${year})?`, "i"),
    new RegExp(`${String(lastDay).padStart(2, "0")}${mm}${year}`, "i"),
    new RegExp(`as on ${d}\\b`, "i"),
    new RegExp(`\\b${d}${mm}${year}\\b`, "i"),
  ];
  if (lastDay === 31) {
    patterns.push(
      /\b31(?:st)?\s*(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)/i,
      /(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+31(?:st)?(?:\b|[,\s_])/i,
      /[-_.]31[-_.]0?\d[-_.]20\d{2}/i,
    );
  }
  return patterns;
}

/** Mid-month (15th) hints for the same calendar month. */
function midMonthHints(meta) {
  if (!meta) return [];
  const { mm, monthShort, monthLong, year } = meta;
  return [
    new RegExp(`\\b15(?:st|th|nd|rd)?\\s*${monthLong}\\b`, "i"),
    new RegExp(`\\b15(?:st|th|nd|rd)?\\s*${monthShort}\\b`, "i"),
    new RegExp(`\\b${monthLong}\\s+15(?:st|th|nd|rd)?\\b`, "i"),
    new RegExp(`\\b${monthShort}\\s+15(?:st|th|nd|rd)?\\b`, "i"),
    new RegExp(`15(?:st|th|nd|rd)?[-_./]${mm}[-_./]${year}`, "i"),
    new RegExp(`15(?:st|th|nd|rd)?[-_./]${mm}[-_./]${String(year).slice(-2)}`, "i"),
    new RegExp(`\\b${monthShort}\\s+15(?:st|th|nd|rd)?,?\\s+${year}\\b`, "i"),
    new RegExp(`\\b${monthLong}\\s+15(?:st|th|nd|rd)?,?\\s+${year}\\b`, "i"),
    new RegExp(`1-15\\s+${monthShort}\\b`, "i"),
    /\bmid[-\s]?month\b/i,
    /\bmidmonth\b/i,
  ];
}

/**
 * When fetching into data/disclosures/{cadence}/YYYY-MM-DD/, keep only files for that slice.
 * @param {{ filename?: string, url?: string }} file
 * @param {string | undefined} storageKey YYYY-MM-DD
 * @param {'monthly'|'fortnightly'} cadence
 */
export function fileMatchesStorageKey(file, storageKey, cadence = "fortnightly") {
  if (!storageKey || !AS_OF_RE.test(storageKey)) return true;
  const meta = monthMeta(storageKey);
  if (!meta) return true;

  const blob = `${file.filename || ""} ${file.url || ""}`.toLowerCase();
  const isMid = meta.day <= 15;
  const endHints = monthEndHints(meta);
  const midHints = midMonthHints(meta);

  if (/monthly portfolio/.test(blob) && cadence === "fortnightly") return false;

  if (cadence === "fortnightly") {
    if (isMid) {
      if (endHints.some((re) => re.test(blob))) return false;
      return true;
    }
    if (midHints.some((re) => re.test(blob)) && !endHints.some((re) => re.test(blob))) {
      return false;
    }
  }

  if (cadence === "monthly" && !isMid) {
    if (midHints.some((re) => re.test(blob)) && !endHints.some((re) => re.test(blob))) {
      return false;
    }
  }

  return true;
}

/**
 * Positive date match when an AMC page lists many historical files (Union, etc.).
 */
export function fileMatchesAsOfStrict(file, asOf) {
  if (!asOf || !AS_OF_RE.test(asOf)) return true;
  const meta = monthMeta(asOf);
  if (!meta) return true;
  const blob = `${file.filename || ""} ${file.url || ""}`.toLowerCase();
  if (/monthly portfolio/.test(blob)) return false;
  const isMid = meta.day <= 15;
  const endHints = monthEndHints(meta);
  const midHints = midMonthHints(meta);
  const d = meta.day;
  const mm = meta.mm;
  const year = meta.year;
  const numeric = [
    new RegExp(`(?<!\\d)${String(d).padStart(2, "0")}[-_./]${mm}[-_./]${year}(?!\\d)`, "i"),
    new RegExp(`(?<!\\d)${d}[-_./]${mm}[-_./]${year}(?!\\d)`, "i"),
  ];
  const hints = (isMid ? midHints : endHints).concat(numeric);
  return hints.some((re) => re.test(blob));
}

export function filterFilesForStorageKey(files, storageKey, cadence) {
  if (!storageKey || !AS_OF_RE.test(storageKey)) return files;
  if (cadence === "fortnightly") {
    return files.filter((f) => fileMatchesAsOfStrict(f, storageKey));
  }
  return files.filter((f) => fileMatchesStorageKey(f, storageKey, cadence));
}

/** True when as-of is the last calendar day of its month. */
export function isMonthEndAsOf(asOf) {
  const meta = monthMeta(asOf);
  if (!meta) return false;
  return meta.day === meta.lastDay;
}
