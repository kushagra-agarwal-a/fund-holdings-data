#!/usr/bin/env node
/**
 * CI guard: catalog available_as_of / latest_as_of must match on-disk portfolio files.
 *
 *   node scripts/verify-catalog-coverage.mjs
 *   node scripts/verify-catalog-coverage.mjs --out=/path/to/fund-holdings-data
 */
import { readFileSync, existsSync } from "node:fs";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import {
  assertNoPhantomAsOfLinks,
  assertCatalogPortfolioCoverage,
} from "./lib/asof-portfolios.mjs";
import { defaultHoldingsOutDir } from "./lib/resolve-holdings-out-dir.mjs";

const __dirname = dirname(fileURLToPath(import.meta.url));
const ROOT = join(__dirname, "..");

function argValue(name, fallback = null) {
  const prefix = `--${name}=`;
  const hit = process.argv.find((a) => a.startsWith(prefix));
  return hit ? hit.slice(prefix.length) : fallback;
}

const outDir = argValue("out", defaultHoldingsOutDir(ROOT));
const catalogPath = join(outDir, "catalog/amfi-lookup.json");

if (!existsSync(catalogPath)) {
  console.error(`Missing catalog: ${catalogPath}`);
  process.exit(1);
}

const catalog = JSON.parse(readFileSync(catalogPath, "utf8"));
const phantom = assertNoPhantomAsOfLinks(outDir, catalog);
const coverage = assertCatalogPortfolioCoverage(outDir, catalog);

let failed = false;

if (!phantom.ok) {
  failed = true;
  console.error(
    `Phantom available_as_of links: ${phantom.phantom.length} (catalog date with no portfolio file)`,
  );
  for (const p of phantom.phantom.slice(0, 12)) {
    console.error(`  ${p.sample_amfi} → ${p.portfolio_id}@${p.as_of}`);
  }
  if (phantom.phantom.length > 12) {
    console.error(`  … ${phantom.phantom.length - 12} more`);
  }
}

if (!coverage.ok) {
  console.warn(
    `Latest as-of file gaps: ${coverage.missing.length} portfolio(s)`,
  );
  for (const m of coverage.missing.slice(0, 8)) {
    console.warn(`  ${m.sample_amfi || "?"} → ${m.portfolio_id}@${m.as_of || "?"}`);
  }
}

if (failed) {
  process.exit(1);
}

console.log(
  JSON.stringify(
    {
      ok: true,
      out: outDir,
      phantom_links: 0,
      latest_gaps: coverage.missing.length,
    },
    null,
    2,
  ),
);
