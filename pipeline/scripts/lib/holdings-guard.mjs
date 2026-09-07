#!/usr/bin/env node
/**
 * Guards against accidental holdings-data regression (catalog links or on-disk as-of trees).
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import {
  countDedupedAsOfDir,
  parentPortfolioIds,
  scanExistingAsOfDirs,
  assertCatalogPortfolioCoverage,
  assertNoPhantomAsOfLinks,
} from "./asof-portfolios.mjs";

const AS_OF_RE = /^\d{4}-\d{2}-\d{2}$/;

/** Schemes in catalog with a given as_of in available_as_of. */
export function countCatalogSchemesForDate(catalog, asOf) {
  let n = 0;
  for (const row of Object.values(catalog || {})) {
    const dates = row?.available_as_of;
    if (Array.isArray(dates) && dates.includes(asOf)) n += 1;
  }
  return n;
}

/** Per as-of date: deduped portfolio file counts on disk. */
export function asOfDirCounts(outDir, catalog = null) {
  const root = join(outDir, "portfolios", "asof");
  const counts = new Map();
  if (!existsSync(root)) return counts;
  for (const date of readdirSync(root)) {
    if (!AS_OF_RE.test(date)) continue;
    const dir = join(root, date);
    try {
      if (!statSync(dir).isDirectory()) continue;
    } catch {
      continue;
    }
    counts.set(date, countDedupedAsOfDir(dir, catalog));
  }
  return counts;
}

/** Per as-of date: schemes linked in catalog. */
export function catalogAsOfCounts(catalog) {
  const counts = new Map();
  for (const row of Object.values(catalog || {})) {
    for (const d of row?.available_as_of || []) {
      if (!AS_OF_RE.test(String(d))) continue;
      counts.set(d, (counts.get(d) || 0) + 1);
    }
  }
  return counts;
}

function formatDelta(before, after) {
  return `${before} → ${after} (${after - before >= 0 ? "+" : ""}${after - before})`;
}

/**
 * Fail when catalog or on-disk as-of coverage shrinks vs baseline.
 * @param {{ allowRegression?: boolean, label?: string, syncedDates?: string[] }} opts
 */
export function assertNoHoldingsRegression(
  outDir,
  beforeCatalog,
  afterCatalog,
  { allowRegression = false, label = "sync", syncedDates = [] } = {},
) {
  if (allowRegression) return { ok: true, regressions: [] };

  const synced = new Set(
    (syncedDates || []).map((d) => String(d).trim()).filter((d) => AS_OF_RE.test(d)),
  );

  const beforeDirs = asOfDirCounts(outDir, beforeCatalog);
  const afterDirs = asOfDirCounts(outDir, afterCatalog);
  const beforeCat = catalogAsOfCounts(beforeCatalog);
  const afterCat = catalogAsOfCounts(afterCatalog);

  const regressions = [];
  const allDates = new Set([
    ...beforeDirs.keys(),
    ...beforeCat.keys(),
    ...afterDirs.keys(),
    ...afterCat.keys(),
    ...synced,
  ]);

  for (const date of [...allDates].sort()) {
    const prevFiles = beforeDirs.get(date) || 0;
    const nextFiles = afterDirs.get(date) || 0;
    if (prevFiles > 0 && nextFiles < prevFiles) {
      regressions.push(
        `${date}: portfolio files ${formatDelta(prevFiles, nextFiles)}`,
      );
    }

    // Catalog link checks only for dates being written in this sync.
    if (synced.has(date)) {
      const prevSchemes = beforeCat.get(date) || 0;
      const nextSchemes = afterCat.get(date) || 0;
      if (prevSchemes > 0 && nextSchemes < prevSchemes) {
        regressions.push(
          `${date}: catalog scheme links ${formatDelta(prevSchemes, nextSchemes)}`,
        );
      }
      const diskFiles = afterDirs.get(date) || 0;
      if (diskFiles > 0 && nextSchemes > diskFiles + 10) {
        regressions.push(
          `${date}: catalog links (${nextSchemes}) exceed on-disk portfolios (${diskFiles})`,
        );
      }
    }
  }

  if (regressions.length) {
    const msg =
      `Holdings data regression blocked (${label}):\n` +
      regressions.map((r) => `  - ${r}`).join("\n") +
      "\nRe-run with --allow-regression only if the drop is intentional.";
    throw new Error(msg);
  }
  return { ok: true, regressions: [] };
}

