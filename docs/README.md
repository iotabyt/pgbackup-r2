# pgbackup-r2 docs

The documentation site for pgbackup-r2, live at <https://pgbackup-r2.iotabyt.com>. It's built with [Astro Starlight](https://starlight.astro.build) and served by Cloudflare as static files.

It's a self-contained Node project, so nothing here touches the Python package, and the Python side doesn't depend on it.

| Path | What it is |
|---|---|
| `src/content/docs/` | The pages: Markdown and MDX, one file per page. The sidebar order is in `astro.config.mjs`. |
| `src/components/` | `Figure` (screenshot slots), `HowItWorks` (the diagram), `Footer` |
| `src/styles/custom.css` | The Iotabyt brand colours and type |
| `src/assets/` | Logos. Screenshots go in `src/assets/screenshots/`. |
| `public/_headers` | Security and cache headers for Cloudflare |
| `scripts/csp.mjs` | Runs after the build and adds the CSP hashes to `dist/_headers` |
| `wrangler.jsonc` | The Cloudflare Worker that serves `dist/` |

The Changelog page imports `../CHANGELOG.md` from the repo root, so that file stays the one source.

## Run it locally

You need Node 22. With nvm, `nvm use` reads `.nvmrc`.

```bash
cd docs
npm ci
npm run dev        # http://localhost:4321, hot reload while writing
npm run build      # the same build Cloudflare runs
npm run preview    # serves dist/ through wrangler at http://localhost:8787, with _headers applied
```

- **`npm run dev`** is for writing. Pages reload as you save. It doesn't apply `_headers`, and search doesn't work, because Pagefind's index only exists after a build.
- **`npm run build`** runs three things in order:
  - `astro check`, which must report 0 errors
  - `astro build`, which writes the pages and the Pagefind search index into `dist/`
  - `scripts/csp.mjs`, which hashes every inline script in `dist/` into the CSP in `dist/_headers`
- **`npm run preview`** serves `dist/` through `wrangler dev`, the way Cloudflare will. That includes:
  - `_headers`, with the CSP
  - real 404s, from `src/pages/404.astro`
  - trailing-slash redirects

  Run `npm run build` first. Wrangler downloads its local runtime the first time, and no Cloudflare login is needed.

If you change `ec.config.mjs` (code block colours) or the theme and a page still looks old, clear Astro's content cache and build again:

```bash
rm -rf .astro node_modules/.astro && npm run build
```

Cloudflare always builds from scratch, so this only matters locally.

## Check before a pull request

CI (`.github/workflows/docs.yml`) runs the build and the link check on every pull request that touches `docs/` or `CHANGELOG.md`. To check everything yourself, run `npm run build && npm run preview`, then go through this list.

**Search works.** Open <http://localhost:8787>, press `/`, and search for "bucket lock". Results should appear straight away.

**The headers are there:**

```bash
curl -sI http://localhost:8787/
```

You should see:

- `Strict-Transport-Security`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy`
- `X-Frame-Options: DENY`
- `Permissions-Policy`
- `Cross-Origin-Opener-Policy`
- `Content-Security-Policy`, with `sha256-…` hashes in `script-src`

`/_astro/*` files and `/pagefind/index/*` should have `Cache-Control: public, max-age=31536000, immutable`.

**The CSP breaks nothing.** Open the browser's developer console, then:

- search for something
- switch the theme between light, dark and auto
- click a code block's copy button
- switch a tab (bash or PowerShell)
- open `/no-such-page/` and click **Search the docs**

The console should show no "Content Security Policy" errors.

**Contrast, in light and dark.** Use your browser's accessibility checker (Lighthouse, or axe DevTools) on a few pages in each theme. Look especially at pages with links inside notes (asides) and with code blocks. Every text colour in the brand palette meets WCAG AA (4.5:1):

| | Light | Dark |
|---|---|---|
| Body text | `#00091E` on white | `#C9CFDB` on `#00091E` |
| Links | `#C40000` | `#FF5C5C` |
| Links inside asides | the aside's text colour, underlined | the aside's text colour, underlined |
| Code tokens | GitHub's light theme, with red tokens remapped to blue | GitHub's dark theme, remapped the same way |

**Phone width.** In the browser's device toolbar, at 375px wide:

- no page scrolls sideways (wide tables scroll inside their own box)
- the menu button opens the sidebar

**Links.** The CI check, run locally with [lychee](https://lychee.cli.rs) (`brew install lychee`, or `cargo install lychee`):

```bash
lychee --offline --root-dir "$PWD/dist" --index-files index.html --include-fragments 'dist/**/*.html'
```

`--offline` checks internal links only, so external sites can't make it flaky. `--include-fragments` checks `#anchor` links too, and `--index-files` resolves `/page/` to `/page/index.html`, the way Cloudflare does. Expect `0 Errors`.

