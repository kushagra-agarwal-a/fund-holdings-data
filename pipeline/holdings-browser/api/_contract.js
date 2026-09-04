/** Public holdings API contract. Every holding emits the same keys. */

export const MARKET_VALUE_UNIT = "INR_LAKH";
export const PCT_NAV_UNIT = "percent";

export const HOLDING_TYPES = [
  "equity",
  "debt",
  "money_market",
  "cash",
  "derivative",
  "commodity",
  "fund_unit",
  "other",
];

export const SCHEME_KEYS = [
  "amfi_code",
  "name",
  "amc_name",
  "parent_name",
  "parent_amfi",
  "nav",
  "nav_date",
  "isin",
  "category",
];

export const META_KEYS = [
  "as_of",
  "disclosure_type",
  "period",
  "disclosure_shortcode",
  "holding_count",
  "source_file",
  "market_value_unit",
  "pct_nav_unit",
];

export const HOLDING_KEYS = [
  "holding_type",
  "instrument",
  "isin",
  "section",
  "industry",
  "rating",
  "coupon",
  "maturity_date",
  "quantity",
  "market_value",
  "pct_nav",
  "ytm",
  "ytc",
  "instrument_yield",
  "listed_status",
  "underlying",
  "position_side",
];

const EMPTY = new Set([
  "",
  "-",
  "--",
  "—",
  "na",
  "n/a",
  "n.a.",
  "nil",
  "null",
  "none",
  ".",
  "nan",
  "^",
  "#",
  "@",
  "$",
  "% to nav",
  "% to n.a.v",
]);

const CASH_RE =
  /treps?|tri[\s-]?party|reverse\s+repos?|\bcblo\b|clearing\s+corporation|\bccil\b|amc\s+repo\s+clearing|net\s+current\s+assets?|\bnca\b|net\s+receivables?|net\s+payables?|receivable\s*\/\s*\(?\s*payable|payables?\s*\/\s*\(?\s*receivable|cash\s+margin|margin\s+money|cash\s*\/\s*bank|cash\s+and\s+other|call,\s*cash|^\s*cash\s*$|^\s*cash\s*\/|\brepos?\b|^\s*trp[_-]|^\s*rep\d+/i;
const NOT_CASH_RE =
  /interest\s+rate\s+swaps?|\birs\b|(?<![A-Za-z])ois(?![A-Za-z])|t[\s-]?bill|treasury\s+bill|commercial\s+paper|certificate\s+of\s+deposit|\bdebenture\b|\bncd\b/i;
const DERIV_RE =
  /\bfutures?\b|\boptions?\b|\bderivatives?\b|covered\s+call|interest\s+rate\s+swaps?|\birs\b|(?<![A-Za-z])ois(?![A-Za-z])|\bswaps?\b/i;
const COMMODITY_RE =
  /^\s*\(?\s*(?:[a-z]\)\s*)?gold(?:\s+\d{3}\s+purity)?\s*\)?\s*$|^\s*\(?\s*(?:[a-z]\)\s*)?silver\s*\)?\s*$|physical\s+gold|physical\s+silver|gold\s+bar|silver\s+bar|gold\s+\d{3}\s+purity/i;
const NOT_COMMODITY_RE = /sovereign\s+gold|gold\s+bond/i;
const FUND_RE =
  /mutual\s+fund|units?\s+of\s+(?:an?\s+)?(?:alternative|aif)|exchange\s+traded\s+fund|\betf\b|fund\s+of\s+funds|overseas\s+etfs?|international\s+selection\s+fund/i;
const MONEY_MARKET_RE =
  /treasury\s+bills?|t[\s-]?bills?|commercial\s+paper|certificate\s+of\s+deposits?/i;
const DEBT_RE =
  /government\s+securit|g[\s-]?sec|\bsdl\b|state\s+development|non[\s-]?convertible|\bbonds?\b|\bdebenture|\bncd\b|\bfrn\b|securitised|corporate\s+debt|debt\s+instrument|zero\s+coupon|floating\s+rate|perpetual|tier\s+[-i1]|\bgoi\b/i;
const EQUITY_RE =
  /equity|listed\s*\/?\s*awaiting|shares?\b|stock\s+exchange|preference|warrant|rights?\s+entitlement|real\s+estate\s+investment|infrastructure\s+investment\s+trust|\breits?\b|\binvits?\b|overseas\s+securit/i;
