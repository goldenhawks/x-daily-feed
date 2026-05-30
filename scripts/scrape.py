#!/usr/bin/env python3
"""Scrape recent tweets for one or more target X (Twitter) accounts.

Strategy (no login required), tried per account in order:
  1. X's public syndication timeline endpoint:
       https://syndication.twitter.com/srv/timeline-profile/screen-name/<user>
  2. A configurable list of Nitter instances (RSS feeds).

Multiple accounts (comma-separated in X_ACCOUNT) are fetched and merged into a
single, reverse-chronological timeline. Each item is tagged with its `author`
and the `source` it came from.

Output is written to data/feed.json with the shape:
  {
    "account": "elonmusk",                 # first account (kept for compat)
    "accounts": ["elonmusk", "paulg"],     # everyone tracked
    "updated_at": "2026-05-30T12:00:00+00:00",
    "last_success_at": "2026-05-30T12:00:00+00:00",
    "source": "syndication",               # or "nitter" / "mixed"
    "error_message": "",                   # non-empty if some accounts failed
    "items": [
      { "author": "...", "date": "...", "text": "...", "url": "...",
        "source": "...", "media": "..." }, ...
    ]
  }

Graceful degradation: if *every* account fails, the previous data/feed.json is
left untouched (never overwritten with an empty file) and the script exits with
a non-zero status so CI goes red. If only *some* accounts fail, the file is
written with whatever succeeded and the failures are recorded in
`error_message`.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# --------------------------------------------------------------------------- #
# Configuration (overridable via environment variables)
# --------------------------------------------------------------------------- #

# Comma-separated list of handles. Change this default, or (recommended) set the
# X_ACCOUNT repository variable in Settings -> Secrets and variables -> Actions.
DEFAULT_ACCOUNTS = "elonmusk,paulg,sama,naval,karpathy"

ACCOUNTS = [
    a.strip().lstrip("@")
    for a in os.environ.get("X_ACCOUNT", DEFAULT_ACCOUNTS).split(",")
    if a.strip()
]

MAX_ITEMS = int(os.environ.get("X_MAX_ITEMS", "40"))

# Comma-separated list of Nitter base URLs used as a fallback.
NITTER_INSTANCES = [
    inst.strip().rstrip("/")
    for inst in os.environ.get(
        "NITTER_INSTANCES",
        "https://nitter.net,https://nitter.poast.org,https://nitter.privacydev.net",
    ).split(",")
    if inst.strip()
]

REPO_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_PATH = REPO_ROOT / "data" / "feed.json"

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
TIMEOUT = 25

# Media namespace used by RSS feeds for <media:content>.
RSS_NS = {
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def log(msg: str) -> None:
    print(f"[scrape] {msg}", file=sys.stderr)


def http_get(url: str, accept: str = "*/*") -> str:
    req = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": accept,
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with urlopen(req, timeout=TIMEOUT) as resp:
        charset = resp.headers.get_content_charset() or "utf-8"
        return resp.read().decode(charset, errors="replace")


def to_iso(value) -> str:
    """Best-effort conversion of a date-ish value to an ISO 8601 string (UTC)."""
    if value is None:
        return ""
    if isinstance(value, dt.datetime):
        d = value
    elif isinstance(value, (int, float)):
        d = dt.datetime.fromtimestamp(value, tz=dt.timezone.utc)
    else:
        s = str(value).strip()
        if not s:
            return ""
        d = None
        # RFC 2822 (RSS pubDate, e.g. "Tue, 27 May 2025 10:00:00 GMT").
        try:
            d = parsedate_to_datetime(s)
        except (TypeError, ValueError, IndexError):
            d = None
        # Twitter syndication created_at (e.g. "Wed May 28 12:00:00 +0000 2025").
        if d is None:
            try:
                d = dt.datetime.strptime(s, "%a %b %d %H:%M:%S %z %Y")
            except ValueError:
                d = None
        # ISO 8601.
        if d is None:
            try:
                d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError:
                return s  # give up, return as-is
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return d.astimezone(dt.timezone.utc).isoformat()


def clean_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text or "")
    text = html.unescape(text)
    return text.strip()


def dedupe(items: list[dict]) -> list[dict]:
    seen = set()
    out = []
    for it in items:
        key = it.get("url") or it.get("text")
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


# --------------------------------------------------------------------------- #
# Source 1: X syndication timeline
# --------------------------------------------------------------------------- #

def _walk_find_tweets(node, found: list[dict]) -> None:
    """Recursively collect dict nodes that look like tweet entries.

    Done structurally (rather than by a fixed path) so the parser keeps working
    if the syndication payload's nesting changes.
    """
    if isinstance(node, dict):
        text = node.get("full_text") or node.get("text")
        has_id = node.get("id_str") or node.get("id") or node.get("conversation_id_str")
        if text and has_id:
            found.append(node)
        for value in node.values():
            _walk_find_tweets(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_find_tweets(value, found)


def parse_syndication(body: str, account: str) -> list[dict]:
    """Parse the HTML body of the syndication endpoint into feed items."""
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        body,
        re.DOTALL,
    )
    if not match:
        raise ValueError("could not locate __NEXT_DATA__ payload")

    payload = json.loads(match.group(1))
    raw_tweets: list[dict] = []
    _walk_find_tweets(payload, raw_tweets)
    if not raw_tweets:
        raise ValueError("no tweets found in syndication payload")

    items = []
    for tw in raw_tweets:
        text = clean_text(tw.get("full_text") or tw.get("text") or "")
        if not text:
            continue
        tweet_id = tw.get("id_str") or tw.get("id") or ""
        created = tw.get("created_at")
        url_out = (
            f"https://x.com/{account}/status/{tweet_id}"
            if tweet_id
            else f"https://x.com/{account}"
        )
        item = {
            "date": to_iso(created),
            "text": text,
            "url": url_out,
        }
        media = tw.get("mediaDetails") or tw.get("media") or []
        if isinstance(media, list) and media:
            thumb = media[0].get("media_url_https") or media[0].get("media_url")
            if thumb:
                item["media"] = thumb
        items.append(item)

    return dedupe(items)


def fetch_syndication(account: str) -> list[dict]:
    url = (
        "https://syndication.twitter.com/srv/timeline-profile/screen-name/"
        + account
    )
    log(f"[{account}] trying syndication endpoint")
    body = http_get(url, accept="text/html,application/json")
    return parse_syndication(body, account)


# --------------------------------------------------------------------------- #
# Source 2: Nitter RSS fallback
# --------------------------------------------------------------------------- #

def parse_rss(body: str, account: str) -> list[dict]:
    """Parse an RSS feed body into feed items using the stdlib XML parser."""
    try:
        root = ET.fromstring(body.strip())
    except ET.ParseError as exc:
        raise ValueError(f"invalid RSS XML: {exc}") from exc

    items = []
    for item in root.iter("item"):
        title = item.findtext("title") or ""
        description = item.findtext("description") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""

        text = clean_text(description) or clean_text(title)
        if not text:
            continue

        # Normalise nitter links back to x.com.
        link = re.sub(r"https?://[^/]*nitter[^/]*", "https://x.com", link).replace("#m", "")
        if not link:
            link = f"https://x.com/{account}"

        out = {
            "date": to_iso(pub),
            "text": text,
            "url": link,
        }
        media = item.find("media:content", RSS_NS)
        if media is not None and media.get("url"):
            out["media"] = media.get("url")
        items.append(out)

    return dedupe(items)


def fetch_nitter(account: str) -> list[dict]:
    last_error = None
    for base in NITTER_INSTANCES:
        feed_url = f"{base}/{account}/rss"
        try:
            log(f"[{account}] trying nitter rss: {base}")
            body = http_get(
                feed_url, accept="application/rss+xml,application/xml,text/xml"
            )
            items = parse_rss(body, account)
            if items:
                return items
            log(f"[{account}] nitter instance returned no items: {base}")
        except (HTTPError, URLError, ValueError, TimeoutError) as exc:
            last_error = exc
            log(f"[{account}] nitter instance failed ({base}): {exc}")
            continue
    if last_error:
        raise last_error
    raise ValueError("all nitter instances returned no items")


# --------------------------------------------------------------------------- #
# Orchestration
# --------------------------------------------------------------------------- #

def collect(account: str) -> tuple[list[dict], str | None, str | None]:
    """Return (items, source_name, error). Tries each source in turn."""
    errors = []
    for name, fn in (("syndication", fetch_syndication), ("nitter", fetch_nitter)):
        try:
            items = fn(account)
            if items:
                log(f"[{account}] source '{name}' returned {len(items)} items")
                return items, name, None
            errors.append(f"{name}: no items")
        except Exception as exc:  # noqa: BLE001 - want to try every source
            errors.append(f"{name}: {exc}")
            log(f"[{account}] source '{name}' failed: {exc}")
    return [], None, "; ".join(errors)


def build_feed() -> dict | None:
    """Fetch every account and assemble the feed dict, or None on total failure."""
    all_items: list[dict] = []
    sources: set[str] = set()
    errors: dict[str, str] = {}

    for account in ACCOUNTS:
        items, source, error = collect(account)
        if items and source:
            sources.add(source)
            for it in items:
                it["author"] = account
                it["source"] = source
            all_items.extend(items)
        else:
            errors[account] = error or "unknown error"

    if not all_items:
        return None  # total failure -> caller preserves previous file

    all_items = dedupe(all_items)
    all_items.sort(key=lambda it: it.get("date") or "", reverse=True)
    all_items = all_items[:MAX_ITEMS]

    if len(sources) > 1:
        source_label = "mixed"
    elif sources:
        source_label = next(iter(sources))
    else:
        source_label = ""

    error_message = "; ".join(f"@{a}: {e}" for a, e in errors.items())
    now = dt.datetime.now(tz=dt.timezone.utc).isoformat()

    return {
        "account": ACCOUNTS[0] if ACCOUNTS else "",
        "accounts": ACCOUNTS,
        "updated_at": now,
        "last_success_at": now,
        "source": source_label,
        "error_message": error_message,
        "items": all_items,
    }


def main() -> int:
    log(f"accounts={ACCOUNTS} max_items={MAX_ITEMS}")
    feed = build_feed()

    if feed is None:
        log("FAILED to fetch any data from any account")
        if OUTPUT_PATH.exists():
            log("keeping previous data/feed.json (graceful degradation)")
        else:
            log("no previous data/feed.json exists to preserve")
        return 1

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(feed, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(f"wrote {len(feed['items'])} items to {OUTPUT_PATH.relative_to(REPO_ROOT)}")
    if feed["error_message"]:
        log(f"partial failures: {feed['error_message']}")
    return 0


if __name__ == "__main__":
    # Small retry buffer for transient network hiccups before giving up.
    attempts = int(os.environ.get("X_ATTEMPTS", "2"))
    code = 1
    for attempt in range(1, attempts + 1):
        code = main()
        if code == 0:
            break
        if attempt < attempts:
            wait = 5 * attempt
            log(f"retrying in {wait}s ({attempt}/{attempts})")
            time.sleep(wait)
    sys.exit(code)
