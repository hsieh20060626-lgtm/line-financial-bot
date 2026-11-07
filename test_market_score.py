import unittest

from market_score import calculate_market_score, score_chips, score_fundamental, score_market


class MarketScoreTests(unittest.TestCase):
    def test_missing_dimensions_are_not_zero_and_weights_renormalize(self):
        result = calculate_market_score(
            technical={}, fundamental={"available": False},
            chips={"available": False},
            market={"SP500": {"change_pct": 1.0}},
            news=[]
        )
        self.assertIsNotNone(result["score"])
        self.assertEqual(result["data_completeness"], 15.0)
        self.assertEqual(result["effective_weights"], {"market": 1.0})
        self.assertIsNone(result["dimensions"]["technical"][0])

    def test_not_applicable_dimensions_are_excluded_from_coverage_denominator(self):
        result = calculate_market_score(
            technical={}, fundamental={"available": False, "not_applicable": True,
                                       "reason": "ETF 不適用一般公司 EPS／PER"},
            chips={"available": False},
            market={"SP500": {"change_pct": 1.0}},
            news=[{"title": "recent news"}],
            news_score=60,
        )
        self.assertEqual(result["applicable_weight"], 80.0)
        self.assertEqual(result["scored_weight"], 30.0)
        self.assertEqual(result["data_completeness"], 37.5)
        self.assertIn("fundamental", result["not_applicable_dimensions"])
        self.assertNotIn("fundamental", result["missing_dimensions"])

    def test_us_volume_price_proxy_scores_without_claiming_institutional_flow(self):
        score, reasons = score_chips({
            "available": True, "market": "US", "method": "volume_price_proxy",
            "price_change_pct": 1.0, "volume_ratio": 1.5,
        })
        self.assertEqual(score, 56.0)
        self.assertTrue(any("不是機構持倉" in reason for reason in reasons))

    def test_missing_us_proxy_is_missing_not_zero(self):
        score, _ = score_chips({
            "available": False, "market": "US", "method": "volume_price_proxy",
            "price_change_pct": None, "volume_ratio": None,
        })
        self.assertIsNone(score)

    def test_no_dimensions_stops_scoring(self):
        result = calculate_market_score({}, {}, {}, {}, [])
        self.assertIsNone(result["score"])
        self.assertEqual(result["signal"], "資料不足，停止評分")

    def test_taiwan_chips_requires_valid_volume(self):
        result, reasons = score_chips({
            "available": True, "foreign_net": 1000, "trust_net": -200,
            "dealer_net": 0, "volume": float("nan")
        })
        self.assertIsNone(result)
        self.assertTrue(reasons)

    def test_fundamental_missing_values_are_not_zero(self):
        result, _ = score_fundamental({"available": False})
        self.assertIsNone(result)

    def test_fundamental_not_applicable_is_not_scored(self):
        result, reasons = score_fundamental({
            "available": False, "not_applicable": True,
            "reason": "ETF 不適用一般公司 EPS／PER"
        })
        self.assertIsNone(result)
        self.assertIn("ETF", reasons[0])

    def test_market_score_uses_available_benchmarks(self):
        result, _ = score_market({"SP500": {"change_pct": 1.0}})
        self.assertEqual(result, 54.0)

    def test_news_is_not_scored_without_ai_sentiment(self):
        result = calculate_market_score({}, {}, {}, {}, [{"title": "recent news"}])
        self.assertIsNone(result["dimensions"]["news"][0])

    def test_news_uses_ai_sentiment_when_available(self):
        result = calculate_market_score({}, {}, {}, {}, [{"title": "recent news"}], news_score=72)
        self.assertEqual(result["dimensions"]["news"][0], 72.0)
        self.assertIn("news", result["effective_weights"])


if __name__ == "__main__":
    unittest.main()
