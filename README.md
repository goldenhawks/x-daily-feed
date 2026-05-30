# X Daily Feed

A tiny, zero-backend project that grabs a target X (Twitter) account's recent
posts **once a day** and shows them on a static, mobile-friendly, dark-themed
page hosted on GitHub Pages.

- **Scraper:** `scripts/scrape.py` (Python standard library only)
- **Data:** `data/feed.json`
- **Page:** `index.html` + `style.css` + `app.js` (vanilla JS, no frameworks)
- **Automation:** GitHub Actions (daily cron + Pages deploy)

## How it works

1. A scheduled GitHub Actions workflow runs every day at **08:00 UTC**.
2. `scripts/scrape.py` fetches the latest tweets:
   - First it tries X's public **syndication** endpoint
     (`https://syndication.twitter.com/srv/timeline-profile/screen-name/<user>`),
     which needs no login.
   - If that fails, it falls back to a configurable list of **Nitter** RSS
     instances.
3. The result is written to `data/feed.json`. If **every** source fails, the
   previous `feed.json` is kept (never overwritten with an empty file) and the
   job exits non-zero so the run shows up red.
4. If `data/feed.json` changed, the workflow commits it back to `main`.
5. A separate workflow deploys the repo root to **GitHub Pages** on every push
   to `main`. The page just `fetch()`es `./data/feed.json` and renders cards.

## Changing the target account

Pick whichever is easiest:

- **Repository variable (recommended):** Settings → Secrets and variables →
  Actions → **Variables** → add `X_ACCOUNT` with the handle (no `@`), e.g.
  `elonmusk`. The daily workflow reads `vars.X_ACCOUNT`.
- **Edit the default:** change the `X_ACCOUNT` fallback in
  `.github/workflows/daily.yml`, and/or the `ACCOUNT` default in
  `scripts/scrape.py`.

Other tunables (env vars read by the scraper):

| Variable           | Default                                   | Purpose                              |
| ------------------ | ----------------------------------------- | ------------------------------------ |
| `X_ACCOUNT`        | `elonmusk`                                 | Handle to scrape (without `@`).      |
| `X_MAX_ITEMS`      | `30`                                       | Max posts kept in `feed.json`.       |
| `NITTER_INSTANCES` | a few public instances                     | Comma-separated Nitter fallbacks.    |
| `X_ATTEMPTS`       | `2`                                        | Retries before giving up.            |

## Running the scraper manually

### Trigger the workflow from GitHub

Actions → **Daily feed update** → **Run workflow** (the `workflow_dispatch`
button).

### Run locally

```bash
# No third-party dependencies required.
X_ACCOUNT=elonmusk python scripts/scrape.py
# then open index.html (e.g. with a static server)
python -m http.server 8000   # visit http://localhost:8000
```

## Where's the page?

Once Pages is enabled, the site is published at:

```
https://goldenhawks.github.io/x-daily-feed/
```

> **One-time setup:** go to **Settings → Pages** and set **Source** to
> **"GitHub Actions"**. After that, every push to `main` redeploys the site.

## Notes & limitations

- These public endpoints are unofficial and can rate-limit or change without
  notice; the Nitter fallback and graceful degradation exist for exactly that
  reason. Expect occasional red runs.
- Nothing here uses the paid X API or any login/cookies.
