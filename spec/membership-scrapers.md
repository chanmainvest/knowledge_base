# Spec — Membership scrapers (Patreon, Substack)

Read this when touching `src/kb/scrapers/patreon.py`, `substack.py`, or the
Patreon browser daemon (`src/kb/scrapers/patreon_daemon.py`).

## Patreon

- `kb patreon scrape-creator` prefers the local browser daemon (headed
  Chromium, host-only) to refresh the session cookie, but inside Docker —
  where a headed browser cannot launch — it skips the daemon entirely and
  authenticates with the shared `data/patreon/.session.json` instead. Prime
  that file on the host via `kb patreon prime-session` /
  `kb patreon browser login`; exit 2 means the saved cookie itself expired.
- **Patreon API calls go through curl_cffi's Chrome TLS impersonation**
  (`PatreonScraper.http()`): Patreon's Cloudflare fingerprints TLS, and
  plain httpx from the Linux container's OpenSSL draws
  `403 cf-mitigated: challenge` on every URL (the Windows build passes), so
  the Jenkins stage could never reach patreon.com without it. curl_cffi
  falls back to plain httpx when not importable.

## Substack notes

- Handles (e.g. `michaelwgreen` from `https://substack.com/@michaelwgreen`) are
  resolved to a publication `subdomain` once via the public
  `https://substack.com/api/v1/user/<handle>/public_profile` endpoint and cached
  in `channel.metadata`, mirroring how `patreon.py` caches `campaign_id`. Discovery
  (`.../api/v1/archive`) and post bodies (`.../api/v1/posts/<slug>`) come from
  that publication's own public API — no login needed for free posts.
- Some publications force a *custom domain* (`custom_domain_optional: false`,
  e.g. `michaelwgreen` → `www.yesigiveafig.com`); Substack 301-redirects every
  `.substack.com` request for these, and `httpx` follows the redirect
  transparently. Others (`custom_domain_optional: true`, or no custom domain)
  serve directly from `<subdomain>.substack.com`.
- Substack's `substack.sid` auth cookie is scoped to `.substack.com` and does
  **not** carry over to a custom domain for a plain HTTP client. Paid
  (`audience != "everyone"`) posts whose API body looks truncated relative to
  the post's own `wordcount` are re-fetched with a headless, cookie-injected
  Playwright browser navigating straight to the post's `canonical_url` — the
  same cross-domain auth-sync a real logged-in browser performs for a human
  reader.
- Get a session with `kb substack prime-session` (opens a real browser window
  to log in manually, saves `substack.sid` to `data/substack/.session.json`),
  or set `SUBSTACK_SESSION_COOKIE` / `SUBSTACK_COOKIES_FROM_BROWSER` in `.env`.
  `kb substack check-session` validates it; `kb substack resolve <handle>`
  resolves a handle without needing a session.
