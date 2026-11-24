// @ts-check
import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";

const SITE = "https://pgbackup-r2.iotabyt.com";
const OG_ALT =
  "pgbackup-r2: encrypted Postgres backups to R2, with restore drills that prove they work";

export default defineConfig({
  // The Changelog page imports ../CHANGELOG.md from the repo root (one source of truth).
  vite: {
    server: { fs: { allow: [".."] } },
    // Never inline fonts as data: URLs (Vite inlines files under 4 KB). The CSP allows fonts
    // from 'self' only.
    build: { assetsInlineLimit: (file) => (/\.(woff2?|ttf|otf)$/.test(file) ? false : undefined) },
  },
  site: SITE,
  trailingSlash: "always",
  integrations: [
    starlight({
      title: "pgbackup-r2",
      description:
        "Encrypted Postgres backups to R2 or any S3, with restore drills that prove they work.",
      social: [
        { icon: "github", label: "GitHub", href: "https://github.com/iotabyt/pgbackup-r2" },
      ],
      // Starlight appends the file's path from this folder (src/content/docs/...).
      editLink: { baseUrl: "https://github.com/iotabyt/pgbackup-r2/edit/main/docs/" },
      lastUpdated: true,
      // src/pages/404.astro replaces Starlight's 404, with a search button.
      disable404Route: true,
      // The pgbackup-r2 mark (pgbackup-r2-brand/): three pills and a red check.
      logo: {
        light: "./src/assets/mark.svg",
        dark: "./src/assets/mark-reversed.svg",
        alt: "pgbackup-r2",
      },
      favicon: "/favicon.svg",
      head: [
        { tag: "link", attrs: { rel: "icon", href: "/favicon.ico", sizes: "any" } },
        { tag: "link", attrs: { rel: "apple-touch-icon", href: "/apple-touch-icon.png" } },
        { tag: "link", attrs: { rel: "manifest", href: "/site.webmanifest" } },
        { tag: "meta", attrs: { name: "theme-color", content: "#00091E" } },
        { tag: "meta", attrs: { property: "og:image", content: `${SITE}/og.png` } },
        { tag: "meta", attrs: { property: "og:image:width", content: "1280" } },
        { tag: "meta", attrs: { property: "og:image:height", content: "640" } },
        { tag: "meta", attrs: { property: "og:image:alt", content: OG_ALT } },
        { tag: "meta", attrs: { name: "twitter:image", content: `${SITE}/og.png` } },
        { tag: "meta", attrs: { name: "twitter:image:alt", content: OG_ALT } },
        { tag: "meta", attrs: { name: "twitter:card", content: "summary_large_image" } },
      ],
      customCss: [
        // Self-hosted fonts: no requests to Google Fonts.
        "@fontsource-variable/outfit",
        "@fontsource/jetbrains-mono/400.css",
        "@fontsource/jetbrains-mono/500.css",
        "@fontsource/jetbrains-mono/600.css",
        "./src/styles/custom.css",
      ],
      components: {
        Footer: "./src/components/Footer.astro",
      },
      sidebar: [
        {
          label: "Start here",
          items: [
            { label: "Introduction", link: "/" },
            { slug: "start/why" },
            { slug: "start/quick-start" },
            { slug: "start/install" },
          ],
        },
        {
          label: "Guides",
          items: [
            { slug: "guides/r2" },
            { slug: "guides/other-storage" },
            { slug: "guides/vps" },
            { slug: "guides/managed-databases" },
            { slug: "guides/neon" },
            { slug: "guides/supabase" },
            { slug: "guides/kamal" },
            { slug: "guides/existing-folders" },
            { slug: "guides/monthly-drills" },
            { slug: "guides/restoring" },
          ],
        },
        {
          label: "Concepts",
          items: [
            { slug: "concepts/how-it-works" },
            { slug: "concepts/keys" },
            { slug: "concepts/retention" },
            { slug: "concepts/postgres-versions" },
          ],
        },
        {
          label: "Reference",
          items: [
            { slug: "reference/pgb" },
            { slug: "reference/cli" },
            { slug: "reference/environment" },
            { slug: "reference/exit-codes" },
          ],
        },
        { slug: "troubleshooting" },
        { slug: "changelog" },
      ],
      // Pagefind search is built in and on by default; it only exists after `npm run build`.
    }),
  ],
});
