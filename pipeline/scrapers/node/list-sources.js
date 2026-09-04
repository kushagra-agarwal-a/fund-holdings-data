import { existsSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const root = join(dirname(fileURLToPath(import.meta.url)), "../..");
const registryPath = existsSync(join(root, "registry/amcs.json"))
  ? join(root, "registry/amcs.json")
  : join(root, "data/sources/amcs.json");

const registry = JSON.parse(readFileSync(registryPath, "utf8"));
const amcs = registry.amcs ?? [];
const statsOnly = process.argv.includes("--stats");
const statusFilter = process.argv
  .find((a) => a.startsWith("--status="))
  ?.slice("--status=".length);
const typeFilter = process.argv
  .find((a) => a.startsWith("--type="))
  ?.slice("--type=".length);

const focusTypes = registry.focus ?? ["fortnightly", "monthly"];

const rows = statusFilter
  ? amcs.filter((a) => a.status === statusFilter)
  : amcs;

function urlFor(amc, type) {
  return amc.disclosures?.[type]?.page_url ?? null;
}

if (statsOnly) {
  const byStatus = Object.create(null);
  for (const a of amcs) {
    byStatus[a.status] = (byStatus[a.status] ?? 0) + 1;
  }
  console.log(`Source: ${registry.source}`);
  console.log(`Seeded: ${registry.sourced_at}`);
  console.log(`Focus:  ${(registry.focus ?? []).join(", ")}`);
  console.log(`AMCs:   ${amcs.length}`);
  console.log("Overall status:");
  for (const [k, v] of Object.entries(byStatus).sort()) {
    console.log(`  ${k.padEnd(12)} ${v}`);
  }
  console.log("Per type (page URL present):");
  for (const t of [...focusTypes, "semi_annually"]) {
    const withUrl = amcs.filter((a) => urlFor(a, t)).length;
    console.log(`  ${t.padEnd(16)} ${withUrl}/${amcs.length}`);
  }
  process.exit(0);
}

const typesToShow = typeFilter ? [typeFilter] : focusTypes;

console.log(`# ${rows.length} AMCs (of ${amcs.length})\n`);
for (const a of rows) {
  console.log(`${a.name}`);
  console.log(`  id:     ${a.id}`);
  console.log(`  status: ${a.status}`);
  for (const t of typesToShow) {
    const url = urlFor(a, t) ?? "(none)";
    const st = a.disclosures?.[t]?.status ?? "?";
    console.log(`  ${t}: [${st}] ${url}`);
  }
  if (a.notes) console.log(`  notes:  ${a.notes}`);
  console.log("");
}