/** Load catalog JSON from repo checkout if present. */
export function loadRepoCatalog(outDir) {
  const p = join(outDir, "catalog/amfi-lookup.json");
  if (!existsSync(p)) return {};
  try {
    return JSON.parse(readFileSync(p, "utf8"));
  } catch {
    return {};
  }
}

/**
 * Merge available_as_of / latest_as_of from an existing published catalog into a
 * freshly built lookup, keeping only dates whose portfolio files still exist.
 */
export function mergeCatalogAsOfFromRepo(outDir, freshCatalog, repoCatalog) {
  const asOfMap = scanExistingAsOfDirs(outDir, freshCatalog);
  const out = { ...freshCatalog };

  for (const [code, row] of Object.entries(out)) {
    if (!row || typeof row !== "object") continue;
    const pid = String(
      row.portfolio_id || row.parent_amfi || row.amfi_code || "",
    ).trim();
    if (!/^\d{4,8}$/.test(pid)) continue;

    const merged = new Set(asOfMap.get(pid) || []);
    const prev = repoCatalog?.[code];
    for (const d of prev?.available_as_of || []) {
      const day = String(d).trim();
      if (!AS_OF_RE.test(day)) continue;
      const path = join(outDir, "portfolios/asof", day, `${pid}.json`);
      if (existsSync(path)) merged.add(day);
    }

    if (!merged.size) continue;
    const available = [...merged].sort().reverse();
    const latest = available[0] || null;
    out[code] = {
      ...row,
      portfolio_id: row.portfolio_id || pid,
      available_as_of: available,
      latest_as_of: latest,
    };
  }
  return out;
}

/**
 * Fail when catalog as-of metadata does not match on-disk portfolio files.
 * Phantom link check is zero-tolerance; latest-file gaps allow a small legacy floor.
 */
export function enforceCatalogIntegrity(outDir, catalog, {
  label = "catalog integrity",
  maxMissingLatest = 5,
} = {}) {
  const phantom = assertNoPhantomAsOfLinks(outDir, catalog);
  if (!phantom.ok) {
    const sample = phantom.phantom.slice(0, 8);
    throw new Error(
      `${label}: ${phantom.phantom.length} phantom available_as_of link(s). ` +
        `Examples: ${sample.map((p) => `${p.portfolio_id}@${p.as_of}`).join(", ")}`,
    );
  }

  const coverage = assertCatalogPortfolioCoverage(outDir, catalog);
  if (!coverage.ok && coverage.missing.length > maxMissingLatest) {
    const sample = coverage.missing.slice(0, 8);
    throw new Error(
      `${label}: ${coverage.missing.length} portfolio(s) missing latest as-of file. ` +
        `Examples: ${sample.map((m) => `${m.portfolio_id}@${m.as_of || "?"}`).join(", ")}`,
    );
  }
  if (!coverage.ok) {
    const sample = coverage.missing.slice(0, 8);
    console.warn(
      `Warning (${label}): ${coverage.missing.length} portfolio(s) without latest file: ` +
        sample.map((m) => `${m.portfolio_id}@${m.as_of || "?"}`).join(", "),
    );
  }
  return { phantom, coverage };
}

/**
 * Fail when catalog as-of metadata does not match on-disk portfolio files.
 * Phantom link check is zero-tolerance; latest-file gaps allow a small legacy floor.
 */
export function enforceCatalogIntegrity(outDir, catalog, {
  label = "catalog integrity",
  maxMissingLatest = 5,
} = {}) {
  const phantom = assertNoPhantomAsOfLinks(outDir, catalog);
  if (!phantom.ok) {
    const sample = phantom.phantom.slice(0, 8);
    throw new Error(
      `${label}: ${phantom.phantom.length} phantom available_as_of link(s). ` +
        `Examples: ${sample.map((p) => `${p.portfolio_id}@${p.as_of}`).join(", ")}`,
    );
  }

  const coverage = assertCatalogPortfolioCoverage(outDir, catalog);
  if (!coverage.ok && coverage.missing.length > maxMissingLatest) {
    const sample = coverage.missing.slice(0, 8);
    throw new Error(
      `${label}: ${coverage.missing.length} portfolio(s) missing latest as-of file. ` +
        `Examples: ${sample.map((m) => `${m.portfolio_id}@${m.as_of || "?"}`).join(", ")}`,
    );
  }
  if (!coverage.ok) {
    const sample = coverage.missing.slice(0, 8);
    console.warn(
      `Warning (${label}): ${coverage.missing.length} portfolio(s) without latest file: ` +
        sample.map((m) => `${m.portfolio_id}@${m.as_of || "?"}`).join(", "),
    );
  }
  return { phantom, coverage };
}
