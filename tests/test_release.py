"""Offline regression tests for release-ready persistence, UI, and price labels."""
from datetime import datetime, timezone, timedelta
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog
import catalog_ui
import observations
import pricechecks


class ReleaseTests(unittest.TestCase):
    def test_strict_asin_market_restrictions(self):
        self.assertEqual(
            pricechecks.identify("https://amazon.fr/dp/B0B9C4DKKG"),
            ("amazon.fr", "B0B9C4DKKG")
        )
        for url in [
            "https://amazon.fr.evil.example/dp/B0B9C4DKKG",
            "http://amazon.fr/dp/B0B9C4DKKG",
            "https://amazon.com/dp/B0B9C4DKKG",
            "https://amazon.fr/dp/not-an-asin",
            "https://amazon.fr/gp/product/B0B9C4DKKG",
        ]:
            self.assertIsNone(pricechecks.identify(url), url)

    def test_keepa_date_pricing_and_coverage(self):
        now = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
        def km(days):
            return int((now - timedelta(days=days)).timestamp() // 60) - 21564000
        product = {
            "asin": "B0B9C4DKKG", "domainId": 4, "lastUpdate": km(0),
            "csv": [[km(95), 10000, km(45), 8500, km(10), 7000]]
        }
        row = pricechecks.parse_product(product, "amazon.fr", "B0B9C4DKKG", now)
        self.assertEqual(row["source"], "Keepa")
        self.assertEqual(row["price_type"], "amazon")
        self.assertEqual(row["current_eur"], 70)
        self.assertGreater(row["reference_90d_eur"], 70)
        self.assertGreater(row["drop_90d_percent"], 0)
        self.assertFalse(row["seller_verified"])
        self.assertIsNone(pricechecks.parse_product(
            {**product, "domainId": 9}, "amazon.fr", "B0B9C4DKKG", now
        ))
        self.assertIsNone(pricechecks.parse_product(
            {**product, "lastUpdate": km(12)}, "amazon.fr", "B0B9C4DKKG", now
        ))

    def test_unchanged_advertised_price_is_not_fake_timeseries(self):
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "state.sqlite"
            record = {
                "source_url": "https://www.dealabs.com/bons-plans/ssd-test",
                "source": "www.dealabs.com", "title": "Samsung SSD 990 Pro 80 €",
                "category": "electronics/gaming", "claimed_discount": 40,
                "advertised_price_eur": 80.0,
                "amazon_product_url": "https://amazon.fr/dp/B0B9C4DKKG",
                "price_scope": "unverified_public_offer", "state": "ready_for_alert",
                "image_url": "",
            }
            catalog.save_records([record], db)
            self.assertEqual(observations.record_public_changes(
                [record], db, "2026-10-20T10:00:00+00:00"), 1)
            self.assertEqual(observations.record_public_changes(
                [record], db, "2026-10-21T10:00:00+00:00"), 0)
            with sqlite3.connect(db) as conn:
                n = conn.execute("SELECT COUNT(*) FROM public_price_changes").fetchone()[0]
            self.assertEqual(n, 1)
            record["advertised_price_eur"] = 75.0
            self.assertEqual(observations.record_public_changes(
                [record], db, "2026-10-21T10:01:00+00:00"), 1)

            published = datetime.now(timezone.utc).isoformat(timespec="seconds")
            sample = {
                "source": "Keepa", "market": "amazon.fr", "asin": "B0B9C4DKKG",
                "observed_at": published, "current_eur": 75.0,
                "reference_90d_eur": 95.0, "drop_90d_percent": 21.1,
                "price_type": "amazon",
            }
            observations.record_keepa([sample], db)
            dest = Path(temp) / "web"
            catalog.export(db, dest)
            result = catalog_ui.enhance(dest, db)
            self.assertEqual(result, {"cards": 1, "keepa": 1})
            html = (dest / "index.html").read_text("utf8")
            payload = json.loads((dest / "finds.json").read_text("utf8"))
            self.assertIn('data-tab="favorites"', html)
            self.assertIn('id="freshness"', html)
            self.assertIn('data-tab="keepa"', html)
            self.assertIn("localStorage", html)
            self.assertIn("Keepa", html)
            self.assertIn("не история Amazon", html)
            self.assertEqual(len(payload["offers"][0]["public_price_changes"]), 2)
            self.assertEqual(payload["offers"][0]["keepa"]["price_type"], "amazon")

    def test_keepa_history_missing_never_implies_discount(self):
        now = datetime(2026, 10, 21, 10, tzinfo=timezone.utc)
        km = int(now.timestamp() // 60) - 21564000
        row = pricechecks.parse_product({
            "asin": "B0B9C4DKKG", "domainId": 4, "lastUpdate": km,
            "csv": [[km - 60, 7599, km, 7599]]
        }, "amazon.fr", "B0B9C4DKKG", now)
        self.assertIsNotNone(row)
        self.assertIsNone(row["reference_90d_eur"])
        self.assertIsNone(row["drop_90d_percent"])


if __name__ == "__main__":
    unittest.main()