## Writing pages

- **Voice:** direct, plain, no marketing.
- **Commands:** give `pgb` first. Show the `docker run` equivalent in a `<Tabs syncKey="tool">` (`pgb` / `docker`) or a `<details>` block.
- **Shells:** use `<Tabs syncKey="shell">` where commands differ: bash, zsh, fish, PowerShell.
- **Screenshots:** `<Figure file="r2-2-create-bucket.png" alt="…" />`. Until the file exists in `src/assets/screenshots/`, the page shows a placeholder naming it.
- **New pages:** add them to the sidebar in `astro.config.mjs`.
- **Red:** the brand allows red once per page, outside links: the logo. Don't add red elsewhere.

## Deploy (Cloudflare Workers Builds)

The site deploys from GitHub on its own: every push to `main` that touches the docs builds and publishes it. The `docs.yml` workflow only checks pull requests and never deploys. One-time setup, in the **Iotabyt** Cloudflare account:

1. **Workers & Pages → Create → Import a repository** → `iotabyt/pgbackup-r2`.
2. **Root directory: `docs`.**
   - **Build command:** `npm run build`. It runs `astro check`, the build and the CSP hashing.
   - **Deploy command:** `npx wrangler deploy`. It reads `wrangler.jsonc`: the Worker `pgbackup-r2-docs`, serving `./dist`.
   - Cloudflare runs `npm ci` itself, from `package-lock.json`. Node comes from `.nvmrc` (22). If the build log shows a different Node version, add the build variable `NODE_VERSION` = `22`.
3. **Settings → Build → Build watch paths:** include `docs/*` and `CHANGELOG.md`, nothing else.
   - Code-only commits then don't rebuild the site. (Docs-only commits already skip the Python CI and image builds, through `paths-ignore` in `ci.yml`.)
   - `CHANGELOG.md` is there because the Changelog page is built from it. Without it, a changelog-only commit wouldn't update the site.
4. **Domains & Routes → Add → Custom Domain:** `pgbackup-r2.iotabyt.com`. The iotabyt.com zone is in the same account, so Cloudflare creates the DNS record and the certificate.
5. **Once it's live,** set `"workers_dev": false` in `wrangler.jsonc` and push. The site is then only on its own domain, and the `*.workers.dev` URL stops serving it. `preview_urls` is off too: preview URLs would be public, indexable copies of the site. Check changes locally with `npm run build && npm run preview`.
6. **Search and analytics.**
   - **Search Console:** the iotabyt.com Domain property already covers subdomains, so there's nothing to verify. Under **Sitemaps**, submit `https://pgbackup-r2.iotabyt.com/sitemap-index.xml`.
   - **Web Analytics:** in Cloudflare, **Analytics & Logs → Web Analytics → Add a site**, and choose `pgbackup-r2.iotabyt.com`. The CSP already allows its beacon (`static.cloudflareinsights.com` and `cloudflareinsights.com`). After turning it on, check the browser console once for CSP errors.

### After the first deploy

```bash
curl -sI https://pgbackup-r2.iotabyt.com/                 # 200, with HSTS, the CSP and the rest
curl -sI https://pgbackup-r2.iotabyt.com/start/quick-start  # 307 to the trailing-slash URL
curl -sI https://pgbackup-r2.iotabyt.com/no-such-page/      # 404, the site's own 404 page
curl -sI https://pgbackup-r2.iotabyt.com/og.png             # 200, image/png: the link preview image
```

Then open the site, search for something, and toggle the theme with the console open. There should be no CSP errors.

Things outside this repo:

- **Social preview.** In the GitHub repo's **Settings → General → Social preview**, upload `.github/assets/social-preview.png`.
- **Website link.** In the repo's **About** settings, set the website to `https://pgbackup-r2.iotabyt.com`.
