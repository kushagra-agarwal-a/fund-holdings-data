#!/usr/bin/env node
/**
 * Upload enriched portfolio.json files to Backblaze B2.
 *
 * Reads data/parsed/b2_holdings_manifest.json (from enrich_holdings_identifiers.py).
 *
 *   node --env-file=holdings-browser/.env.local scripts/upload-holdings-to-b2.mjs
 *   node --env-file=holdings-browser/.env.local scripts/upload-holdings-to-b2.mjs --dry-run
 */
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");
const require = createRequire(join(root, "holdings-browser", "package.json"));
const {
  S3Client,
  PutObjectCommand,
  GetObjectCommand,
  ListObjectsV2Command,
} = require("@aws-sdk/client-s3");

const dryRun = process.argv.includes("--dry-run");
const verifyN = 3;
const CONCURRENCY = 4;

const manifest = JSON.parse(
  readFileSync(join(root, "data/parsed/b2_holdings_manifest.json"), "utf8"),
);

function requireEnv(name) {
  const v = process.env[name];
  if (!v) throw new Error(`Missing ${name}`);
  return v;
}

async function mapPool(items, n, fn) {
  const q = items.slice();
  await Promise.all(
    Array.from({ length: Math.min(n, items.length) }, async () => {
      while (q.length) await fn(q.shift());
    }),
  );
}

async function main() {
  const Bucket = process.env.B2_BUCKET || "pocketedge";
  const endpoint = process.env.B2_ENDPOINT || "https://s3.us-east-005.backblazeb2.com";
  const region = process.env.B2_REGION || "us-east-005";
  const client = new S3Client({
    region,
    endpoint,
    credentials: {
      accessKeyId: requireEnv("B2_KEY_ID"),
      secretAccessKey: requireEnv("B2_APPLICATION_KEY"),
    },
    forcePathStyle: true,
  });

  const schemes = manifest.schemes || [];
  console.log(
    `Upload ${schemes.length} holdings → s3://${Bucket}/${manifest.b2_prefix} (${dryRun ? "dry-run" : "live"})`,
  );
  if (dryRun) {
    for (const s of schemes.slice(0, 8)) {
      console.log(
        `  ${s.b2_key}  amfi=${s.amfi_code || "-"}  ${s.scheme_name}  as_of=${s.as_of}`,
      );
    }
    console.log(`  … ${Math.max(0, schemes.length - 8)} more`);
    return;
  }

  let uploaded = 0;
  let failed = 0;
  await mapPool(schemes, CONCURRENCY, async (s) => {
    const body = readFileSync(join(root, s.local_path));
    try {
      await client.send(
        new PutObjectCommand({
          Bucket,
          Key: s.b2_key,
          Body: body,
          ContentType: "application/json; charset=utf-8",
        }),
      );
      uploaded += 1;
      if (uploaded % 100 === 0) {
        console.log(`  uploaded ${uploaded}/${schemes.length} failed=${failed}`);
      }
    } catch (err) {
      failed += 1;
      console.error(`  FAIL ${s.b2_key}`, err?.$metadata?.httpStatusCode, err?.message);
    }
  });

  const indexKey = "fund-disclosures/holdings/index.json";
  const indexBody = Buffer.from(
    JSON.stringify(
      {
        generated_at: manifest.generated_at,
        scheme_count: manifest.scheme_count,
        with_amfi_code: manifest.with_amfi_code,
        without_amfi_code: manifest.without_amfi_code,
        bucket: Bucket,
        prefix: manifest.b2_prefix,
        schemes: schemes.map(({ local_path, ...rest }) => rest),
      },
      null,
      2,
    ) + "\n",
  );
  await client.send(
    new PutObjectCommand({
      Bucket,
      Key: indexKey,
      Body: indexBody,
      ContentType: "application/json; charset=utf-8",
    }),
  );

  const samples = schemes.filter((s) => s.amfi_code).slice(0, verifyN);
  for (const s of samples) {
    const got = await client.send(new GetObjectCommand({ Bucket, Key: s.b2_key }));
    const buf = Buffer.from(await got.Body.transformToByteArray());
    const parsed = JSON.parse(buf.toString("utf8"));
    console.log("fetch-verify", {
      key: s.b2_key,
      http: got.$metadata?.httpStatusCode,
      amfi_code: parsed.meta?.amfi_code,
      scheme_name: parsed.meta?.scheme_name,
      as_of: parsed.meta?.as_of,
      holdings: parsed.holdings?.length,
    });
  }

  const listed = await client.send(
    new ListObjectsV2Command({ Bucket, Prefix: manifest.b2_prefix, MaxKeys: 5 }),
  );
  console.log(
    JSON.stringify(
      {
        uploaded,
        failed,
        index: indexKey,
        listed_prefix_sample: (listed.Contents || []).map((o) => o.Key),
      },
      null,
      2,
    ),
  );
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
