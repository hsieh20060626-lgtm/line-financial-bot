import unittest
from unittest.mock import patch

import pandas as pd

from market_data import fetch_benchmarks


class BenchmarkSourceTests(unittest.TestCase):
    def test_taiwan_benchmarks_do_not_request_unsupported_yahoo_txf(self):
        requested_symbols = []

        class FakeTicker:
            def __init__(self, symbol):
                requested_symbols.append(symbol)

            def history(self, **kwargs):
                index = pd.date_range("2026-11-10", periods=2, freq="D")
                return pd.DataFrame(
                    {
                        "Open": [100.0, 101.0],
                        "High": [101.0, 102.0],
                        "Low": [99.0, 100.0],
                        "Close": [100.0, 101.0],
                        "Volume": [1000, 1200],
                    },
                    index=index,
                )

        with patch("market_data.yf.Ticker", side_effect=FakeTicker):
            result = fetch_benchmarks("TW")

        self.assertIn("TAIEX", result)
        self.assertNotIn("TAIFEX", result)
        self.assertNotIn("TX=F", requested_symbols)


if __name__ == "__main__":
    unittest.main()