const RATING_RE =
  /^(?:(?:crisil|icra|care|india\s*ratings?|fitch|brickwork|acute|infomerics)[\s/\-]*)?(sovereign|unrated|not\s*applicable|a1\+?|a2\+?|a3\+?|a4\+?|aaa|aa[+\-]?|a[+\-]?|bbb[+\-]?|bb[+\-]?|b[+\-]?|ccc|d)(?:\s*\/\s*[a-z0-9+\-]+)?$/i;
const COUPON_NAME_RE = /\d+(?:\.\d+)?\s*%/;

export function parseNumber(value) {
  if (value == null) return null;
  if (typeof value === "number") return Number.isFinite(value) ? value : null;
  let s = String(value).trim();
  if (!s || EMPTY.has(s.toLowerCase())) return null;
  s = s.replace(/[%$,₹]/g, "").replace(/\bRs\.?\b/gi, "").replace(/,/g, "").trim();
  if (!s || EMPTY.has(s.toLowerCase())) return null;
  const n = Number(s);
  if (Number.isFinite(n)) return n;
  const m = s.match(/-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/);
  return m ? Number(m[0]) : null;
}

function round6(n) {
  if (n == null || !Number.isFinite(n)) return null;
  return Math.round(n * 1e6) / 1e6;
}

function textOrNull(value) {
  if (value == null) return null;
  const s = String(value).trim();
  if (!s || EMPTY.has(s.toLowerCase())) return null;
  return s;
}

function looksRating(value) {
  const s = textOrNull(value);
  return s ? RATING_RE.test(s) : false;
}

export function classifyHoldingType(h) {
  const name = String(h?.instrument || "");
  const section = String(h?.section || "");
  const industry = `${h?.industry || ""} ${h?.industry_rating || ""}`;
  const isin = String(h?.isin || "").trim().toUpperCase();
  const blob = `${name} ${section} ${industry}`;

  if (DERIV_RE.test(name) || DERIV_RE.test(section) || DERIV_RE.test(industry)) {
    return "derivative";
  }
  if (CASH_RE.test(name) || (CASH_RE.test(section) && !isin && !NOT_CASH_RE.test(name))) {
    if (!NOT_CASH_RE.test(name)) return "cash";
  }
  if (isin.startsWith("INF") || FUND_RE.test(section) || FUND_RE.test(name) || /^LU[A-Z0-9]{10}$/.test(isin)) {
    return "fund_unit";
  }
  if ((COMMODITY_RE.test(name) || /^(?:[a-z]\)\s*)?(gold|silver)\b/i.test(section)) && !NOT_COMMODITY_RE.test(name)) {
    return "commodity";
  }
  if (MONEY_MARKET_RE.test(section) || MONEY_MARKET_RE.test(name)) return "money_market";
  if (
    COUPON_NAME_RE.test(name) ||
    DEBT_RE.test(name) ||
    /^(IN0|IN3)/.test(isin) ||
    (DEBT_RE.test(section) && !EQUITY_RE.test(section))
  ) {
    return "debt";
  }
  if (/physical\s+commodit|commodities\s+exchange/i.test(section) || /commodity/i.test(name) || /gold.*bar|silver.*bar|1\s*kg/i.test(name)) {
    return "commodity";
  }
  if (
    EQUITY_RE.test(section) ||
    EQUITY_RE.test(name) ||
    isin.startsWith("INE") ||
    /^(US|GB|KY|TW|KR|JP|HK|MU|IE|CA|AU|CH|DE|FR|NL|BM|SG)/.test(isin) ||
    /unlisted|privately\s+placed/i.test(section) ||
    /\b(?:ltd|limited|plc|inc|corp|holdings)\b/i.test(name)
  ) {
    return "equity";
  }
  if (!isin && EQUITY_RE.test(blob)) return "equity";
  return "other";
}

function industryAndRating(h) {
  const industry = textOrNull(h?.industry);
  const combined = textOrNull(h?.industry_rating);
  const rating = textOrNull(h?.rating);
  if (rating && industry) {
    return {
      industry,
      rating: looksRating(combined) && combined.toLowerCase() !== industry.toLowerCase() ? combined : rating,
    };
  }
  if (rating) {
    return { industry: industry || (looksRating(combined) ? null : combined), rating };
  }
  if (combined && looksRating(combined) && !industry) return { industry: null, rating: combined };
  if (combined && !industry) return { industry: looksRating(combined) ? null : combined, rating: looksRating(combined) ? combined : null };
  if (combined && industry && combined.toLowerCase() !== industry.toLowerCase()) {
    return { industry, rating: looksRating(combined) ? combined : null };
  }
  return { industry, rating: null };
}

