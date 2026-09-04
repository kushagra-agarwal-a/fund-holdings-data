import { randomUUID } from "node:crypto";
import { httpFetch } from "../lib/http.js";
import { parsePeriod } from "../lib/period.js";

const API_URL = "https://apps.digital.icicipruamc.com/nms/v1/downloads/files";
const CATEGORY_MONTHLY = "26a073d7-08d2-4a95-95fa-f83a4ee51e40";
const CATEGORY_FORTNIGHTLY = "d608ec2a-4f18-4346-ae5f-059727f1b1c6";

const TITLE_YM_RE =
  /(?:monthly\s+portfolio\s+disclosure|fortnightly(?:\s+debt\s+scheme)?\s+portfolio(?:\s+disclosure)?)(?:\s+[-–]?\s*|\s+)(?:(?:\d{1,2}(?:st|nd|rd|th)?\s+)?)?(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(20\d{2})/i;
const TITLE_DAY_MON_YEAR_RE =
  /(\d{1,2})(?:st|nd|rd|th)?\s+(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t|tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\s+(20\d{2})/i;

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

function parseYm(row) {
  const title = String(row?.title?.text || row?.title?.code || "");
  const m = TITLE_YM_RE.exec(title);
  if (m) {
    const mon = MONTHS[m[1].toLowerCase()];
    if (mon) return { year: Number(m[2]), month: mon };
  }
  const m2 = TITLE_DAY_MON_YEAR_RE.exec(title);
  if (m2) {
    const mon = MONTHS[m2[2].toLowerCase()];
    if (mon) return { year: Number(m2[3]), month: mon };
  }
  for (const k of ["applicableMonth", "fileDate"]) {
    const val = row?.[k];
    if (typeof val === "number" && val > 0) {
      const d = new Date(val);
      return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1 };
    }
  }
  return null;
}

/**
 * ICICI — POST apps.digital.icicipruamc.com/nms/v1/downloads/files
 */
export const iciciAdapter = {
  id: "icici_nms",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const p = parsePeriod(ctx.period);
    const categoryId =
      ctx.type === "fortnightly" ? CATEGORY_FORTNIGHTLY : CATEGORY_MONTHLY;
    const all = [];
    for (let page = 1; page <= 100; page++) {
      const res = await httpFetch(API_URL, {
        method: "POST",
        headers: {
          accept: "*/*",
          "content-type": "application/json",
          origin: "https://www.icicipruamc.com",
          referer: "https://www.icicipruamc.com/",
          env: "api",
          requestAPIId: randomUUID(),
        },
        body: JSON.stringify({
          categoryId,
          schemeCategory: "",
          userType: "Investor",
          fileType: "All",
          page: String(page),
          size: "50",
          filter: [],
          categoryName: "OTHERS",
        }),
      });
      const text = await res.text();
      if (!res.ok) return { files: [], notes: `http_${res.status}` };
      let obj;
      try {
        obj = JSON.parse(text);
      } catch {
        return { files: [], notes: "non-json response" };
      }
      const data = obj?.success?.data || {};
      const chunk = data.files || [];
      if (!Array.isArray(chunk) || !chunk.length) break;
      all.push(...chunk);
      if (!data.isNext) break;
    }

    const files = [];
    const seen = new Set();
    for (const row of all) {
      const title = String(row?.title?.text || row?.title?.code || row?.title || "");
      if (ctx.type === "fortnightly") {
        if (!/fortnight|mid[\s_-]?month|debt/i.test(title + " " + JSON.stringify(row)))
          continue;
        if (/\bmonthly\b/i.test(title) && !/fortnight/i.test(title)) continue;
      } else if (ctx.type === "monthly") {
        if (/fortnight/i.test(title) && !/monthly/i.test(title)) continue;
      }
      const ym = parseYm(row);
      if (!ym || ym.year !== p.year || ym.month !== p.month) continue;
      let raw = String(row.url || "").trim();
      if (!raw) continue;
      if (!raw.startsWith("http")) {
        raw = new URL(raw.replace(/^\//, ""), "https://www.icicipruamc.com/").href;
      }
      raw = raw.replace(
        "://www.icicipruamc.com/downloads/",
        "://www.icicipruamc.com/blob/downloads/",
      );
      if (seen.has(raw)) continue;
      seen.add(raw);
      files.push({
        url: raw,
        filename:
          decodeURIComponent(new URL(raw).pathname.split("/").pop() || "") ||
          "icici.zip",
      });
    }
    return { files, notes: `indexed ${all.length} rows cat=${categoryId.slice(0, 8)}` };
  },
};
