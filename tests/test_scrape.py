"""Fixture-based tests for scripts/scrape.py (stdlib unittest, no deps).

Run with:  python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import scrape  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #

NITTER_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:media="http://search.yahoo.com/mrss/"
     xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel>
    <title>elonmusk / @elonmusk</title>
    <item>
      <title>Hello world</title>
      <description>&lt;p&gt;Hello &lt;b&gt;world&lt;/b&gt;&lt;/p&gt;</description>
      <link>https://nitter.net/elonmusk/status/123#m</link>
      <pubDate>Tue, 27 May 2025 10:00:00 GMT</pubDate>
      <media:content url="https://pbs.twimg.com/media/abc.jpg" type="image/jpeg" />
    </item>
    <item>
      <title>Second post</title>
      <description>Just text here</description>
      <link>https://nitter.net/elonmusk/status/124#m</link>
      <pubDate>Tue, 27 May 2025 09:00:00 GMT</pubDate>
    </item>
  </channel>
</rss>"""

EMPTY_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>nobody</title></channel></rss>"""

# Note the *different* nesting from a "normal" payload to exercise the
# structural (recursive) tweet finder rather than a fixed path.
SYNDICATION_HTML = """<!DOCTYPE html><html><body>
<script id="__NEXT_DATA__" type="application/json">
{"props":{"pageProps":{"timeline":{"entries":[
  {"content":{"tweet":{
     "id_str":"555","full_text":"First syndicated tweet",
     "created_at":"Wed May 28 12:00:00 +0000 2025",
     "mediaDetails":[{"media_url_https":"https://pbs.twimg.com/media/xyz.jpg"}]}}},
  {"wrapper":{"deeper":{"item":{"tweet":{
     "id":"556","full_text":"Second, nested differently",
     "created_at":"Wed May 28 11:00:00 +0000 2025"}}}}}
]}}}}
</script></body></html>"""

NO_DATA_HTML = "<html><body><p>no next data here</p></body></html>"


# --------------------------------------------------------------------------- #
# Nitter RSS parsing
# --------------------------------------------------------------------------- #

class TestNitterRss(unittest.TestCase):
    def test_parses_items_and_strips_html(self):
        items = scrape.parse_rss(NITTER_RSS, "elonmusk")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["text"], "Hello world")

    def test_normalises_link_to_x_com(self):
        items = scrape.parse_rss(NITTER_RSS, "elonmusk")
        self.assertEqual(items[0]["url"], "https://x.com/elonmusk/status/123")

    def test_extracts_media_when_present(self):
        items = scrape.parse_rss(NITTER_RSS, "elonmusk")
        self.assertEqual(items[0]["media"], "https://pbs.twimg.com/media/abc.jpg")
        self.assertNotIn("media", items[1])

    def test_parses_rfc2822_date_to_iso_utc(self):
        items = scrape.parse_rss(NITTER_RSS, "elonmusk")
        self.assertTrue(items[0]["date"].startswith("2025-05-27T10:00:00"))

    def test_empty_feed_returns_empty_list(self):
        self.assertEqual(scrape.parse_rss(EMPTY_RSS, "elonmusk"), [])

    def test_malformed_xml_raises_valueerror(self):
        with self.assertRaises(ValueError):
            scrape.parse_rss("<rss><channel><item> oops", "elonmusk")


# --------------------------------------------------------------------------- #
# Syndication parsing
# --------------------------------------------------------------------------- #

class TestSyndication(unittest.TestCase):
    def test_finds_tweets_regardless_of_nesting(self):
        items = scrape.parse_syndication(SYNDICATION_HTML, "elonmusk")
        texts = {it["text"] for it in items}
        self.assertEqual(
            texts, {"First syndicated tweet", "Second, nested differently"}
        )

    def test_builds_status_url_and_media(self):
        items = scrape.parse_syndication(SYNDICATION_HTML, "elonmusk")
        by_text = {it["text"]: it for it in items}
        first = by_text["First syndicated tweet"]
        self.assertEqual(first["url"], "https://x.com/elonmusk/status/555")
        self.assertEqual(first["media"], "https://pbs.twimg.com/media/xyz.jpg")

    def test_parses_twitter_created_at(self):
        items = scrape.parse_syndication(SYNDICATION_HTML, "elonmusk")
        by_text = {it["text"]: it for it in items}
        self.assertTrue(
            by_text["First syndicated tweet"]["date"].startswith("2025-05-28T12:00:00")
        )

    def test_missing_next_data_raises(self):
        with self.assertRaises(ValueError):
            scrape.parse_syndication(NO_DATA_HTML, "elonmusk")


# --------------------------------------------------------------------------- #
# Dedupe + date helpers
# --------------------------------------------------------------------------- #

class TestHelpers(unittest.TestCase):
    def test_dedupe_removes_duplicate_urls(self):
        items = [
            {"url": "https://x.com/a/status/1", "text": "one"},
            {"url": "https://x.com/a/status/1", "text": "one (dup)"},
            {"url": "https://x.com/a/status/2", "text": "two"},
        ]
        out = scrape.dedupe(items)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0]["text"], "one")

    def test_dedupe_empty(self):
        self.assertEqual(scrape.dedupe([]), [])

    def test_to_iso_handles_epoch_and_blank(self):
        self.assertTrue(scrape.to_iso(0).startswith("1970-01-01"))
        self.assertEqual(scrape.to_iso(""), "")
        self.assertEqual(scrape.to_iso(None), "")


if __name__ == "__main__":
    unittest.main()
