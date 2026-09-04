#!/usr/bin/env node
/** Compat shim → scrapers/node/fetch-period.js */
import { pathToFileURL } from "node:url";
import { join, dirname } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
await import(pathToFileURL(join(root, "scrapers/node/fetch-period.js")).href);
