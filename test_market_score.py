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
        self.assertEqual(result["dimensions"]["technical"][0], None)

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

    def test_market_score_uses_available_benchmarks(self):
        result, _ = score_market({"SP500": {"change_pct": 1.0}})
        self.assertEqual(result, 54.0)


if __name__ == "__main__":
    unittest.main()
