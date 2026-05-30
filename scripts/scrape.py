#!/usr/bin/env python3
"""Scrape recent tweets for a target X (Twitter) account.

Strategy (no login required):
  1. Try X's public syndication timeline endpoint:
       https://syndication.twitter.com/srv/timeline-profile/screen-name/<user>
  2. If that fails, fall back to a configurable list of Nitter instances
     (RSS feeds).

Output is written to data/feed.json with the shape:
  {
    "account": "elonmusk",
    "updated_at": "2026-05-30T08:00:00+00:00",
    "items": [ { "date": "...", "text": "...", "url": "..." }, ... ]
  }

Graceful degradation: if every source fails, the previous data/feed.json is
left untouched (never overwritten with an empty file) and the script exits
with a non-zero status so CI goes red.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import time
import datetime as dt
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# --------------------------------------------------------------------------- #
# Configuration (overridable via environment variables)
# --------------------------------------------------------------------------- #

ACCOUNT = os.environ.get("X_ACCOUNT", "elonmusk").lstrip("@")
MAX_ITEMS = int(os.environ.get("X_MAX_ITEMS", "30"))

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
        d = None
        # Try RFC 2822 (used by RSS), then ISO 8601.
        try:
            d = parsedate_to_datetime(s)
        except (TypeError, ValueError):
            d = None
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
    """Recursively collect dict nodes that look like tweet entries."""
    if isinstance(node, dict):
        # A tweet object usually has full_text/text plus an id and created_at.
        text = node.get("full_text") or node.get("text")
        has_id = node.get("id_str") or node.get("id") or node.get("conversation_id_str")
        if text and has_id:
            found.append(node)
        for value in node.values():
            _walk_find_tweets(value, found)
    elif isinstance(node, list):
        for value in node:
            _walk_find_tweets(value, found)


def fetch_syndication(account: str) -> list[dict]:
    url = (
        "https://syndication.twitter.com/srv/timeline-profile/screen-name/"
        + account
    )
    log(f"trying syndication endpoint: {url}")
    body = http_get(url, accept="text/html,application/json")

    # The page embeds a __NEXT_DATA__ JSON blob with the timeline entries.
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
        # Optional media thumbnail.
        media = (tw.get("mediaDetails") or tw.get("media") or [])
        if isinstance(media, list) and media:
            thumb = media[0].get("media_url_https") or media[0].get("media_url")
            if thumb:
                item["media"] = thumb
        items.append(item)

    return dedupe(items)


# --------------------------------------------------------------------------- #
# Source 2: Nitter RSS fallback
# --------------------------------------------------------------------------- #

def fetch_nitter(account: str) -> list[dict]:
    last_error = None
    for base in NITTER_INSTANCES:
        feed_url = f"{base}/{account}/rss"
        try:
            log(f"trying nitter rss: {feed_url}")
            body = http_get(feed_url, accept="application/rss+xml,application/xml,text/xml")
            items = parse_rss(body, account)
            if items:
                return items
            log(f"nitter instance returned no items: {base}")
        except (HTTPError, URLError, ValueError, TimeoutError) as exc:
            last_error = exc
            log(f"nitter instance failed ({base}): {exc}")
            continue
    if last_error:
        raise last_error
    raise ValueError("all nitter instances returned no items")


def parse_rss(body: str, account: str) -> list[dict]:
    items = []
    for block in re.findall(r"<item>(.*?)</item>", body, re.DOTALL):
        title = _xml_tag(block, "title")
        description = _xml_tag(block, "description")
        link = _xml_tag(block, "link")
        pub = _xml_tag(block, "pubDate")

        text = clean_text(description) or clean_text(title)
        if not text:
            continue

        # Normalise nitter links back to x.com.
        link = re.sub(r"https?://[^/]*nitter[^/]*", "https://x.com", link or "")
        link = link.replace("#m", "")
        if not link:
            link = f"https://x.com/{account}"

        item = {
            "date": to_iso(pub),
            "text": text,
            "url": link,
        }
        media = re.search(r'<media:content[^>]*url="([^"]+)"', block)
        if media:
            item["media"] = media.group(1)
        items.append(item)

    return dedupe(items)


def _xml_tag(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.DOTALL)
    if not m:
        return ""
    value = m.group(1).strip()
    cdata = re.match(r"<!\[CDATA\[(.*?)\]\]>", value, re.DOTALL)
    if cdata:
        value = cdata.group(1)
    return value


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #

def collect(account: str) -> list[dict]:
    errors = []
    for name, fn in (("syndication", fetch_syndication), ("nitter", fetch_nitter)):
        try:
            items = fn(account)
            if items:
                log(f"source '{name}' returned {len(items)} items")
                return items
            errors.append(f"{name}: no items")
        except Exception as exc:  # noqa: BLE001 - want to try every source
            errors.append(f"{name}: {exc}")
            log(f"source '{name}' failed: {exc}")
    raise RuntimeError("all sources failed -> " + " | ".join(errors))


def main() -> int:
    log(f"account=@{ACCOUNT} max_items={MAX_ITEMS}")
    try:
        items = collect(ACCOUNT)
    except Exception as exc:  # noqa: BLE001
        log(f"FAILED to fetch any data: {exc}")
        if OUTPUT_PATH.exists():
            log("keeping previous data/feed.json (graceful degradation)")
        else:
            log("no previous data/feed.json exists to preserve")
        return 1

    # Sort newest first when dates are parseable, then trim.
    items.sort(key=lambda it: it.get("date") or "", reverse=True)
    items = items[:MAX_ITEMS]

    output = {
        "account": ACCOUNT,
        "updated_at": dt.datetime.now(tz=dt.timezone.utc).isoformat(),
        "items": items,
    }

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    log(f"wrote {len(items)} items to {OUTPUT_PATH.relative_to(REPO_ROOT)}")
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