function marketValueScale(rawMvs) {
  const maxAbs = rawMvs.reduce((m, n) => (n == null ? m : Math.max(m, Math.abs(n))), 0);
  // Parsed AMFI books are in ₹ lakh. Absolute rupees show up as 8+ digit holdings.
  if (maxAbs >= 1e8) return 1e-5;
  return 1;
}

function normalizedPcts(rawHoldings, marketValues) {
  const parsed = rawHoldings.map((h) => parseNumber(h?.pct_nav));
  const present = parsed.filter((n) => n != null);
  const sum = present.reduce((a, b) => a + b, 0);
  const maxAbs = present.reduce((m, n) => Math.max(m, Math.abs(n)), 0);
  const fractional = present.length > 0 && maxAbs <= 1.5 && Math.abs(sum) >= 0.85 && Math.abs(sum) <= 1.15;
  const totalMv = marketValues.reduce((a, b) => a + (b || 0), 0);
  return parsed.map((pct, i) => {
    let n = fractional && pct != null ? pct * 100 : pct;
    const mv = marketValues[i];
    if ((n == null || Math.abs(n) < 1e-12) && mv != null && Math.abs(mv) > 0 && totalMv) {
      n = (mv / totalMv) * 100;
    }
    return round6(n);
  });
}

function pick(obj, keys, extras = {}) {
  const out = {};
  for (const k of keys) out[k] = extras[k] !== undefined ? extras[k] : obj[k] ?? null;
  return out;
}

export function shapeHoldingsPayload(scheme, portfolio) {
  const metaIn = portfolio?.meta && !Array.isArray(portfolio) ? portfolio.meta : {};
  const rawHoldings = Array.isArray(portfolio)
    ? portfolio
    : Array.isArray(portfolio?.holdings)
      ? portfolio.holdings
      : [];

  const rawMvs = rawHoldings.map((h) => parseNumber(h?.market_value));
  const scale = marketValueScale(rawMvs);
  const marketValues = rawMvs.map((n) => round6(n == null ? null : n * scale));
  const pcts = normalizedPcts(rawHoldings, marketValues);

  const holdings = rawHoldings.map((h, i) => {
    const { industry, rating } = industryAndRating(h);
    return pick(
      {},
      HOLDING_KEYS,
      {
        holding_type: classifyHoldingType(h),
        instrument: textOrNull(h?.instrument) || "",
        isin: textOrNull(h?.isin),
        section: textOrNull(h?.section),
        industry,
        rating,
        coupon: round6(parseNumber(h?.coupon)),
        maturity_date: textOrNull(h?.maturity_date),
        quantity: round6(parseNumber(h?.quantity)),
        market_value: marketValues[i],
        pct_nav: pcts[i],
        ytm: round6(parseNumber(h?.ytm)),
        ytc: round6(parseNumber(h?.ytc)),
        instrument_yield: round6(parseNumber(h?.instrument_yield)),
        listed_status: textOrNull(h?.listed_status),
        underlying: textOrNull(h?.underlying),
        position_side: textOrNull(h?.position_side),
      },
    );
  });

  const schemeOut = pick(
    {},
    SCHEME_KEYS,
    {
      amfi_code: textOrNull(scheme?.amfi_code),
      name: textOrNull(scheme?.name),
      amc_name: textOrNull(scheme?.amc_name),
      parent_name: textOrNull(scheme?.parent_name),
      parent_amfi: textOrNull(scheme?.parent_amfi),
      nav: round6(parseNumber(scheme?.nav)),
      nav_date: textOrNull(scheme?.nav_date),
      isin: textOrNull(scheme?.isin),
      category: textOrNull(scheme?.category),
    },
  );

  const meta = pick(
    {},
    META_KEYS,
    {
      as_of: textOrNull(metaIn.as_of) || textOrNull(scheme?.as_of),
      disclosure_type: textOrNull(metaIn.disclosure_type),
      period: textOrNull(metaIn.period),
      disclosure_shortcode: textOrNull(scheme?.shortcode) || textOrNull(metaIn.shortcode),
      holding_count: holdings.length,
      source_file: textOrNull(metaIn.source_file) || textOrNull(scheme?.source_file),
      market_value_unit: MARKET_VALUE_UNIT,
      pct_nav_unit: PCT_NAV_UNIT,
    },
  );

  return {
    amfi_code: schemeOut.amfi_code,
    scheme: schemeOut,
    meta,
    holdings,
  };
}
