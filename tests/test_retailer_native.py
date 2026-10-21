"""Direct retailer and universal >=25% acceptance without unwanted products."""
import os
import sys
import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import bot
import policy
import direct_pipeline
import retailer_direct
import retail_scope
import keepa_discovery


NOW = datetime(2026, 10, 21, 14, tzinfo=timezone.utc)
CD = "https://www.cdiscount.com/maison/achat-meuble-literie/emma-matelas-original-140x190-cm-memoire-de-fo/f-1175520-emm4260568040740.html"
JB = "https://www.joybuy.fr/dp/hyperx-cloud-stinger-2/102762790"


def html_product(title, price, link, image="https://cdn.example/p.jpg"):
    return ('<html><script type="application/ld+json">' +
            json.dumps({"@context":"https://schema.org",
                        "@type":"Product","name":title,"url":link,
                        "image":image,
                        "offers":{"@type":"Offer","priceCurrency":"EUR",
                                  "price":price,"availability":"https://schema.org/InStock"}}) +
            "</script></html>")


class MerchantTests(unittest.TestCase):
    def test_all_allowed_categories_25_percent_not_50(self):
        self.assertEqual(bot.MIN_DISCOUNT,25)
        names=["LEGO Star Wars", "PC gamer RTX 5080", "PS5 DualSense",
               "HyperX Cloud III Wireless Casque", "Dyson aspirateur robot",
               "Chaise Bauhaus design", "Vase sculptural céramique jaune"]
        for title in names:
            with self.subTest(title=title):
                item={"title":title,"direct_source":"Bright Data Amazon Search",
                      "direct_current_eur":75.0,"direct_claimed_percent":25,
                      "link":"https://amazon.fr/dp/B0B9C4DKKG"}
                self.assertIsNotNone(direct_pipeline.to_candidate(item,bot.category_matches))
                item["direct_claimed_percent"]=24
                self.assertIsNone(direct_pipeline.to_candidate(item,bot.category_matches))
        for title in ["Surchemise Bonobo Homme", "Catan jeu de société",
                      "Xbox Series X", "Samsung Galaxy S25"]:
            item={"title":title,"direct_source":"Bright Data Amazon Search",
                  "direct_current_eur":60,"direct_claimed_percent":85,
                  "link":"https://amazon.fr/dp/B0B9C4DKKG"}
            self.assertIsNone(direct_pipeline.to_candidate(item,bot.category_matches))

    def test_product_link_extraction_from_merchant_listing_only(self):
        sample=('<html><a href="'+CD+'"><span>Promo -39%</span>'
                '<div>Casque HyperX</div><strong>149,99 €</strong>'
                '<em>90€99</em></a><a href="https://www.dealabs.com/x">'
                'Other fake deal</a></html>')
        parser=retailer_direct.ProductLinks("https://www.cdiscount.com/")
        parser.feed(sample)
        self.assertEqual(len(parser.links),1)
        self.assertIn("-39%",parser.links[0][1])
        self.assertIn("90€99",parser.links[0][1])

    def test_cdiscount_same_product_pdp_price_and_reference(self):
        p=retailer_direct._retailer_product(
            CD, html_product("Chaise Bauhaus moderne",90.99,CD),
            "Cdiscount",NOW,teaser="Promo -39% Chaise Bauhaus 149,99€ 90€99"
        )
        self.assertIsNotNone(p)
        self.assertEqual(p["direct_source"],"Cdiscount Direct")
        self.assertEqual(p["direct_current_eur"],90.99)
        self.assertGreaterEqual(p["direct_claimed_percent"],25)
        found=direct_pipeline.to_candidate(p,bot.category_matches)
        self.assertEqual(found["discount"],39)
        self.assertEqual(found["market"],"www.cdiscount.com")
        msg=direct_pipeline.format_direct_alert(found)
        self.assertIn("Cdiscount",msg)
        self.assertIn("историей цен",msg)

    def test_joybuy_rpp_eur_prefix(self):
        p=retailer_direct._retailer_product(
            JB, html_product("HyperX Cloud Stinger 2 Casque",27.99,JB),
            "Joybuy",NOW,teaser="44% off HyperX Cloud Stinger 2 €27.99 RRP €49.99"
        )
        self.assertIsNotNone(p)
        self.assertEqual(p["direct_source"],"Joybuy Direct")
        self.assertEqual(p["direct_current_eur"],27.99)
        deal=direct_pipeline.to_candidate(p,bot.category_matches)
        self.assertIsNotNone(deal)
        self.assertGreaterEqual(deal["discount"],44)
        self.assertIn("Joybuy",direct_pipeline.format_direct_alert(deal))

    def test_joybuy_listing_fallback_is_marked_unverified(self):
        teaser = ("HyperX Cloud Stinger 2 Casque -44% "
                  "€27.99 RRP:€49.99")
        product = retailer_direct._retailer_product(
            JB, "<html>Real Joybuy product without JSON-LD</html>",
            "Joybuy", NOW, teaser=teaser
        )
        self.assertIsNotNone(product)
        self.assertEqual(product["direct_current_eur"], 27.99)
        self.assertEqual(product["direct_claimed_percent"], 44)
        self.assertEqual(product["direct_offer_type"],
                         "RETAILER_LISTING_RRP_UNVERIFIED_PDP")
        self.assertIsNotNone(
            direct_pipeline.to_candidate(product,bot.category_matches)
        )
        bad = "Casque Gaming €9.99 RRP:€49.99 €29.99"
        self.assertIsNone(retailer_direct._listing_fallback(
            JB,bad,"Joybuy",NOW
        ))

    def test_joybuy_live_french_spaced_cents(self):
        teaser = ("45% de réduction Meilleures ventes celimax "
                  "Celimax The Vita A Retinal Shot Tightening Booster with Liposomized Retinal "
                  "4.9 8k+ vendus Offre de bienvenue 11 , 90 € Prix initial: 21 , 90 € "
                  "Livraison d'ici le demain Vendu par Joybuy")
        offer = retailer_direct._listing_fallback(JB, teaser, "Joybuy", NOW)
        self.assertIsNotNone(offer)
        self.assertEqual(offer["direct_current_eur"], 11.9)
        self.assertEqual(offer["direct_list_price_eur"], 21.9)
        self.assertEqual(offer["direct_claimed_percent"], 46)

    def test_invalid_price_duplicate_and_unsupported_domain_dropped(self):
        markup=html_product("HyperX Gaming Casque",29.99,JB)
        self.assertIsNone(retailer_direct._retailer_product(
            "https://www.dealabs.com/bons-plans/x",markup,"Joybuy",NOW,
            teaser="RRP €59.99 €29.99"))
        self.assertIsNone(retailer_direct._retailer_product(
            JB,html_product("Xbox Series X",29.99,JB),"Joybuy",NOW,
            teaser="RRP €59.99 €29.99"))
        self.assertIsNone(retailer_direct._retailer_product(
            JB,html_product("HyperX Gaming Casque",49.99,JB),"Joybuy",NOW,
            teaser="RRP €59.99 €49.99"))
        self.assertIsNone(retailer_direct._retailer_product(
            JB,"<html>No structured price</html>","Joybuy",NOW,
            teaser="RRP €59.99 €29.99"))

    def test_optional_cdiscount_brightdata_unlocker_with_budget(self):
        listing = '<a href="'+CD+'">Promo -39% Chaise Bauhaus 149,99€ 90€99</a>'
        def blocked(url, shop):
            if shop == "Cdiscount" and url != CD:
                return "<html>blocked listing</html>"
            if url == CD:
                return html_product("Chaise Bauhaus moderne",90.99,CD)
            return "<html>blocked</html>"
        state = {}
        with patch.dict(os.environ, {
            "BRIGHTDATA_API_TOKEN": "mock-token",
            "BRIGHTDATA_UNLOCKER_ZONE": "mock-zone",
        }), patch.object(retailer_direct, "_fetch_page", side_effect=blocked), \
             patch.object(retailer_direct, "_fetch_cdiscount_with_provider", return_value=listing) as unlock:
            rows=retailer_direct.discover(state,env={
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1",
                "BRIGHTDATA_CDISCOUNT_MAX_DAILY":"1",
                "RETAIL_DIRECT_ENABLED":"true",
            },now=NOW)
            next_rows=retailer_direct.discover(state,env={
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1",
                "BRIGHTDATA_CDISCOUNT_MAX_DAILY":"1",
                "RETAIL_DIRECT_ENABLED":"true",
            },now=NOW+timedelta(minutes=20))
        self.assertEqual(unlock.call_count,1)
        self.assertEqual(state["cdiscount_unlocker_usage"]["count"],1)
        self.assertEqual(len(rows),1)
        self.assertEqual(len(next_rows),0)

    def test_cdiscount_http_403_reaches_opt_in_unlocker(self):
        listing = '<a href="'+CD+'">Promo -39% Chaise Bauhaus 149,99€ 90€99</a>'
        def gated(url, shop):
            if shop == "Cdiscount" and url != CD:
                raise HTTPError(url, 403, "blocked", {}, None)
            if url == CD:
                return html_product("Chaise Bauhaus moderne",90.99,CD)
            return "<html>no Joybuy products</html>"
        with patch.dict(os.environ, {
            "BRIGHTDATA_API_TOKEN": "mock-token",
            "BRIGHTDATA_UNLOCKER_ZONE": "mock-zone",
        }), patch.object(retailer_direct, "_fetch_page", side_effect=gated), \
             patch.object(retailer_direct, "_fetch_cdiscount_with_provider", return_value=listing) as unlock:
            state = {}
            rows = retailer_direct.discover(state, env={
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1",
                "BRIGHTDATA_CDISCOUNT_MAX_DAILY":"1",
                "RETAIL_DIRECT_ENABLED":"true",
            }, now=NOW)
        self.assertEqual(unlock.call_count, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(state["retailer_direct_health"]["Cdiscount"]["status"], "scanned")

    def test_cdiscount_http_403_without_unlocker_reports_required_setup(self):
        with patch.dict(os.environ, {
            "BRIGHTDATA_API_TOKEN": "",
            "BRIGHTDATA_UNLOCKER_ZONE": "",
        }), patch.object(retailer_direct, "_fetch_page",
                          side_effect=HTTPError("https://www.cdiscount.com/",403,"blocked",{},None)):
            state = {}
            self.assertEqual(retailer_direct.discover(state,env={
                "RETAIL_DIRECT_ENABLED":"true",
            },now=NOW), [])
        self.assertEqual(state["retailer_direct_health"]["Cdiscount"]["status"],
                         "needs_web_unlocker")
        self.assertEqual(state["retailer_direct_health"]["Joybuy"]["status"],
                         "unavailable")

    def test_joybuy_uses_its_own_unambiguous_listing_if_pdp_blocked(self):
        listing = ('<a href="'+JB+'">HyperX Cloud Stinger 2 Casque -44% '
                   '€27.99 RRP €49.99</a>')
        def fetch(url, shop):
            if shop == "Cdiscount":
                return "<html>no product links</html>"
            if url == JB:
                raise HTTPError(url, 403, "blocked", {}, None)
            return listing
        with patch.dict(os.environ, {"BRIGHTDATA_UNLOCKER_ZONE": "",
                                      "BRIGHTDATA_API_TOKEN": ""}), \
             patch.object(retailer_direct, "_fetch_page", side_effect=fetch):
            rows = retailer_direct.discover({},env={
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1",
                "RETAIL_DIRECT_ENABLED":"true",
            },now=NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["direct_source"], "Joybuy Direct")
        self.assertEqual(rows[0]["direct_offer_type"],
                         "RETAILER_LISTING_RRP_UNVERIFIED_PDP")
        self.assertEqual(rows[0]["direct_claimed_percent"], 44)

    def test_cdiscount_blocked_listing_and_pdp_share_daily_budget(self):
        listing = '<a href="'+CD+'">Promo -39% Chaise Bauhaus 149,99€ 90€99</a>'
        def blocked(url, shop):
            if shop == "Cdiscount":
                raise HTTPError(url, 403, "blocked", {}, None)
            return "<html>no Joybuy cards</html>"
        def unlocked(url):
            return html_product("Chaise Bauhaus moderne",90.99,CD) if url == CD else listing
        state = {}
        with patch.dict(os.environ, {
            "BRIGHTDATA_API_TOKEN":"mock-token", "BRIGHTDATA_UNLOCKER_ZONE":"mock-zone",
        }), patch.object(retailer_direct,"_fetch_page",side_effect=blocked), \
             patch.object(retailer_direct,"_fetch_cdiscount_with_provider",
                          side_effect=unlocked) as provider:
            rows = retailer_direct.discover(state,env={
                "RETAIL_DIRECT_ENABLED":"true",
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1",
                "BRIGHTDATA_CDISCOUNT_MAX_DAILY":"2",
            },now=NOW)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["direct_source"],"Cdiscount Direct")
        self.assertEqual(provider.call_count,2)
        self.assertEqual(state["cdiscount_unlocker_usage"]["count"],2)

    def test_cdiscount_blocked_pdp_does_not_overrun_daily_budget(self):
        listing = '<a href="'+CD+'">Promo -39% Chaise Bauhaus 149,99€ 90€99</a>'
        with patch.dict(os.environ, {
            "BRIGHTDATA_API_TOKEN":"mock-token", "BRIGHTDATA_UNLOCKER_ZONE":"mock-zone",
        }), patch.object(retailer_direct,"_fetch_page",
                          side_effect=HTTPError(CD,403,"blocked",{},None)), \
             patch.object(retailer_direct,"_fetch_cdiscount_with_provider",
                          return_value=listing) as provider:
            state={}
            rows=retailer_direct.discover(state,env={
                "RETAIL_DIRECT_ENABLED":"true",
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1",
                "BRIGHTDATA_CDISCOUNT_MAX_DAILY":"1",
            },now=NOW)
        self.assertEqual(rows,[])
        self.assertEqual(provider.call_count,1)
        self.assertEqual(state["cdiscount_unlocker_usage"]["count"],1)

    def test_joybuy_expanded_checks_do_not_expand_cdiscount_budget(self):
        from urllib.parse import urlsplit
        products = [
            "https://www.joybuy.fr/dp/pc-gaming-headset-" + str(i) +
            "/" + str(102762790+i)
            for i in range(9)
        ]
        listing = "".join(
            '<a href="' + item + '">Casque gaming -50% €50 RRP €100</a>'
            for item in products
        )
        calls = []
        def fetch(url, shop):
            calls.append((url, shop))
            if shop == "Cdiscount":
                return "<html>no product cards</html>"
            if url in products:
                return html_product("Casque gaming HyperX",50,url)
            return listing
        with patch.dict(os.environ, {
            "BRIGHTDATA_API_TOKEN":"",
            "BRIGHTDATA_UNLOCKER_ZONE":"",
        }), patch.object(retailer_direct,"_fetch_page",side_effect=fetch):
            state = {}
            results = retailer_direct.discover(state,env={
                "RETAIL_DIRECT_ENABLED":"true",
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"2",
                "RETAIL_JOYBUY_PRODUCT_CHECKS_PER_SCAN":"6",
            },now=NOW)
        self.assertEqual(len(results),6)
        self.assertEqual(len([c for c in calls if c[1]=="Joybuy" and
                              c[0] in products]),6)
        self.assertEqual(state["retailer_joybuy_item_cursor"],6)
        self.assertEqual(state["retailer_direct_health"]["Joybuy"]["offers"],6)

    def test_joybuy_scans_flash_offers_not_unproductive_homepage(self):
        self.assertEqual(len(retailer_direct.PAGES["Joybuy"]),1)
        self.assertIn("/marketing/lightning-offers",retailer_direct.PAGES["Joybuy"][0])

    def test_free_one_shot_can_recheck_joybuy_without_changing_regular_throttle(self):
        listing='<a href="'+JB+'">HyperX Cloud Stinger 2 -44% €27.99 RRP €49.99</a>'
        def fetch(url,shop):
            if shop=="Joybuy":
                return html_product("HyperX Cloud Stinger 2 Casque",27.99,JB) if url==JB else listing
            return "<html>Cdiscount has no products</html>"
        state={"retailer_direct_last_checked":{"Joybuy":NOW.timestamp()}}
        with patch.dict(os.environ, {
            "BRIGHTDATA_API_TOKEN":"",
            "BRIGHTDATA_UNLOCKER_ZONE":"",
        }),patch.object(retailer_direct,"_fetch_page",side_effect=fetch) as calls:
            rows=retailer_direct.discover(state,env={
                "RETAIL_DIRECT_ENABLED":"true",
                "RETAIL_FORCE_JOYBUY_SCAN":"true",
                "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1",
            },now=NOW+timedelta(seconds=30))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["direct_source"],"Joybuy Direct")
        self.assertEqual(len([x for x in calls.call_args_list if x.args[1]=="Joybuy"]),2)

    def test_bounded_direct_scans_and_error_handling(self):
        cdoc=('<a href="'+CD+'">Promo -39% Chaise Bauhaus 149,99€ 90€99</a>')
        jdoc=('<a href="'+JB+'">44% off HyperX Cloud €27.99 RRP €49.99</a>')
        def mock_fetch(url,shop):
            if "lightning-offers" in url or url=="https://www.joybuy.fr/":
                return jdoc
            if url=="https://www.cdiscount.com/":
                return cdoc
            if url==JB:
                return html_product("HyperX Cloud Stinger 2 Casque",27.99,JB)
            if url==CD:
                return html_product("Chaise Bauhaus moderne",90.99,CD)
            raise AssertionError(url)
        state={}
        with patch.object(retailer_direct,"_fetch_page",side_effect=mock_fetch) as fetch:
            rows=retailer_direct.discover(state,env={"RETAIL_DIRECT_ENABLED":"true",
                                                     "RETAIL_PRODUCT_CHECKS_PER_SHOP":"1"},now=NOW)
        self.assertEqual(len(rows),2)
        self.assertEqual(fetch.call_count,4)
        self.assertEqual({r["direct_source"] for r in rows},{"Joybuy Direct","Cdiscount Direct"})
        self.assertEqual(state["retailer_direct_health"]["Joybuy"]["offers"],1)
        with patch.object(retailer_direct,"_fetch_page",side_effect=ValueError("blocked")):
            self.assertEqual(retailer_direct.discover({},now=NOW),[])


if __name__ == "__main__":
    unittest.main()
