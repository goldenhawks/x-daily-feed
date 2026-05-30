# X Daily Feed

A tiny, zero-backend project that grabs recent posts from one or more X
(Twitter) accounts **once a day** and shows them on a static, mobile-friendly,
dark-themed page hosted on GitHub Pages.

- **Scraper:** `scripts/scrape.py` (Python standard library only)
- **Tests:** `tests/test_scrape.py` (stdlib `unittest`, fixture-based, no network)
- **Data:** `data/feed.json`
- **Page:** `index.html` + `style.css` + `app.js` (vanilla JS, no frameworks)
- **Automation:** GitHub Actions (daily cron + Pages deploy + CI)

## How it works

1. A scheduled GitHub Actions workflow runs every day at **12:00 UTC**
   (~08:00 in Toronto during EDT). Adjust the `cron` in
   `.github/workflows/daily.yml` for your timezone.
2. `scripts/scrape.py` fetches the latest tweets for **each** configured
   account:
   - First it tries X's public **syndication** endpoint
     (`https://syndication.twitter.com/srv/timeline-profile/screen-name/<user>`),
     which needs no login.
   - If that fails, it falls back to a configurable list of **Nitter** RSS
     instances (parsed with the stdlib XML parser).
3. All accounts are merged into one reverse-chronological timeline and written
   to `data/feed.json`, along with reliability metadata (`source`,
   `last_success_at`, `error_message`). If **every** account fails, the
   previous `feed.json` is kept (never overwritten with an empty file) and the
   job exits non-zero so the run shows up red.
4. If `data/feed.json` changed, the workflow commits it back to `main`.
5. A separate workflow deploys **only the static files** (`index.html`,
   `style.css`, `app.js`, `data/feed.json`) to **GitHub Pages** on every push
   to `main`. The page `fetch()`es `./data/feed.json` and renders cards. If the
   newest post is more than 2 days old, it shows a "data may be out of date"
   banner so a silently-failing scraper doesn't masquerade as fresh.

## Tracking accounts (this is the main thing to configure)

The default tracks a handful of public accounts
(`elonmusk,paulg,sama,naval,karpathy`). Change it to whoever you want:

- **Repository variable (recommended):** Settings → Secrets and variables →
  Actions → **Variables** → add `X_ACCOUNT`, a **comma-separated** list of
  handles (no `@`), e.g. `paulg,sama,patio11`. The daily workflow reads
  `vars.X_ACCOUNT`.
- **Edit the defaults:** change the `X_ACCOUNT` fallback in
  `.github/workflows/daily.yml` and the `DEFAULT_ACCOUNTS` constant in
  `scripts/scrape.py`.

> **Why not "everyone I follow"?** This project deliberately uses only
> no-login public endpoints. An account's *following list / home timeline*
> requires authentication, so it can't be read this way — you instead curate
> the list of public handles above.

Other tunables (env vars read by the scraper):

| Variable           | Default                              | Purpose                                  |
| ------------------ | ------------------------------------ | ---------------------------------------- |
| `X_ACCOUNT`        | `elonmusk,paulg,sama,naval,karpathy` | Comma-separated handles (no `@`).        |
| `X_MAX_ITEMS`      | `40`                                 | Max posts kept in `feed.json`.           |
| `NITTER_INSTANCES` | a few public instances               | Comma-separated Nitter fallbacks.        |
| `X_ATTEMPTS`       | `2`                                  | Retries before giving up.                |

## Running the scraper manually

### Trigger the workflow from GitHub

Actions → **Daily feed update** → **Run workflow** (the `workflow_dispatch`
button). This fetches fresh data and commits it, which in turn redeploys Pages.

### Run locally

```bash
# No third-party dependencies required.
X_ACCOUNT=paulg,sama python scripts/scrape.py
# then serve the static site and open it:
python -m http.server 8000   # visit http://localhost:8000
```

### Run the tests

```bash
python -m unittest discover -s tests -v
```

The fixtures cover syndication payload parsing (including changed nesting),
Nitter RSS parsing, media extraction, date normalisation, empty feeds,
malformed XML, and dedupe.

## Where's the page?

Once Pages is enabled, the site is published at:

```
https://goldenhawks.github.io/x-daily-feed/
```

> **One-time setup:** go to **Settings → Pages** and set **Source** to
> **"GitHub Actions"**. After that, every push to `main` redeploys the site.

## Notes & limitations

- These public endpoints are unofficial and can rate-limit or change their
  structure without notice; the Nitter fallback, graceful degradation, and the
  on-page staleness banner all exist for exactly that reason. Expect occasional
  red runs.
- Nothing here uses the paid X API or any login/cookies.
