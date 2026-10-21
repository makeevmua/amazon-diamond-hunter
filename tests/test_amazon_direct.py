"""Offline regression checks for first-party Amazon discovery and alerts."""
import sys
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import amazon_direct
import direct_pipeline
import bot
import catalog
import policy


class DirectDiscoveryTests(unittest.TestCase):
    def test_creators_api_disabled_without_valid_credentials(self):
        self.assertEqual(amazon_direct.discover({}, env={}), [])
        self.assertFalse(amazon_direct.enabled({}))

    def test_normalize_direct_amazon_new_offer_and_exact_asin(self):
        item = {
            "asin": "B0B9C4DKKG",
            "detailPageURL": "https://www.amazon.fr/dp/B0B9C4DKKG?tag=approved-21",
            "itemInfo": {"title": {"displayValue": "Samsung SSD 990 PRO 2 To"}},
            "images": {"primary": {"medium": {"url": "https://m.media-amazon.com/images/I/foo.jpg"}}},
            "offersV2": {"listings": [{
                "condition": {"value": "New"},
                "availability": {"type": "IN_STOCK"},
                "isBuyBoxWinner": True,
                "price": {
                    "money": {"amount": 79.99, "currency": "EUR"},
                    "savingBasis": {"money": {"amount": 199.99, "currency": "EUR"}},
                    "savings": {"percentage": 60}
                },
                "merchantInfo": {"name": "Amazon EU"}
            }]}
        }
        result = amazon_direct.normalize_item(item, "amazon.fr",
                         "Wed, 21 Oct 2026 12:00:00 GMT")
        self.assertEqual(result["direct_current_eur"], 79.99)
        self.assertEqual(result["direct_claimed_percent"], 60)
        self.assertEqual(result["direct_affiliate_url"],
                         "https://www.amazon.fr/dp/B0B9C4DKKG?tag=approved-21")
        self.assertEqual(result["link"], "https://amazon.fr/dp/B0B9C4DKKG")
        self.assertTrue(policy.is_allowed(result["title"]))
        d = direct_pipeline.to_candidate(result, bot.category_matches)
        self.assertEqual(d["signal"], "direct_amazon_promo")
        self.assertFalse(d["source_only"])
        self.assertIn("Цена в Amazon API: 79.99", direct_pipeline.format_direct_alert(d))
        self.assertIn("НЕ проверены", direct_pipeline.format_direct_alert(d))
        return result

    def test_no_invalid_sellers_xbox_or_samsung_phone(self):
        for title in ["Xbox Series X 1TB", "Samsung Galaxy S26", "Case Galaxy A55"]:
            template = {"asin": "B0B9C4DKKG", "itemInfo": {"title": {"displayValue": title}},
                        "offersV2": {"listings": [{"condition": {"value": "New"},
                                    "price": {"money": {"amount": 100, "currency": "EUR"}}}]}}
            self.assertIsNone(amazon_direct.normalize_item(template, "amazon.fr", "now"))
        template = {"asin": "B0B9C4DKKG", "itemInfo": {"title": {"displayValue": "LEGO Technic"}},
                    "offersV2": {"listings": [{"condition": {"value": "Used"},
                                    "price": {"money": {"amount": 20, "currency": "EUR"}}}]}}
        self.assertIsNone(amazon_direct.normalize_item(template, "amazon.fr", "now"))
        self.assertIsNone(amazon_direct.normalize_item(
            {"asin":"B0B9C4DKKG","itemInfo":{"title":{"displayValue":"SSD Kingston"}},
             "offersV2":{"listings":[{"condition":{"value":"New"},
               "price":{"money":{"amount":20,"currency":"GBP"}}}]}},
            "amazon.fr", "now"))

    def test_observed_short_term_drop_and_23h_limit(self):
        now = datetime(2026, 10, 21, 10, tzinfo=timezone.utc).timestamp()
        link = "https://amazon.de/dp/B0B9C4DKKG"
        state = {link: [[now - 2400, 140], [now - 1800, 135]]}
        item = {"link":link,"direct_current_eur":70}
        observed = amazon_direct.observe_price(state, item, now)
        self.assertGreaterEqual(observed, 45)
        state[link].append([now - 90_000, 12])
        with patch.object(amazon_direct, "enabled", return_value=False):
            self.assertEqual(amazon_direct.discover({}, env={}), [])
        # old cache data must not contaminate the comparison
        self.assertNotEqual(amazon_direct.observe_price(state, item, now + 2), 0)

    def test_native_amazon_index_not_mislabelled_as_rss_article(self):
        offer = {
          "title": "Samsung SSD 990 PRO -30%", "description": "", "content": "",
          "link": "https://amazon.fr/dp/B0B9C4DKKG",
          "key": "https://amazon.fr/dp/B0B9C4DKKG",
          "pubDate": datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S GMT"),
          "image": "", "direct_source": "Amazon Creators API",
          "direct_current_eur": 99.99, "direct_claimed_percent": 30,
        }
        rows = catalog.collect_records(
           [offer], [], bot.category_matches, bot.percent, bot.price_pair,
           lambda t: None, lambda t: False, lambda t: True, bot.find_amazon_link
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["price_scope"], "amazon_creators_featured_new")
        self.assertEqual(rows[0]["advertised_price_eur"], 99.99)
        self.assertEqual(rows[0]["amazon_product_url"], offer["link"])
        self.assertTrue(all(r["price_scope"] != "unverified_public_offer" for r in rows))


if __name__ == "__main__":
    unittest.main()
