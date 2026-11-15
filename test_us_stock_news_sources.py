import unittest
from unittest.mock import patch

import news


class USStockNewsSourceTests(unittest.TestCase):
    def test_us_queries_cover_reuters_yahoo_cnbc_bloomberg(self):
        queries = news._us_news_queries("NVDA", "NVIDIA")
        joined = "\n".join(queries)
        for domain in ("reuters.com", "finance.yahoo.com", "cnbc.com", "bloomberg.com"):
            self.assertIn(domain, joined)
        self.assertIn('"NVDA"', joined)
        self.assertIn('"NVIDIA"', joined)
        self.assertTrue(all("when:7d" in query for query in queries))

    @patch("news._fetch_search_items")
    def test_us_news_labels_sources_and_deduplicates(self, fetch):
        fetch.side_effect = [
            [{"title": "NVIDIA earnings beat", "link": "https://reuters.com/a",
              "published": "2026-11-15 10:00", "source": "Google News / Reuters"}],
            [{"title": "NVIDIA earnings beat", "link": "https://finance.yahoo.com/a",
              "published": "2026-11-15 10:00", "source": "Google News / Yahoo Finance"},
             {"title": "NVIDIA launches product", "link": "https://finance.yahoo.com/b",
              "published": "2026-11-15 09:00", "source": "Google News / Yahoo Finance"}],
            [],
            [],
        ]
        items = news.fetch_symbol_news("NVDA", market="US")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0]["source"], "Google News / Reuters")
        self.assertEqual(items[1]["source"], "Google News / Yahoo Finance")
        self.assertEqual(fetch.call_count, 4)

    @patch("news._fetch_search_items")
    def test_taiwan_news_uses_taiwan_queries(self, fetch):
        fetch.return_value = []
        self.assertEqual(news.fetch_symbol_news("2330", market="TW"), [])
        queries = [call.args[0] for call in fetch.call_args_list]
        self.assertTrue(all("台股" in query or "股價" in query for query in queries))
        self.assertEqual(len(queries), 2)


if __name__ == "__main__":
    unittest.main()
