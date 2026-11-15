import unittest

from market_analysis import recommendation_for_position


class PositionRecommendationTests(unittest.TestCase):
    def test_held_position_returns_three_support_scores_totaling_100(self):
        result = recommendation_for_position(
            {"score": 72.0, "data_completeness": 85.0}, "held"
        )
        self.assertEqual(result["action"], "較明確偏向加碼／買進")
        self.assertEqual(set(result["scores"]), {"加碼／買進", "持有", "減碼／賣出"})
        self.assertEqual(sum(result["scores"].values()), 100)

    def test_unheld_position_returns_entry_support_scores_totaling_100(self):
        result = recommendation_for_position(
            {"score": 50.0, "data_completeness": 80.0}, "unheld"
        )
        self.assertEqual(result["action"], "較明確偏向等待觀望")
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
        self.assertEqual(result["action"], "較明確偏向持有")

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
    def test_primary_action_follows_top_support_when_scores_are_close(self):
        # Reproduces the user's 0050 report: buy leads hold by only a few points.
        result = recommendation_for_position(
            {"score": 61.7, "data_completeness": 100.0,
             "dimensions": {"technical": (44.6, []), "fundamental": (77.3, []),
                            "chips": (80.0, []), "market": (47.4, []),
                            "news": (65.0, [])}}, "held"
        )
        scores = result["scores"]
        top_two = sorted(scores.values(), reverse=True)[:2]
        self.assertGreater(scores["加碼／買進"], scores["持有"])
        self.assertEqual(result["action"], "中性偏向加碼／買進")
        self.assertLess(top_two[0] - top_two[1], 8)
        self.assertIn("領先第二名", result["reason"])

    def test_action_direction_always_matches_highest_support(self):
        cases = [
            ({"score": 72.0, "data_completeness": 100.0}, "held", "加碼／買進"),
            ({"score": 50.0, "data_completeness": 100.0}, "unheld", "等待觀望"),
            ({"score": 25.0, "data_completeness": 100.0}, "held", "減碼／賣出"),
        ]
        for score, position, expected_direction in cases:
            with self.subTest(position=position, total=score["score"]):
                result = recommendation_for_position(score, position)
                self.assertEqual(max(result["scores"], key=result["scores"].get), expected_direction)
                self.assertIn(expected_direction, result["action"])

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
