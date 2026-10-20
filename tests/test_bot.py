import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot

RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><item>
<title>Samsung SSD 990 PRO 2 To -70%</title>
<link>https://www.dealabs.com/bons-plans/ssd</link>
<guid>deal-100</guid>
<description><![CDATA[SSD NVMe -70%. https://www.amazon.fr/dp/B0B9C4DKKG?tag=demo]]></description>
</item></channel></rss>
"""


def post(title, link, description, key="test-1", pub_date=""):
    return {
        "title": title, "link": link, "description": description,
        "content": "", "key": key, "pubDate": pub_date,
    }


class DiamondTests(unittest.TestCase):
    def test_feed_and_discount(self):
        item = bot.parse_feed(RSS)[0]
        self.assertEqual(item["key"], "deal-100")
        self.assertEqual(bot.percent(item["title"], item["description"]), 70)
        self.assertTrue(bot.relevant(item["title"], item["description"]))

    def test_direct_amazon_all_three_marketplaces(self):
        for suffix in ("fr", "es", "it"):
            source = "https://www.amazon." + suffix + "/dp/B0B9C4DKKG?tag=demo"
            self.assertEqual(bot.find_amazon_link(source),
                             "https://amazon." + suffix + "/dp/B0B9C4DKKG")
        self.assertIsNone(bot.find_amazon_link("https://www.amazon.de/dp/B0B9C4DKKG"))
        self.assertIsNone(bot.find_amazon_link("https://evilamazon.fr/dp/B0B9C4DKKG"))
        self.assertIsNone(bot.find_amazon_link("https://amazon.fr.evil.com/dp/B0B9C4DKKG"))

    def test_brand_priority_and_categories(self):
        self.assertTrue(bot.PREFERRED_BRANDS.search("Apple iPad"))
        self.assertTrue(bot.relevant("Dyson aspirateur -65%", "aspirateur robot"))
        self.assertTrue(bot.relevant("Dyson aspirapolvere -65%", "casa"))
        self.assertTrue(bot.relevant("Sony auriculares -65%", "audio"))
        self.assertTrue(bot.relevant("Nintendo videogiochi -65%", "giochi"))
        self.assertFalse(bot.relevant("Robe pas cher -70%", "fashion"))

    def test_excludes_used_in_three_languages(self):
        for name in ("reconditionné", "reacondicionado", "segunda mano",
                     "ricondizionato", "usato", "occasion"):
            self.assertFalse(bot.relevant("Samsung SSD -70%", name))

    def test_only_trusted_source_pages(self):
        for url in ("https://www.dealabs.com/bons-plans/foo",
                    "https://www.chollometro.com/ofertas/foo",
                    "https://quantospendo.app/offerte/foo"):
            self.assertTrue(bot.dealabs_link(url), url)
        self.assertFalse(bot.dealabs_link("https://evil-chollometro.com/ofertas/foo"))
        self.assertFalse(bot.dealabs_link("https://www.dealabs.com.evil.com/foo"))

    def test_percentage_below_threshold(self):
        self.assertEqual(bot.percent("Samsung -72%", ""), 72)
        self.assertIsNone(bot.percent("Samsung -40%", ""))
        self.assertIsNone(bot.percent("Samsung jusqu'à -70%", ""))

    def test_crossborder_candidate_with_disclosed_market(self):
        item = post("Samsung smartphone -70%",
                    "https://www.chollometro.com/ofertas/samsung",
                    "https://www.amazon.es/dp/B0B9C4DKKG")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            candidates = bot.candidates([item])
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["market"], "amazon.es")
        self.assertTrue(candidates[0]["amazon"].startswith("https://amazon.es/dp/"))

    def test_italian_home_appliances(self):
        item = post("Dyson aspirapolvere -70%",
                    "https://quantospendo.app/offerte/offerta1",
                    "https://www.amazon.it/dp/B0B9C4DKKG")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([item])
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["market"], "amazon.it")

    def test_exceptional_any_category_70_percent_with_savings(self):
        item = post("Cool toy set -80%",
                    "https://www.dealabs.com/bons-plans/toy",
                    "40 € au lieu de 200 € https://amazon.fr/dp/B0B9C4DKKG")
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected fetch")):
            found = bot.candidates([item])
        self.assertEqual(len(found), 1)

    def test_dry_run_deduplicates_and_tracks_state(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bot, "STATE_FILE", Path(temp) / "seen.json"), \
                 patch.object(bot, "fetch", return_value=RSS), \
                 patch.object(bot, "DRY_RUN", True):
                self.assertEqual(bot.run(), 1)
                self.assertEqual(bot.run(), 0)
                seen = bot.get_seen()
                self.assertIn("__metadata__", seen)
                self.assertEqual(seen["__metadata__"]["daily_stats"][
                    next(iter(seen["__metadata__"]["daily_stats"]))]["scans"], 2)

    def test_configuration(self):
        self.assertEqual(tuple(bot.MARKETS),
                         ("amazon.fr", "amazon.es", "amazon.it"))
        self.assertEqual(bot.REPORT_HOUR, 21)


if __name__ == "__main__":
    unittest.main()
