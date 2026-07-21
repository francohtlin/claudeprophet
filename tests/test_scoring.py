from __future__ import annotations

import math
import unittest

from backtest.scoring import (
    accuracy,
    base_rate,
    brier_score,
    calibration_bins,
    expected_calibration_error,
    log_loss,
)


class ScoringTests(unittest.TestCase):
    def test_brier_perfect_and_worst(self) -> None:
        self.assertEqual(brier_score([(1.0, 1), (0.0, 0)]), 0.0)
        self.assertEqual(brier_score([(0.0, 1), (1.0, 0)]), 1.0)

    def test_brier_half(self) -> None:
        self.assertAlmostEqual(brier_score([(0.5, 1), (0.5, 0)]), 0.25)

    def test_log_loss_matches_formula(self) -> None:
        pairs = [(0.8, 1), (0.3, 0)]
        expected = -(math.log(0.8) + math.log(0.7)) / 2
        self.assertAlmostEqual(log_loss(pairs), expected)

    def test_log_loss_clips_extremes(self) -> None:
        # A confident wrong prediction should be large but finite, not inf.
        value = log_loss([(1.0, 0)])
        self.assertTrue(value is not None and value > 10 and math.isfinite(value))

    def test_accuracy_threshold(self) -> None:
        self.assertEqual(accuracy([(0.9, 1), (0.4, 0), (0.6, 0)]), 2 / 3)

    def test_base_rate(self) -> None:
        self.assertEqual(base_rate([(0.1, 1), (0.9, 1), (0.5, 0)]), 2 / 3)

    def test_calibration_bins_perfect(self) -> None:
        # 10 predictions at 0.9 where 9 resolve yes -> empirical 0.9 in that bin.
        pairs = [(0.9, 1)] * 9 + [(0.9, 0)]
        rows = calibration_bins(pairs, bins=10)
        top = rows[-1]
        self.assertEqual(top["n"], 10)
        self.assertAlmostEqual(top["mean_predicted"], 0.9)
        self.assertAlmostEqual(top["empirical_frequency"], 0.9)

    def test_ece_zero_when_calibrated(self) -> None:
        pairs = [(0.9, 1)] * 9 + [(0.9, 0)]
        self.assertAlmostEqual(expected_calibration_error(pairs, bins=10), 0.0)

    def test_empty_is_none(self) -> None:
        self.assertIsNone(brier_score([]))
        self.assertIsNone(log_loss([]))


if __name__ == "__main__":
    unittest.main()
