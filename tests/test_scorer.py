"""Tests for the transport-event scorer.

Standard library unittest, so `python -m unittest discover` works on a clean
checkout with nothing installed.

These are mostly invariant tests rather than golden-value tests. Golden values
would pin the placeholder coefficients, which is exactly the thing that should
be free to change; the invariants are what has to hold for the score to mean
anything.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from scorer import (  # noqa: E402
    CloudLabel,
    Level,
    Window,
    breakthrough_probability,
    classify_cloud,
    score,
)


def profile(peaks: list[tuple[float, float]], wind_dir: float = 200.0) -> tuple[Level, ...]:
    """Build a profile from (height, backscatter) pairs."""
    return tuple(
        Level(height_m=h, backscatter=b, wind_dir_deg=wind_dir, wind_speed_ms=5.0)
        for h, b in peaks
    )


def window(**kw) -> Window:
    base = dict(
        site="NTU",
        start_hour_sgt=14.0,
        profile=profile([(200, 1.0), (600, 1.2), (1000, 3.0), (1400, 1.1)]),
        pm25_ugm3=30.0,
        upwind_fire_count=5,
        upwind_fire_bearing_deg=200.0,
        boundary_layer_height_m=900.0,
    )
    base.update(kw)
    return Window(**base)


class TestInvariants(unittest.TestCase):
    def test_every_component_is_a_probability(self):
        for hour in (0.0, 6.0, 14.0, 23.0):
            s = score(window(start_hour_sgt=hour))
            for name, value in s.components.items():
                self.assertGreaterEqual(value, 0.0, name)
                self.assertLessEqual(value, 1.0, name)
            self.assertGreaterEqual(s.total, 0.0)
            self.assertLessEqual(s.total, 1.0)

    def test_weights_sum_to_one(self):
        from scorer import WEIGHTS

        self.assertAlmostEqual(sum(WEIGHTS.values()), 1.0, places=9)

    def test_scorer_is_pure(self):
        """Same window in, same score out. This is what makes replay valid."""
        w = window()
        self.assertEqual(score(w).total, score(w).total)

    def test_empty_profile_does_not_crash(self):
        s = score(window(profile=()))
        self.assertEqual(s.components["aloft_enhancement"], 0.0)
        self.assertGreaterEqual(s.total, 0.0)


class TestCloudClassifier(unittest.TestCase):
    def test_saturating_return_is_opaque_at_stage_1(self):
        v = classify_cloud(profile([(200, 1.0), (800, 12.0), (1200, 1.0)]))
        self.assertIs(v.label, CloudLabel.OPAQUE)
        self.assertEqual(v.stage_failed, 1)

    def test_sharp_edge_is_caught_at_stage_2(self):
        v = classify_cloud(profile([(200, 0.5), (800, 5.0), (1200, 0.5)]))
        self.assertIs(v.label, CloudLabel.THIN)
        self.assertEqual(v.stage_failed, 2)

    def test_smooth_aerosol_layer_is_clear(self):
        v = classify_cloud(profile([(200, 1.0), (600, 1.4), (1000, 1.8), (1400, 1.3)]))
        self.assertIs(v.label, CloudLabel.CLEAR)
        self.assertIsNone(v.stage_failed)

    def test_opaque_cloud_blocks_the_event(self):
        s = score(window(profile=profile([(200, 1.0), (800, 20.0), (1200, 1.0)])))
        self.assertFalse(s.is_event)


class TestBreakthrough(unittest.TestCase):
    def test_night_is_lower_than_afternoon(self):
        night = breakthrough_probability(3.0, 1200.0, 400.0)
        day = breakthrough_probability(14.0, 1200.0, 400.0)
        self.assertLess(night, day)

    def test_layer_below_boundary_layer_is_high(self):
        self.assertGreaterEqual(breakthrough_probability(14.0, 600.0, 1200.0), 0.75)

    def test_degenerate_input_is_zero(self):
        self.assertEqual(breakthrough_probability(14.0, 0.0, 1000.0), 0.0)
        self.assertEqual(breakthrough_probability(14.0, 1000.0, 0.0), 0.0)


class TestDirectionality(unittest.TestCase):
    def test_wind_from_the_fires_scores_higher_than_wind_across(self):
        aligned = score(window(profile=profile([(1000, 3.0)], wind_dir=200.0)))
        crosswise = score(window(profile=profile([(1000, 3.0)], wind_dir=290.0)))
        self.assertGreater(
            aligned.components["wind_alignment"],
            crosswise.components["wind_alignment"],
        )

    def test_no_fires_means_no_wind_alignment(self):
        s = score(window(upwind_fire_count=0))
        self.assertEqual(s.components["wind_alignment"], 0.0)
        self.assertEqual(s.components["fire_upwind"], 0.0)

    def test_bearing_wraps_around_north(self):
        """350 and 010 are 20 degrees apart, not 340."""
        a = score(window(profile=profile([(1000, 3.0)], wind_dir=350.0),
                         upwind_fire_bearing_deg=10.0))
        b = score(window(profile=profile([(1000, 3.0)], wind_dir=350.0),
                         upwind_fire_bearing_deg=170.0))
        self.assertGreater(a.components["wind_alignment"], b.components["wind_alignment"])


if __name__ == "__main__":
    unittest.main()
