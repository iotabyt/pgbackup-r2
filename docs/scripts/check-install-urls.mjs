// Fails if a raw GitHub install URL appears where users copy install commands.
//
// Users should copy the short URLs (https://pgbackup-r2.iotabyt.com/install.sh and
// /install.ps1), which public/_redirects sends on to GitHub. The raw URLs belong in one
// place only: the "Install without the short URL" note on the Install pgb page, for
// networks whose proxies block redirects. Also checks the redirects are still there.
import { readdir, readFile } from "node:fs/promises";
import { join, relative } from "node:path";

const DOCS = new URL("..", import.meta.url).pathname;
const REPO = join(DOCS, "..");
const RAW = /https:\/\/raw\.githubusercontent\.com\/iotabyt\/pgbackup-r2\/[^/\s]+\/scripts\/(install\.sh|install\.ps1|pgb\.ps1|pgb)\b/g;
const NOTE_PAGE = join(DOCS, "src/content/docs/start/install.mdx");
const NOTE_HEADING = "## Install without the short URL";

async function* pages(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) yield* pages(path);
    else if (/\.mdx?$/.test(entry.name)) yield path;
  }
}

/** The text of the note's section: from its heading to the next "## " heading. */
function noteRange(text) {
  const start = text.indexOf(`\n${NOTE_HEADING}\n`);
  if (start === -1) return null;
  const next = text.indexOf("\n## ", start + 1);
  return [start, next === -1 ? text.length : next];
}

const problems = [];
const files = [join(REPO, "README.md")];
for await (const page of pages(join(DOCS, "src/content"))) files.push(page);

for (const file of files) {
  const text = await readFile(file, "utf8");
  const allowed = file === NOTE_PAGE ? noteRange(text) : null;
  if (file === NOTE_PAGE && !allowed) problems.push(`${relative(REPO, file)}: no "${NOTE_HEADING}" section`);
  for (const m of text.matchAll(RAW)) {
    if (allowed && m.index > allowed[0] && m.index < allowed[1]) continue;
    const line = text.slice(0, m.index).split("\n").length;
    problems.push(`${relative(REPO, file)}:${line}: raw GitHub URL; use https://pgbackup-r2.iotabyt.com/install.${m[1].endsWith("ps1") ? "ps1" : "sh"}`);
  }
}

const redirects = await readFile(join(DOCS, "public/_redirects"), "utf8");
for (const [from, script] of [["/install.sh", "install.sh"], ["/install.ps1", "install.ps1"]]) {
  const rule = new RegExp(`^${from.replace(".", "\\.")}\\s+https://raw\\.githubusercontent\\.com/iotabyt/pgbackup-r2/main/scripts/${script.replace(".", "\\.")}\\s+302$`, "m");
  if (!rule.test(redirects)) problems.push(`docs/public/_redirects: missing "${from} … scripts/${script} 302"`);
}

if (problems.length) {
  console.error(`Install URLs: ${problems.length} problem(s)\n  ${problems.join("\n  ")}`);
  process.exit(1);
}
console.log(`Install URLs: ${files.length} files checked, short URLs only, redirects in place.`);
