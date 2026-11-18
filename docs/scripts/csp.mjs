// Post-build: put a sha256 for every inline <script> in dist/ into the CSP in dist/_headers.
//
// Starlight writes small inline scripts into each page (theme, sidebar, tabs). Allowing them
// by hash keeps script-src free of 'unsafe-inline'. The hashes change when Starlight does, so
// they're computed from the build output every time, never hand-copied.
import { createHash } from "node:crypto";
import { readdir, readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";

const DIST = new URL("../dist/", import.meta.url).pathname;
const PLACEHOLDER = "{{SCRIPT_HASHES}}";

async function* htmlFiles(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* htmlFiles(path);
    else if (entry.name.endsWith(".html")) yield path;
  }
}

const hashes = new Set();
// Inline scripts only: <script> without a src attribute. JSON data blocks don't run, so skip them.
const inline = /<script(?![^>]*\bsrc=)(?![^>]*type="application\/(?:ld\+)?json")[^>]*>([\s\S]*?)<\/script>/g;
for await (const file of htmlFiles(DIST)) {
  const html = await readFile(file, "utf8");
  for (const [, body] of html.matchAll(inline)) {
    if (body.trim()) hashes.add(`'sha256-${createHash("sha256").update(body).digest("base64")}'`);
  }
}

const headersPath = join(DIST, "_headers");
const headers = await readFile(headersPath, "utf8");
if (!headers.includes(PLACEHOLDER)) {
  throw new Error(`${headersPath} has no ${PLACEHOLDER} to fill in`);
}
if (hashes.size === 0) throw new Error("no inline scripts found in dist/: is the build empty?");
await writeFile(headersPath, headers.replaceAll(PLACEHOLDER, [...hashes].sort().join(" ")));
console.log(`csp: ${hashes.size} inline script hashes written to dist/_headers`);
