import unittest

from market_analysis import recommendation_for_position


class PositionRecommendationTests(unittest.TestCase):
    def test_held_position_returns_three_support_scores_totaling_100(self):
        result = recommendation_for_position(
            {"score": 72.0, "data_completeness": 85.0}, "held"
        )
        self.assertEqual(result["action"], "偏向加碼／買進")
        self.assertEqual(set(result["scores"]), {"加碼／買進", "持有", "減碼／賣出"})
        self.assertEqual(sum(result["scores"].values()), 100)

    def test_unheld_position_returns_entry_support_scores_totaling_100(self):
        result = recommendation_for_position(
            {"score": 50.0, "data_completeness": 80.0}, "unheld"
        )
        self.assertEqual(result["action"], "偏向等待觀望")
        self.assertEqual(set(result["scores"]), {"買進", "等待觀望", "暫不買進"})
        self.assertEqual(sum(result["scores"].values()), 100)

    def test_middle_score_does_not_create_false_100_percent_hold_support(self):
        result = recommendation_for_position(
            {"score": 56.9, "data_completeness": 81.0,
             "dimensions": {"technical": (48.4, []), "market": (49.9, []),
                            "chips": (75.0, []), "news": (None, [])}}, "held"
        )
        self.assertEqual(sum(result["scores"].values()), 100)
        self.assertLess(max(result["scores"].values()), 100)
        self.assertEqual(result["action"], "偏向持有")

    def test_weak_technical_structure_reduces_aggressive_support(self):
        baseline = recommendation_for_position(
            {"score": 70.0, "data_completeness": 80.0, "dimensions": {}}, "unheld"
        )
        weak = recommendation_for_position(
            {"score": 70.0, "data_completeness": 80.0,
             "dimensions": {"technical": (20.0, []), "market": (50.0, [])}}, "unheld"
        )
        self.assertLess(weak["adjusted_score"], baseline["adjusted_score"])
        self.assertLess(weak["scores"]["買進"], baseline["scores"]["買進"])
    def test_low_data_completeness_suppresses_action_scores(self):
        result = recommendation_for_position(
            {"score": 90.0, "data_completeness": 25.0}, "unheld"
        )
        self.assertEqual(result["action"], "資料不足，暫不提供操作方向")
        self.assertEqual(result["scores"], {})

    def test_missing_market_score_suppresses_action_scores(self):
        result = recommendation_for_position(
            {"score": None, "data_completeness": 100.0}, "held"
        )
        self.assertEqual(result["scores"], {})


if __name__ == "__main__":
    unittest.main()
