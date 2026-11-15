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
