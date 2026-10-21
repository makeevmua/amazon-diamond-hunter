"""Bright Data search itself becomes an independent Amazon discovery source."""
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import brightdata_search
import bot
import catalog
import direct_pipeline
import fast_hunter
import brightdata_verify

NOW=datetime(2026,10,21,12,tzinfo=timezone.utc)


def example():
    return {
        "asin":"B0B9C4DKKG",
        "url":"https://www.amazon.fr/dp/B0B9C4DKKG/ref=sr_1_2",
        "name":"Samsung SSD 990 Pro NVMe 2To",
        "initial_price":149.99,
        "final_price":49.99,
        "currency":"EUR",
        "timestamp":"2026-10-21",
        "image":"https://m.media-amazon.com/images/I/ssd.jpg",
    }


class BrightSearchTests(unittest.TestCase):
    def test_proper_european_search_result_without_amazon_partners(self):
        result=brightdata_search.normalize(example(),NOW)
        self.assertEqual(result["link"],"https://amazon.fr/dp/B0B9C4DKKG")
        self.assertEqual(result["direct_source"],"Bright Data Amazon Search")
        self.assertEqual(result["direct_current_eur"],49.99)
        self.assertGreater(result["direct_claimed_percent"],60)
        deal=direct_pipeline.to_candidate(result,bot.category_matches)
        self.assertIsNotNone(deal)
        self.assertIn("AMAZON SEARCH",direct_pipeline.format_direct_alert(deal))
        self.assertIn("НЕ историческая",direct_pipeline.format_direct_alert(deal))
        rows=catalog.collect_records([result],[deal],bot.category_matches,
                    bot.percent,bot.price_pair,lambda x:None,lambda x:False,
                    lambda x:True,bot.find_amazon_link)
        self.assertEqual(rows[0]["price_scope"],"brightdata_search_listing")
        self.assertEqual(rows[0]["advertised_price_eur"],49.99)

    def test_product_filters_and_stale_snapshots(self):
        for title in ("Xbox Series X","Samsung Galaxy S26","Coque Galaxy S25"):
            self.assertIsNone(brightdata_search.normalize({**example(),"name":title},NOW))
        self.assertIsNone(brightdata_search.normalize({**example(),"currency":"USD"},NOW))
        self.assertIsNone(brightdata_search.normalize(
             {**example(),"url":"https://www.amazon.com/dp/B0B9C4DKKG"},NOW))
        self.assertIsNone(brightdata_search.normalize(
             {**example(),"timestamp":"2026-10-10"},NOW))
        self.assertIsNone(brightdata_search.normalize(
             {**example(),"asin":"B0B9C4DKKH"},NOW))
        self.assertIsNone(brightdata_search.normalize(
             {**example(),"final_price":None,"initial_price":None},NOW))

    def test_free_quota_and_daily_limit(self):
        state={}
        env={"BRIGHTDATA_API_TOKEN":"mock-token",
             "BRIGHTDATA_SEARCH_PER_DAY":"1"}
        self.assertEqual(brightdata_search.discover({},env={},now=NOW),[])
        with patch.object(brightdata_search,"_read_search",
                          return_value=[example()]) as get:
            first=brightdata_search.discover(state,env=env,now=NOW)
            next_scan=brightdata_search.discover(state,env=env,now=NOW)
        self.assertEqual(len(first),1)
        self.assertEqual(next_scan,[])
        get.assert_called_once()
        self.assertEqual(state["brightdata_search_usage"]["count"],1)
        self.assertEqual(state["brightdata_search_cursor"],1)

    def test_async_search_queues_one_job_then_retrieves_without_extra_charge(self):
        sample=example()
        state={}
        env={"BRIGHTDATA_API_TOKEN":"fake-only-in-mocks",
             "BRIGHTDATA_SEARCH_PER_DAY":"1"}
        with patch.object(brightdata_search,"_read_search",
                          return_value={"snapshot_id":"s_abc123456789"}) as trigger, \
             patch.object(brightdata_search,"_poll_snapshot",
                          side_effect=[None,[sample]]) as poll:
            one=brightdata_search.discover(state,env=env,now=NOW)
            two=brightdata_search.discover(state,env=env,now=NOW)
            three=brightdata_search.discover(state,env=env,now=NOW)
        self.assertEqual((len(one),len(two),len(three)),(0,0,1))
        self.assertEqual(trigger.call_count,1)
        self.assertEqual(poll.call_count,2)
        self.assertNotIn("brightdata_search_pending",state)
        self.assertEqual(state["brightdata_search_usage"]["count"],1)
        self.assertEqual(three[0]["direct_current_eur"],49.99)

    def test_brightdata_trigger_uses_top_level_input_array(self):
        import json
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,n=1000000):
                return b'{"snapshot_id":"s_abc123456789"}'
        with patch.object(brightdata_search,"urlopen",
                          return_value=Response()) as get:
            result=brightdata_search._read_search("fake-token","amazon.fr","SSD NVMe")
        req=get.call_args.args[0]
        self.assertIn("/datasets/v3/trigger?",req.full_url)
        self.assertEqual(req.get_method(),"POST")
        payload=json.loads(req.data)
        self.assertIsInstance(payload,list)
        self.assertEqual(payload[0]["keyword"],"SSD NVMe")
        self.assertEqual(payload[0]["url"],"https://www.amazon.fr")
        self.assertEqual(result["snapshot_id"],"s_abc123456789")

    def test_no_key_worker_can_use_search_provider_without_keepa(self):
        record=brightdata_search.normalize(example(),NOW)
        calls=[]
        with patch.object(fast_hunter.amazon_direct,"enabled",return_value=False), \
             patch.object(fast_hunter.keepa_discovery,"discover",return_value=[]), \
             patch.object(fast_hunter.brightdata_search,"discover",return_value=[record]), \
             patch.object(fast_hunter.brightdata_verify,"verify",return_value={}):
            sent=fast_hunter.scan({},lambda msg,buttons,image:calls.append(msg))
        self.assertEqual(sent,1)
        self.assertIn("AMAZON SEARCH",calls[0])


if __name__=="__main__":
    unittest.main()
