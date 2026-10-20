import os
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

class DiamondTests(unittest.TestCase):
    def test_feed_and_discount(self):
        item = bot.parse_feed(RSS)[0]
        self.assertEqual(item["key"], "deal-100")
        self.assertEqual(bot.percent(item["title"], item["description"]), 70)
        self.assertTrue(bot.relevant(item["title"], item["description"]))

    def test_direct_amazon_fr_only(self):
        self.assertEqual(
            bot.find_amazon_link("https://www.amazon.fr/dp/B0B9C4DKKG?tag=demo"),
            "https://www.amazon.fr/dp/B0B9C4DKKG",
        )
        self.assertIsNone(bot.find_amazon_link("https://www.amazon.de/dp/B0B9C4DKKG"))
        self.assertIsNone(bot.find_amazon_link("https://evilamazon.fr/dp/B0B9C4DKKG"))

    def test_excludes_unrelated_or_used(self):
        self.assertFalse(bot.relevant("Robe pas cher -70%", "fashion"))
        self.assertFalse(bot.relevant("SSD Samsung -70%", "reconditionné"))

    def test_dry_run_no_duplicates(self):
        with tempfile.TemporaryDirectory() as temp:
            with patch.object(bot, "STATE_FILE", Path(temp) / "seen.json"), \
                 patch.object(bot, "fetch", return_value=RSS), \
                 patch.object(bot, "DRY_RUN", True):
                self.assertEqual(bot.run(), 1)
                self.assertEqual(bot.run(), 0)

if __name__ == "__main__":
    unittest.main()
