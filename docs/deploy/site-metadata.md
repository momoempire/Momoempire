# Site metadata, favicon, robots.txt and sitemap.xml (EMP-WL-014)

Everything that depends on the brand, domain or share image is set at **build time** from
`REACT_APP_*` variables (Cloudflare Pages build settings or `frontend/.env`), so nothing is
hard-coded. **TODO(Brann):** brand name, description, domain and share image are Brann's
decision. Until they are set, the defaults below apply.

| Variable | Used for | When unset |
|---|---|---|
| `REACT_APP_SITE_NAME` | `<title>`, `og:title`, `og:site_name`, `twitter:title` | current title `Empire AI Office` (placeholder) |
| `REACT_APP_SITE_DESCRIPTION` | `<meta name="description">`, `og:description`, `twitter:description` | current text `Empire AI Office platform` (placeholder) |
| `REACT_APP_SITE_URL` | `og:url`, `sitemap.xml`, `Sitemap:` line in `robots.txt` | no `og:url`, no `sitemap.xml`, robots without a Sitemap line |
| `REACT_APP_OG_IMAGE_URL` | `og:image`, `twitter:image`, `twitter:card=summary_large_image` | no image tags, `twitter:card=summary` |

Both URLs must be absolute `http(s)` URLs; anything else fails the build with an
`[EMP-WL-014]` message. Values are HTML-escaped.

## What the build produces
- `index.html`: title and the description/Open Graph/Twitter tags (`frontend/site-meta.js`,
  wired in `craco.config.js`).
- `robots.txt`: always. Waitlist build (`REACT_APP_WAITLIST_ONLY=true`): allow all. Normal build:
  also `Disallow` for the signed-in app, admin, auth flows and token links (`/app`, `/admin`,
  `/login`, `/signup`, `/reset-password`, `/invite`, `/portal/`, `/reviews/`, …).
- `sitemap.xml`: only with `REACT_APP_SITE_URL`. Waitlist build: `/`, `/privacy`, `/terms`;
  normal build adds `/pricing`.
- Favicons in `frontend/public/`: `favicon.svg` (the existing Logo mark, light/dark aware),
  `favicon-32.png` and `apple-touch-icon.png` (180 px), rendered from the same mark.
  **TODO(Brann):** replace with the final brand icon (keep the file names).

Static files win over the SPA fallback in `public/_redirects`, so `/robots.txt` and
`/sitemap.xml` are served as files on Cloudflare Pages.

## Check after a build
```bash
cd frontend
REACT_APP_WAITLIST_ONLY=true REACT_APP_SITE_URL=https://<your-domain> yarn build
cat build/robots.txt build/sitemap.xml
grep -o '<meta[^>]*og:[^>]*>' build/index.html
```
