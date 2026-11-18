# pgbackup-r2 brand files

The mark: three stacked pills (the database) with a red dot and check (verified by a drill).
Part of the Iotabyt family: navy #00091E, red #FF0000, pills and dots.

| File | Use |
|---|---|
| mark.svg | Logo on light backgrounds (docs light mode, README, cards) |
| mark-reversed.svg | Logo on navy/dark backgrounds |
| lockup.svg / lockup-reversed.svg | Mark + "pgbackup-r2" in JetBrains Mono (needs the font loaded; for web use) |
| favicon.svg | Browser tab icon; switches to white pills in dark mode |
| favicon.ico | Fallback favicon (16, 32, 48) |
| apple-touch-icon.png | 180×180, white background |
| icon-192.png, icon-512.png | Web app manifest icons |
| mark-512.png | Transparent PNG for anywhere SVG isn't accepted |
| social-preview.png | 1280×640: GitHub repo social preview and docs OG image |

Rules: the red dot and check always sit bottom-right. Never recolour the dot, never use the mark smaller than 16px,
and keep clear space around it equal to the dot's radius.

Where they go:
- Docs (Starlight): mark.svg + mark-reversed.svg in docs/src/assets/ (logo light/dark), favicon.svg + favicon.ico + apple-touch-icon.png in docs/public/
- GitHub: Settings → General → Social preview → upload social-preview.png
- Repo README: mark.svg at the top (64px)
