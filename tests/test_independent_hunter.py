"""Tests independent price change discovery and paid-verification guardrails."""
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import brightdata_verify
import catalog
import direct_pipeline
import keepa_discovery

NOW = datetime(2026, 10, 21, 12, tzinfo=timezone.utc)


def km(dt):
    return int(dt.timestamp() // 60 - 21564000)


def keepa_record(name="Samsung SSD 990 PRO NVMe", amount=5999, avg=15999):
    return {
        "asin": "B0B9C4DKKG", "title": name,
        "lastUpdate": km(NOW - timedelta(minutes=3)),
        "creationDate": km(NOW - timedelta(minutes=7)),
        "current": [amount, amount],
        "avg": [[-1, -1],[-1, -1],[-1, -1],[avg, avg]],
        "image": list(map(ord, "61k3Lay7JUL.jpg")),
    }


class KeepaIndependentTests(unittest.TestCase):
    def test_keepa_90_day_average_and_price_drop(self):
        row = keepa_discovery.normalize_deal(
            keepa_record(), "amazon.fr", 0, NOW
        )
        self.assertEqual(row["direct_source"], "Keepa Deals")
        self.assertEqual(row["direct_current_eur"], 59.99)
        self.assertEqual(row["keepa_reference90_eur"], 159.99)
        self.assertEqual(row["keepa_price_type"], "amazon")
        self.assertIn("images-na.ssl-images-amazon.com", row["image"])
        deal = direct_pipeline.keepa_candidate(row, bot.category_matches)
        self.assertEqual(deal["signal"], "keepa_recent_drop")
        self.assertIn("KEEPA", direct_pipeline.format_direct_alert(deal))
        self.assertIn("90 дней", direct_pipeline.format_direct_alert(deal))
        self.assertIn("не проверена", direct_pipeline.format_direct_alert(deal))

    def test_policy_bad_data_freshness_and_low_drop(self):
        self.assertIsNone(keepa_discovery.normalize_deal(
            keepa_record("Xbox Series X"), "amazon.fr", 0, NOW
        ))
        self.assertIsNone(keepa_discovery.normalize_deal(
            keepa_record("Samsung Galaxy S25"), "amazon.fr", 0, NOW
        ))
        self.assertIsNone(keepa_discovery.normalize_deal(
            keepa_record(amount=-1), "amazon.fr", 0, NOW
        ))
        self.assertIsNone(keepa_discovery.normalize_deal(
            keepa_record(amount=14999), "amazon.fr", 0, NOW
        ))
        row = keepa_record()
        row["lastUpdate"] = km(NOW - timedelta(hours=5))
        self.assertIsNone(keepa_discovery.normalize_deal(row,"amazon.fr",0,NOW))

    def test_query_schema_and_missing_key_no_usage(self):
        self.assertEqual(keepa_discovery.discover({}, env={}), [])
        q = keepa_discovery.deals_query("amazon.de")
        self.assertEqual(q["domainId"], 3)
        self.assertEqual(q["priceTypes"], [0])
        self.assertEqual(q["sortType"], 1)
        self.assertEqual(q["currentRange"][0], 100)

    def test_day_quota_and_round_robin(self):
        state = {}
        env = {"KEEPA_API_KEY":"placeholder",
               "KEEPA_MAX_DEAL_QUERIES_PER_DAY":"2"}
        with patch.object(keepa_discovery,"request_deals",
                          return_value=[keepa_record()]) as fetch:
            first=keepa_discovery.discover(state, env=env,now=NOW,requests_per_scan=4)
            keepa_discovery.discover(state,env=env,now=NOW,requests_per_scan=4)
        self.assertEqual(fetch.call_count,2)
        self.assertEqual(len(first),1) # Same ASIN across distinct EU markets
        self.assertEqual(state["keepa_deals_usage"]["calls"],2)
        self.assertEqual(state["keepa_deals_cursor"],2)


class BrightDataTests(unittest.TestCase):
    def test_no_token_means_zero_cost_and_zero_requests(self):
        with patch.object(brightdata_verify,"_request_product") as req:
            self.assertEqual(brightdata_verify.verify(
                [{"amazon":"https://amazon.fr/dp/B0B9C4DKKG"}], {}, env={}), {})
            req.assert_not_called()

    def test_strict_price_identity_and_budget(self):
        url = "https://amazon.fr/dp/B0B9C4DKKG"
        row = {"url":"https://www.amazon.fr/dp/B0B9C4DKKG",
               "asin":"B0B9C4DKKG","currency":"EUR","final_price":59.89}
        self.assertTrue(brightdata_verify.exact_item(row,url))
        self.assertEqual(brightdata_verify.amount(row),59.89)
        self.assertFalse(brightdata_verify.exact_item(
            {**row,"url":"https://amazon.de/dp/B0B9C4DKKG"},url))
        self.assertFalse(brightdata_verify.exact_item(
            {**row,"asin":"B0B9C4DKKH"},url))
        self.assertIsNone(brightdata_verify.amount({**row,"currency":"USD"}))
        state={}
        fake = [{"amazon":url}]
        with patch.object(brightdata_verify,"_request_product",return_value=row) as req:
            checks=brightdata_verify.verify(fake,state,
                                            env={"BRIGHTDATA_API_TOKEN":"placeholder"},now=NOW)
            again=brightdata_verify.verify(fake,state,
                                           env={"BRIGHTDATA_API_TOKEN":"placeholder"},now=NOW)
        self.assertEqual(checks[url]["price_eur"],59.89)
        self.assertEqual(again,{})
        self.assertEqual(req.call_count,1)
        self.assertEqual(state["brightdata_usage"]["daily"],1)

    def test_keepa_storefront_is_not_mislabeled(self):
        post = keepa_discovery.normalize_deal(
            keepa_record(), "amazon.de", 0, NOW
        )
        rows = catalog.collect_records([post], [], bot.category_matches,
                  bot.percent, bot.price_pair, lambda x: None,lambda x: False,
                  lambda x: True,bot.find_amazon_link)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["price_scope"],"keepa_observed_new")
        self.assertEqual(rows[0]["advertised_price_eur"],59.99)
        self.assertEqual(rows[0]["amazon_product_url"],"https://amazon.de/dp/B0B9C4DKKG")


if __name__ == "__main__":
    unittest.main()
