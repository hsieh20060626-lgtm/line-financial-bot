import unittest

import numpy as np
import pandas as pd

from indicators import (
    analyze_technical_indicators,
    calculate_daily_indicators,
    calculate_weekly_indicators,
    resample_weekly,
)


def sample_ohlcv(periods=180):
    dates = pd.bdate_range("2025-01-01", periods=periods)
    close = pd.Series(np.linspace(50, 100, periods), index=dates)
    return pd.DataFrame({
        "Open": close - 0.5,
        "High": close + 1.0,
        "Low": close - 1.0,
        "Close": close,
        "Volume": np.full(periods, 100_000),
    }, index=dates)


class TechnicalIndicatorTests(unittest.TestCase):
    def test_daily_indicators_return_expected_fields(self):
        result = calculate_daily_indicators(sample_ohlcv())
        self.assertEqual(result["timeframe"], "daily")
        self.assertTrue(result["data_sufficient"])
        self.assertIsNotNone(result["moving_averages"]["MA20"])
        self.assertIsNotNone(result["rsi14"])
        self.assertIsNotNone(result["macd"])

    def test_weekly_resampling_and_indicators(self):
        source = sample_ohlcv()
        weekly = resample_weekly(source)
        result = calculate_weekly_indicators(source)
        self.assertGreater(len(weekly), 20)
        self.assertEqual(result["timeframe"], "weekly")
        self.assertIsNotNone(result["moving_averages"]["MA5"])

    def test_combined_result_contains_timeframe_comparison(self):
        result = analyze_technical_indicators(sample_ohlcv())
        self.assertIn("daily", result)
        self.assertIn("weekly", result)
        self.assertIn("timeframe_comparison", result)
        self.assertIn("interpretation", result["timeframe_comparison"])

    def test_empty_input_is_rejected(self):
        with self.assertRaises(ValueError):
            calculate_daily_indicators(pd.DataFrame())


if __name__ == "__main__":
    unittest.main()
