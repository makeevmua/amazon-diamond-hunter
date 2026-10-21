"""Regression tests for bounded, multilingual public feed discovery."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import catalog
import signals


class FeedDiscoveryTests(unittest.TestCase):
    def test_custom_feeds_allow_only_selected_european_publishers(self):
        additions = (
            "https://www.dealabs.com/rss/groupe/maison\\n"
            "https://www.chollometro.com/rss/nouveaux;"
            "https://www.dealabs.com/rss/groupe/maison,"
            "https://evil.example/rss,"
            "https://www.hotukdeals.com/rss,"
            "http://www.dealabs.com/rss,"
            "https://www.dealabs.com:9443/rss"
        )
        # Use an actual newline rather than a literal slash-n.
        additions = additions.replace("\\n", "\n")
        with patch.object(bot, "RSS_URL", None), patch.dict(
                os.environ, {"EXTRA_RSS_FEEDS": additions}):
            feeds = bot.configured_feeds()
        self.assertEqual(len(feeds), len(bot.FEEDS) + 2)
        self.assertEqual(feeds[-2:], [
            "https://www.dealabs.com/rss/groupe/maison",
            "https://www.chollometro.com/rss/nouveaux"
        ])
        self.assertFalse(any("hotukdeals" in url for url in feeds))

    def test_debug_override_kept(self):
        with patch.object(bot, "RSS_URL", "https://test.example/local.xml"):
            self.assertEqual(bot.configured_feeds(), [
                "https://test.example/local.xml"
            ])

    def test_parallel_scanner_handles_one_bad_feed_and_duplicate_articles(self):
        feed_a = """<rss><channel><item>
          <title>Lampe Bauhaus -30%</title>
          <link>https://www.dealabs.com/bons-plans/lamp</link>
          <guid>id-one</guid>
        </item></channel></rss>"""
        feed_b = """<rss><channel><item>
          <title>Lampe Bauhaus -30%</title>
          <link>https://www.dealabs.com/bons-plans/lamp</link>
          <guid>other-id-for-same-link</guid>
        </item></channel></rss>"""
        urls = [
            "https://www.dealabs.com/rss/a",
            "https://www.chollometro.com/rss/b",
            "https://www.dealabs.com/rss/bad",
        ]
        payloads = {urls[0]: feed_a, urls[1]: feed_b}

        def stub(url, timeout=15):
            if url not in payloads:
                raise URLError("unavailable")
            return payloads[url]

        with patch.object(bot, "FEEDS", urls), patch.object(bot, "RSS_URL", None), patch.dict(
                os.environ, {"EXTRA_RSS_FEEDS": ""}), patch.object(bot, "fetch", side_effect=stub):
            posts, working, scanned = bot.collect()
        self.assertEqual((working, scanned), (2, 3))
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0]["title"], "Lampe Bauhaus -30%")

    def test_direct_amazon_url_is_listed_as_such_without_alert_threshold(self):
        post = {
            "title": "Lampe Bauhaus 29 €", "description":
            "https://amazon.fr/dp/B0B9C4DKKG", "pubDate": "", "content": "",
            "link": "https://www.dealabs.com/bons-plans/lamp", "key": "id-123",
        }
        rows = catalog.collect_records(
            [post], [], bot.category_matches, bot.percent, bot.price_pair,
            signals.headline_price,
            lambda text: bool(bot.EXCLUDE_RE.search(text)),
            bot.fresh_enough, bot.find_amazon_link,
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "ready_for_alert")
        self.assertEqual(rows[0]["advertised_price_eur"], 29)


if __name__ == "__main__":
    unittest.main()
