"""Cross-border comparisons never turn an advertised price into a live quote."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import catalog
import catalog_ui


def row(market, amount):
    return {
        "source_url": f"https://www.dealabs.com/bons-plans/item-{market}",
        "source": "www.dealabs.com", "title": "SSD 990 PRO",
        "category": "electronics/gaming", "claimed_discount": 25,
        "advertised_price_eur": amount,
        "amazon_product_url": f"https://amazon.{market}/dp/B0B9C4DKKG",
        "price_scope": "unverified_public_offer", "state": "ready_for_alert",
        "image_url": "",
    }


class ComparisonTests(unittest.TestCase):
    def test_same_asin_is_crosslinked_without_claiming_checkout_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.sqlite"
            output = Path(tmp) / "site"
            catalog.save_records([row("fr", 89), row("de", 85)], db)
            catalog.export(db, output)
            catalog_ui.enhance(output, db)
            doc = json.loads((output / "finds.json").read_text("utf-8"))
            self.assertEqual(len(doc["offers"]), 2)
            fr = next(r for r in doc["offers"] if r["amazon_product_url"].startswith("https://amazon.fr"))
            self.assertEqual(fr["related_markets"][0]["market"], "amazon.de")
            self.assertEqual(fr["related_markets"][0]["advertised_price_eur"], 85)
            self.assertIn("not verified Amazon checkout", fr["related_markets"][0]["disclaimer"])
            html = (output / "index.html").read_text("utf-8")
            self.assertIn("Другие страны для этого ASIN", html)
            self.assertIn("одинаковую комплектацию", html)
            self.assertIn("85.00 € в объявлении", html)

    def test_distinct_asin_never_compared(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "catalog.sqlite"
            first = row("fr", 89)
            second = row("de", 85)
            second["amazon_product_url"] = "https://amazon.de/dp/B0B9C4DKKH"
            catalog.save_records([first, second], db)
            catalog.export(db, Path(tmp) / "site")
            catalog_ui.enhance(Path(tmp) / "site", db)
            doc = json.loads((Path(tmp) / "site/finds.json").read_text("utf-8"))
            self.assertTrue(all(not r["related_markets"] for r in doc["offers"]))


if __name__ == "__main__":
    unittest.main()
