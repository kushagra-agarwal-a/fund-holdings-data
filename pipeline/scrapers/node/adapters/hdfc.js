import { httpFetch } from "../lib/http.js";
import { parsePeriod } from "../lib/period.js";

/**
 * HDFC — CMS API (bypasses www WAF).
 * POST https://cms.hdfcfund.com/en/hdfc/api/v2/disclosures/monthfortportfolio
 * multipart: year, type=monthly, month=1..12
 */
export const hdfcAdapter = {
  id: "hdfc_cms",
  async listFiles(ctx) {
    if (ctx.type !== "monthly" && ctx.type !== "fortnightly") {
      return { files: [], notes: `unsupported type ${ctx.type}` };
    }
    const p = parsePeriod(ctx.period);
    const apiType = ctx.type === "fortnightly" ? "fortnightly" : "monthly";
    const boundary = `----WebKitFormBoundary${Date.now().toString(36)}`;
    const body = [
      `--${boundary}`,
      'Content-Disposition: form-data; name="year"',
      "",
      String(p.year),
      `--${boundary}`,
      'Content-Disposition: form-data; name="type"',
      "",
      apiType,
      `--${boundary}`,
      'Content-Disposition: form-data; name="month"',
      "",
      String(p.month),
      `--${boundary}--`,
      "",
    ].join("\r\n");

    const res = await httpFetch(
      "https://cms.hdfcfund.com/en/hdfc/api/v2/disclosures/monthfortportfolio",
      {
        method: "POST",
        headers: {
          accept: "application/json, text/plain, */*",
          "content-type": `multipart/form-data; boundary=${boundary}`,
          origin: "https://www.hdfcfund.com",
          referer: "https://www.hdfcfund.com/",
        },
        body,
      },
    );
    const text = await res.text();
    if (!res.ok) return { files: [], notes: `http_${res.status}` };
    let payload;
    try {
      payload = JSON.parse(text);
    } catch {
      return { files: [], notes: "non-json response" };
    }
    const rows = payload?.data?.files || [];
    const files = [];
    for (const row of rows) {
      const url = row?.file?.url;
      if (!url) continue;
      files.push({
        url,
        filename:
          decodeURIComponent(new URL(url).pathname.split("/").pop() || "") ||
          String(row.title || "hdfc.xlsx"),
      });
    }
    return {
      files,
      notes: `year=${p.year} month=${p.month}`,
    };
  },
};
