"""Germany-only regression checks: legal country scope and correct store links."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import catalog
import alerts
import pricechecks
import policy


class GermanyTests(unittest.TestCase):
    def test_german_store_whitelisted_without_uk_us(self):
        product = "https://www.amazon.de/dp/B0B9C4DKKG?tag=123"
        canonical = "https://amazon.de/dp/B0B9C4DKKG"
        self.assertEqual(bot.find_amazon_link(product), canonical)
        self.assertEqual(pricechecks.identify(canonical), ("amazon.de", "B0B9C4DKKG"))
        self.assertTrue(alerts.deal_buttons(canonical))
        self.assertIsNone(bot.find_amazon_link("https://amazon.co.uk/dp/B0B9C4DKKG"))
        self.assertIsNone(bot.find_amazon_link("https://amazon.com/dp/B0B9C4DKKG"))
        self.assertFalse(alerts.deal_buttons("https://amazon.com/dp/B0B9C4DKKG"))

    def test_mydealz_source_and_german_categories(self):
        self.assertTrue(bot.dealabs_link("https://www.mydealz.de/deals/samsung-ssd"))
        self.assertIn("mydealz.de", bot.ALLOWED_FEED_DOMAINS)
        self.assertIn("www.mydealz.de", catalog.SOURCE_DOMAINS)
        self.assertIn("design & interior", bot.category_matches("Design Tischlampe Bauhaus"))
        self.assertIn("electronics/gaming", bot.category_matches("Grafikkarte RTX"))
        self.assertTrue(policy.is_allowed("Sony PlayStation 5 Slim"))
        self.assertFalse(policy.is_allowed("Xbox Series X"))

    def test_german_deal_candidate_through_telegram_and_site(self):
        item = {
            "title": "Samsung SSD 990 PRO -70%",
            "description": "https://amazon.de/dp/B0B9C4DKKG",
            "content": "", "link": "https://www.mydealz.de/deals/ssd990",
            "pubDate": "", "key": "de-deal", "image": "",
        }
        with patch.object(bot, "fetch", side_effect=AssertionError("no network expected")):
            found = bot.candidates([item])
        self.assertEqual(found[0]["market"], "amazon.de")
        rows = catalog.collect_records(
            [item], found, bot.category_matches, bot.percent,
            bot.price_pair, lambda x: None, lambda t: False,
            lambda d: True, bot.find_amazon_link
        )
        self.assertEqual(rows[0]["amazon_product_url"], "https://amazon.de/dp/B0B9C4DKKG")
        self.assertEqual(rows[0]["state"], "ready_for_alert")
        self.assertEqual(pricechecks.MARKETS["amazon.de"], 3)


if __name__ == "__main__":
    unittest.main()
