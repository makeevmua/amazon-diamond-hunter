import csv
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import catalog
import signals


def post(title, source, description="", key="test", image=None):
    return {
        "title": title, "link": source, "description": description,
        "key": key, "image": image, "pubDate": "", "content": "",
    }


def make_catalog(posts, candidates=None):
    return catalog.collect_records(
        posts, candidates or [],
        bot.category_matches, bot.percent, bot.price_pair,
        signals.headline_price,
        lambda text: bool(bot.EXCLUDE_RE.search(text)
                          or bot.OBVIOUS_NO_NAME.search(text)),
        bot.fresh_enough, bot.find_amazon_link,
    )


class CatalogTests(unittest.TestCase):
    def test_includes_below_alert_threshold_deals(self):
        posts = [
            post("LEGO Technic 35% off",
                 "https://www.dealabs.com/bons-plans/lego-low",
                 "LEGO Technic discount -35%"),
            post("Lampe Bauhaus 29 €",
                 "https://www.dealabs.com/bons-plans/bauhaus",
                 "https://amazon.fr/dp/B0B9C4DKKG"),
            post("Pull en laine -75%",
                 "https://www.dealabs.com/bons-plans/wool",
                 "Pull homme, vêtements"),
        ]
        rows = make_catalog(posts)
        self.assertEqual(len(rows), 2)
        self.assertTrue(any("LEGO" in x["category"] for x in rows))
        self.assertTrue(any(x["advertised_price_eur"] == 29 for x in rows))
        self.assertTrue(any(x["state"] == "watchlist" for x in rows))

    def test_merchant_mixed_comparison_never_attributed_to_amazon(self):
        row = post(
            "Samsung Galaxy S26+ 889 € chez Joybuy",
            "https://www.lesnumeriques.com/telephone-portable/s26.html",
            "Prix chez Joybuy 889 €, sur Amazon 1172 €. "
            "https://amazon.fr/dp/B0G53TJ5LJ",
        )
        rows = make_catalog([row])
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["amazon_product_url"])
        self.assertEqual(rows[0]["price_scope"], "mixed_merchants_unverified")
        self.assertEqual(rows[0]["state"], "editorial_review")

    def test_rejects_deceptive_roundups_and_unwanted_country(self):
        items = [
            post("Discounts at Amazon.es",
                 "https://brickset.com/article/101",
                 "LEGO Technic -85% https://amazon.es/dp/B0B9C4DKKG"),
            post("Samsung SSD -60%", "https://hotukdeals.com/deals/x",
                 "https://amazon.co.uk/dp/B0B9C4DKKG"),
            post("Sony headset -80%", "https://slickdeals.net/f/xx",
                 "https://amazon.com/dp/B0B9C4DKKG"),
            post("Dyson aspirateur -70%", "https://www.dealabs.com/bons-plans/dyson",
                 "https://amazon.fr/dp/B0B9C4DKKG"),
        ]
        rows = make_catalog(items)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "www.dealabs.com")

    def test_deduplication_and_history_are_observations_not_live_prices(self):
        item = make_catalog([post(
            "LEGO Technic 79 €",
            "https://www.dealabs.com/bons-plans/lego",
            "https://amazon.es/dp/B0B9C4DKKG",
        )])[0]
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "catalog.sqlite"
            count = catalog.save_records([item], db, "2026-10-20T12:00:00+00:00")
            self.assertEqual(count, 1)
            catalog.save_records([item], db, "2026-10-21T12:00:00+00:00")
            with sqlite3.connect(db) as conn:
                r = conn.execute(
                    "SELECT times_seen,first_seen,last_seen,price_scope "
                    "FROM offers WHERE source_url=?", (item["source_url"],)
                ).fetchone()
            self.assertEqual(r[0], 2)
            self.assertIn("2026-10-20", r[1])
            self.assertIn("2026-10-21", r[2])
            self.assertEqual(r[3], "unverified_public_offer")

    def test_exports_searchable_html_json_csv(self):
        rows = make_catalog([
            post('Lampe Bauhaus <script>alert(1)</script> -30%',
                 "https://www.dealabs.com/bons-plans/bauhaus",
                 "https://amazon.fr/dp/B0B9C4DKKG"),
            post("LEGO Technic -60%",
                 "https://www.dealabs.com/bons-plans/technic",
                 "https://amazon.es/dp/B0B9C4DKKH"),
        ])
        with tempfile.TemporaryDirectory() as temp:
            db = Path(temp) / "db.sqlite"
            dest = Path(temp) / "export"
            catalog.save_records(rows, db)
            output = catalog.export(db, dest)
            self.assertEqual(output["total"], 2)
            webpage = (dest / "index.html").read_text(encoding="utf-8")
            self.assertIn('id="search"', webpage)
            self.assertNotIn("<script>alert(1)</script>", webpage)
            with (dest / "finds.csv").open(encoding="utf-8-sig") as fh:
                self.assertEqual(len(list(csv.DictReader(fh))), 2)
            payload = json.loads((dest / "finds.json").read_text("utf-8"))
            self.assertEqual(payload["total_saved"], 2)
            self.assertEqual(len(payload["offers"]), 2)

    def test_run_catalog_enabled_does_not_change_alert_selection(self):
        item = post("LEGO Technic -35%",
                    "https://www.dealabs.com/bons-plans/under-threshold",
                    "LEGO Technic -35%", key="low")
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(bot, "STATE_FILE", Path(folder) / "seen.json"), \
             patch.object(bot, "CATALOG_PATH", Path(folder) / "catalog.sqlite"), \
             patch.object(bot, "CATALOG_EXPORT_DIR", Path(folder) / "export"), \
             patch.object(bot, "CATALOG_ENABLED", True), \
             patch.object(bot, "DRY_RUN", True), \
             patch.object(bot, "collect", return_value=([item], 1, 1)):
            self.assertEqual(bot.run(), 0)
            self.assertTrue((Path(folder) / "catalog.sqlite").exists())
            with sqlite3.connect(Path(folder) / "catalog.sqlite") as connection:
                self.assertEqual(connection.execute(
                    "SELECT count(*) FROM offers").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
