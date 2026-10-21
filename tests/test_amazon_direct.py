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
import fast_hunter


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

    def test_native_search_and_getitems_refresh_in_one_run(self):
        env = {
            "AMAZON_CREATORS_CLIENT_ID": "mock-client",
            "AMAZON_CREATORS_CLIENT_SECRET": "mock-secret",
            "AMAZON_PARTNER_TAG_FR": "mock-fr",
            "DIRECT_MAX_QUERIES": "1",
            "DIRECT_WATCH_BATCHES": "1",
        }
        product = {
            "asin": "B0B9C4DKKG",
            "itemInfo": {"title": {"displayValue": "Samsung SSD NVMe 990 Pro"}},
            "offersV2": {"listings": [{
                "condition": {"value": "New"}, "isBuyBoxWinner": True,
                "availability": {"type": "IN_STOCK"},
                "price": {"money": {"currency": "EUR", "amount": 99.99},
                          "savings": {"percentage": 55}},
            }]}
        }
        state = {}
        amazon_direct.add_watchlist(state, ["https://amazon.fr/dp/B0B9C4DKKG"])
        with patch.object(amazon_direct, "get_token", return_value="mock-token"), \
             patch.object(amazon_direct, "search_items",
                          return_value={"searchResult": {"items": [product]}}) as search, \
             patch.object(amazon_direct, "get_items",
                          return_value={"itemsResult": {"items": [product]}}) as get:
            results = amazon_direct.discover(
                state, env=env,
                now=datetime(2026, 10, 21, 12, tzinfo=timezone.utc),
                sleep_between=0
            )
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["direct_current_eur"], 99.99)
        self.assertEqual(search.call_count, 1)
        self.assertEqual(get.call_count, 1)
        self.assertIn("direct_price_history", state)

    def test_worker_dedupe_and_improvement_alert(self):
        state = {}
        item = {
            "title": "Samsung SSD 990 PRO", "link": "https://amazon.fr/dp/B0B9C4DKKG",
            "key": "https://amazon.fr/dp/B0B9C4DKKG",
            "image": None, "direct_source": "Amazon Creators API",
            "direct_current_eur": 80, "direct_claimed_percent": 60,
            "direct_affiliate_url": "https://amazon.fr/dp/B0B9C4DKKG",
            "direct_merchant": "Amazon EU",
        }
        deliveries = []
        def deliver(message, buttons, photo):
            deliveries.append(message)
        with patch.object(fast_hunter.amazon_direct, "discover", return_value=[item]), \
             patch.object(fast_hunter, "time") as clk:
            clk.time.return_value = 1200000
            self.assertEqual(fast_hunter.scan(state, deliver), 1)
            self.assertEqual(fast_hunter.scan(state, deliver), 0)
        self.assertEqual(len(deliveries), 1)

    def test_fast_rss_samples_six_publishers_only(self):
        seen = []
        rss = "<rss><channel><item><title>SSD -60%</title><link>https://www.dealabs.com/bons-plans/a</link></item></channel></rss>"
        def stub(url, timeout=15):
            seen.append(url)
            return rss
        with patch.object(bot, "RSS_URL", None), \
             patch.object(bot, "fetch", side_effect=stub), \
             patch.object(bot.pepper, "fetch_public_threads",
                          side_effect=AssertionError("Pepper not allowed on quick RSS fallback")):
            posts, okay, requested = bot.collect(fast_only=True)
        self.assertEqual((okay, requested), (6, 6))
        self.assertEqual(len(seen), 6)
        self.assertLessEqual(len(posts), 6)

    def test_market_rotation_does_not_starve_germany_spain_italy(self):
        env = {
            "AMAZON_CREATORS_CLIENT_ID":"mock", "AMAZON_CREATORS_CLIENT_SECRET":"mock",
            "AMAZON_PARTNER_TAG_FR":"tagfr", "AMAZON_PARTNER_TAG_DE":"tagde",
            "AMAZON_PARTNER_TAG_ES":"tages", "AMAZON_PARTNER_TAG_IT":"tagit",
            "DIRECT_MAX_QUERIES":"4", "DIRECT_WATCH_BATCHES":"0",
        }
        with patch.object(amazon_direct,"get_token",return_value="token"), \
             patch.object(amazon_direct,"search_items",return_value={
               "searchResult":{"items":[]}}) as search:
            result=amazon_direct.discover({},env=env,sleep_between=0)
        self.assertEqual(result,[])
        self.assertEqual([call.args[1] for call in search.call_args_list],
                         ["amazon.fr","amazon.de","amazon.es","amazon.it"])

    def test_getitems_uses_actual_itemResults_response(self):
        env={
            "AMAZON_CREATORS_CLIENT_ID":"mock", "AMAZON_CREATORS_CLIENT_SECRET":"mock",
            "AMAZON_PARTNER_TAG_DE":"tagde",
            "DIRECT_MAX_QUERIES":"1", "DIRECT_WATCH_BATCHES":"1",
        }
        state={}
        amazon_direct.add_watchlist(state,["https://amazon.de/dp/B0B9C4DKKG"])
        item={
            "asin":"B0B9C4DKKG",
            "itemInfo":{"title":{"displayValue":"Samsung SSD 990 Pro 2TB"}},
            "offersV2":{"listings":[{
              "condition":{"value":"New"},
              "price":{"money":{"currency":"EUR","amount":59.99}},
              "isBuyBoxWinner":True,
              "availability":{"type":"IN_STOCK"}
            }]}
        }
        with patch.object(amazon_direct,"get_token",return_value="token"), \
             patch.object(amazon_direct,"search_items",
                          return_value={"searchResult":{"items":[]}}), \
             patch.object(amazon_direct,"get_items",
                          return_value={"itemResults":{"items":[item]}}) as get:
            found=amazon_direct.discover(state,env=env,sleep_between=0)
        self.assertEqual(get.call_count,1)
        self.assertEqual(len(found),1)
        self.assertEqual(found[0]["direct_current_eur"],59.99)

    def test_fast_crash_detected_after_single_earlier_quote(self):
        from datetime import timedelta
        now=datetime(2026,10,21,12,tzinfo=timezone.utc).timestamp()
        item={"link":"https://amazon.fr/dp/B0B9C4DKKG","direct_current_eur":19.99}
        history={item["link"]:[[now-90,199.99]]}
        drop=amazon_direct.observe_price(history,item,now)
        self.assertGreaterEqual(drop,89)
        self.assertEqual(history[item["link"]][-1][1],19.99)
        # An old, expired quote must not trigger a bogus low-price alert.
        old={item["link"]:[[now-58*60,199.99]]}
        self.assertIsNone(amazon_direct.observe_price(old,item,now))
        # Consecutive checks less than one minute apart also do not pretend
        # to provide an independently timed observation.
        recent={item["link"]:[[now-10,199.99]]}
        self.assertIsNone(amazon_direct.observe_price(recent,item,now))

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
