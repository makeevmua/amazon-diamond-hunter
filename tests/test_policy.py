"""Regression tests for scope, Pepper fallback, and old-catalog cleanup."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import catalog
import pepper
import policy
import signals


def offer(title, url, price=None):
    return {
        "title": title, "description": "", "content": "", "link": url,
        "key": url, "pubDate": "", "image": "", "advertised_price_eur": price,
    }


def collect_catalog(posts):
    return catalog.collect_records(
        posts, [], bot.category_matches, bot.percent, bot.price_pair,
        signals.headline_price,
        lambda text: bool(bot.EXCLUDE_RE.search(text)
                          or bot.OBVIOUS_NO_NAME.search(text)),
        bot.fresh_enough, bot.find_amazon_link,
    )


class ProductPolicyTests(unittest.TestCase):
    def test_only_xbox_excluded_from_gaming(self):
        for title in (
            "Xbox Series X 1 To", "Manette Xbox Elite", "Xbox Game Pass PC",
            "Juegos Xbox One", "Casque Xbox et PC",
        ):
            with self.subTest(title=title):
                self.assertFalse(policy.is_allowed(title))
        for title in (
            "Sony PlayStation 5 Slim", "DualSense PS5", "Jeu PS5 Spider-Man",
            "Nintendo Switch 2", "PC Gamer RTX 5080", "SSD NVMe pour PS5",
            "Steam PC game",
        ):
            with self.subTest(title=title):
                self.assertTrue(policy.is_allowed(title))
        self.assertTrue(policy.is_allowed("Casque PlayStation PS5", "compatible PC et Xbox"))
        self.assertFalse(policy.is_allowed("Manette sans fil", "compatible Xbox Series X"))


    def test_no_underwear_or_any_hair_products(self):
        excluded = (
            "CALVIN KLEIN Icon Boxers coton stretch",
            "Calvin Klein boxers 3 pack",
            "Lot de 3 caleçons homme",
            "Sous-vêtements en coton",
            "Men's underwear cotton trunks",
            "Slips homme 5-pack",
            "FINO Masque capillaire Premium Touch 230 g",
            "Fino hair mask Premium Touch",
            "Shampooing cheveux secs",
            "Shampoing Kérastase cheveux secs",
            "Après-shampoing soin cheveux",
            "Hair dryer Dyson Supersonic",
            "Lisseur cheveux GHD",
            "Maschera per capelli",
            "Mascarilla capilar",
        )
        for title in excluded:
            with self.subTest(title=title):
                self.assertFalse(policy.is_allowed(title))
        for title in ("Crème visage La Roche-Posay", "Brosse à dents Oral-B",
                      "LEGO Technic 1:8 Supercar", "Nintendo Switch 2"):
            with self.subTest(title=title):
                self.assertTrue(policy.is_allowed(title))

    def test_interior_only_when_agreed_design_is_explicit(self):
        generic = (
            "JOYFURNOS Grand Panier à Linge en Bambou",
            "Panier à linge avec couvercle 60L",
            "Bamboo laundry basket storage",
            "Chaise de bureau ergonomique simple",
            "Lampe de chevet classique blanc",
            "Vase céramique jaune",
            "Table basse en bois",
            "Tapis rond de salon beige",
        )
        for title in generic:
            with self.subTest(title=title):
                self.assertFalse(policy.is_allowed(title))
        wanted = (
            "Chaise Bauhaus design",
            "Vase sculptural géométrique jaune",
            "Lampe style Memphis colorée",
            "Table basse Mid-Century Modernist",
            "Miroir ondulé pop art",
            "Canapé scandinave minimaliste",
            "Panier à linge sculptural style Memphis",
            "Lampe champignon Space Age",
            "Tapis Bauhaus",
            "Tapis de souris Logitech G Pro gaming",
        )
        for title in wanted:
            with self.subTest(title=title):
                self.assertTrue(policy.is_allowed(title))

    def test_only_samsung_ssds_remain(self):
        allow = ("Samsung SSD 990 Pro 2 To", "SSD Samsung 870 EVO",
                 "Samsung 980 NVMe pour PS5", "Samsung T7 Shield 2TB")
        reject = ("Samsung Galaxy S26 Ultra", "Samsung Galaxy Tab S10",
                  "Samsung TV Neo QLED", "Samsung Odyssey OLED",
                  "Samsung Galaxy Buds", "Coque Samsung Galaxy S25",
                  "Samsung lave-linge", "Chargeur Samsung 45W",
                  "Coque Galaxy S23", "Galaxy Watch 8")
        for title in allow:
            with self.subTest(allow=title):
                self.assertTrue(policy.is_allowed(title))
        for title in reject:
            with self.subTest(reject=title):
                self.assertFalse(policy.is_allowed(title))

    def test_alerts_and_catalog_both_respect_policy(self):
        posts = [
            offer("Xbox Series X -80%", "https://www.dealabs.com/bons-plans/xbox"),
            offer("Samsung Galaxy S26 Ultra -80%", "https://www.dealabs.com/bons-plans/galaxy"),
            offer("Samsung SSD 990 PRO -60%", "https://www.dealabs.com/bons-plans/ssd", 70),
            offer("Sony DualSense PS5 -70%", "https://www.dealabs.com/bons-plans/ps5", 50),
        ]
        for p in posts:
            p["description"] = "https://amazon.fr/dp/B0B9C4DKKG"
        records = collect_catalog(posts)
        self.assertEqual(len(records), 2)
        self.assertEqual({v["title"] for v in records},
                         {"Samsung SSD 990 PRO -60%", "Sony DualSense PS5 -70%"})
        self.assertEqual(records[0]["advertised_price_eur"], 70)
        with patch.object(bot, "fetch", side_effect=AssertionError("unexpected remote fetch")):
            self.assertEqual(len(bot.candidates(posts)), 2)

    def test_purge_legacy_excluded_rows_from_sqlite(self):
        with tempfile.TemporaryDirectory() as temp:
            db = str(Path(temp) / "finds.sqlite")
            rows = [
                {
                    "source_url": "https://www.dealabs.com/bons-plans/xbox",
                    "source": "www.dealabs.com", "title": "Xbox Series S 199 €",
                    "category": "electronics/gaming", "claimed_discount": 50,
                    "advertised_price_eur": 199.0, "amazon_product_url": None,
                    "price_scope": "unverified_public_offer", "state": "watchlist",
                    "image_url": "",
                },
                {
                    "source_url": "https://www.dealabs.com/bons-plans/ps5",
                    "source": "www.dealabs.com", "title": "Sony PlayStation 5 Slim 350 €",
                    "category": "electronics/gaming", "claimed_discount": 30,
                    "advertised_price_eur": 350.0, "amazon_product_url": None,
                    "price_scope": "unverified_public_offer", "state": "watchlist",
                    "image_url": "",
                },
            ]
            self.assertEqual(catalog.save_records(rows, db), 1)
            self.assertEqual(catalog.save_records([], db), 1)
            data = catalog.export(db, str(Path(temp) / "export"))
            self.assertEqual(data["total"], 1)

    def test_public_graphql_post_normalization(self):
        threads = [
            {
                "threadId": 10, "title": "Samsung SSD 990 PRO",
                "url": "/bons-plans/ssd-1", "price": "89,99",
                "publishedAt": "2026-10-21T09:00:00Z", "description": "",
            },
            {
                "title": "Wrong destination", "url": "https://evil.example/bons-plans/x",
                "price": "1", "publishedAt": "2026-10-21T09:00:00Z",
            },
            {
                "title": "Expired", "url": "/bons-plans/expired", "isExpired": True,
            }
        ]
        norm = pepper.normalize(threads)
        self.assertEqual(len(norm), 1)
        self.assertEqual(norm[0]["advertised_price_eur"], 89.99)
        self.assertIn("www.dealabs.com", norm[0]["link"])
        self.assertIn("GMT", norm[0]["pubDate"])
        self.assertIsNone(pepper.offered_eur("not-price"))
        self.assertIsNone(pepper.source_url("https://evil.example/bons-plans/x"))

    def test_optional_graphql_merges_when_rss_available(self):
        rss = """<rss><channel><item><title>SSD Samsung 990 PRO -50%</title>
        <link>https://www.dealabs.com/bons-plans/ssd-1</link></item></channel></rss>"""
        pepper_rows = [offer("Samsung SSD 990 PRO",
                             "https://www.dealabs.com/bons-plans/ssd-1", 89.0)]
        with patch.object(bot, "FEEDS", ["https://www.dealabs.com/rss"]), \
             patch.object(bot, "RSS_URL", None), \
             patch.object(bot, "fetch", return_value=rss), \
             patch.object(bot.pepper, "fetch_public_threads", return_value=pepper_rows), \
             patch.dict(os.environ, {"PEPPER_GRAPHQL_ENABLED": "true",
                                     "EXTRA_RSS_FEEDS": ""}):
            posts, count, requested = bot.collect()
        self.assertEqual((len(posts), count, requested), (1, 1, 1))
        self.assertEqual(posts[0]["advertised_price_eur"], 89.0)


if __name__ == "__main__":
    unittest.main()
