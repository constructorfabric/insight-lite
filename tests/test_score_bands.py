"""The band scale: configurable, validated, and suggestible from the data.

Why it needed changing at all. The score is a weighted mean of percentiles, so its median
is 50 BY CONSTRUCTION. A floor is therefore not only a judgement about a person, it is a
decision about what share of the org carries a label — and the old floors at 45/60/75 put
41% of the banded population in "Building" on a year of production data, against 7% above
75. Nobody chose that; arithmetic did. 30/50/70 puts roughly a tenth at the bottom and a
seventh at the top, with the middle floor pinned to the median that the score defines.

What these tests hold. A scale is only meaningful as a whole: the lowest band starts at 0
and the floors strictly ascend. A scale that does not ascend is not a milder scale, it is a
broken one — _score_band walks it from the top and would hand back the wrong label — so the
resolver falls back WHOLE rather than per key, and the writer refuses it outright.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import store  # noqa: E402


def _cfg(bands):
    """store._score_band_floors reads the merged config, so a test overrides that."""
    return patch("configstore.apply_overlay", return_value={"developer_score_bands": bands})


class BandScaleTest(unittest.TestCase):
    def test_the_default_scale_is_the_one_that_was_chosen(self):
        self.assertEqual(store._score_band_floors(),
                         {"Building": 0, "Developing": 30, "Solid": 50, "Strong": 70})

    def test_the_middle_floor_is_the_median_the_score_defines(self):
        """Not a coincidence worth losing: the score's median is 50 by construction, so this
        boundary documents itself as "above or below the middle of the team"."""
        self.assertEqual(store._score_band_floors()["Solid"], 50)

    def test_labels_change_exactly_at_their_floor(self):
        for v, want in ((0, "Building"), (29, "Building"), (30, "Developing"),
                        (49, "Developing"), (50, "Solid"), (69, "Solid"),
                        (70, "Strong"), (100, "Strong")):
            self.assertEqual(store._score_band(v)[0], want, f"score {v}")

    def test_no_score_has_no_band_rather_than_the_lowest_one(self):
        self.assertEqual(store._score_band(None), ("—", "na"))

    def test_a_configured_scale_is_honoured(self):
        with _cfg({"Developing": 20, "Strong": 80}):
            self.assertEqual(store._score_band_floors(),
                             {"Building": 0, "Developing": 20, "Solid": 50, "Strong": 80})
            self.assertEqual(store._score_band(25)[0], "Developing")
            self.assertEqual(store._score_band(75)[0], "Solid")

    def test_a_scale_that_does_not_ascend_falls_back_whole(self):
        # Solid under Developing: keeping the good keys and dropping the bad one would leave
        # a scale nobody configured, so the whole thing reverts.
        with _cfg({"Developing": 60, "Solid": 40}):
            self.assertEqual(store._score_band_floors()["Developing"], 30)
            self.assertEqual(store._score_band_floors()["Solid"], 50)

    def test_the_lowest_band_cannot_start_above_zero(self):
        with _cfg({"Building": 5}):
            self.assertEqual(store._score_band_floors()["Building"], 0)

    def test_equal_floors_are_rejected_too(self):
        # Two bands sharing a floor makes one of them unreachable.
        with _cfg({"Developing": 50}):
            self.assertEqual(store._score_band_floors()["Developing"], 30)

    def test_garbage_in_one_key_does_not_take_the_scale_with_it(self):
        with _cfg({"Strong": "eighty"}):
            self.assertEqual(store._score_band_floors()["Strong"], 70)

    def test_the_spec_the_client_draws_matches_the_labels_it_reads(self):
        spec = store.score_band_spec()
        floors = store._score_band_floors()
        self.assertEqual([s["min"] for s in spec], sorted(s["min"] for s in spec),
                         "a client drawing this expects it ascending")
        for s in spec:
            self.assertEqual(s["min"], floors[s["band"]])
            # the boundary itself must carry the band the spec names
            self.assertEqual(store._score_band(s["min"])[0], s["band"])


class BandSuggestionTest(unittest.TestCase):
    """suggest_score_bands is a SUGGESTION and stays one. Pinning floors to quantiles every
    window would make a label move when the team moves, on top of the score already doing
    so; a human accepts a scale and then it holds still."""

    def test_it_declines_rather_than_guessing_from_a_handful(self):
        with patch.object(store, "developer_scores", return_value={
                "active_pillars": ["engagement"],
                "board": [{"score": s, "pillars": {"engagement": s}} for s in range(4)]}):
            self.assertIsNone(store.suggest_score_bands(None, "a", "b"))

    def test_it_ascends_even_when_the_distribution_is_flat(self):
        # Everyone on the same score collapses every quantile onto one value; the result
        # still has to be a scale _score_band can walk.
        with patch.object(store, "developer_scores", return_value={
                "active_pillars": ["engagement"],
                "board": [{"score": 50, "pillars": {"engagement": 50}} for _ in range(20)]}):
            got = store.suggest_score_bands(None, "a", "b")
        floors = [got[b] for _, b, _ in store._SCORE_BANDS]
        self.assertEqual(floors[0], 0)
        self.assertEqual(floors, sorted(set(floors)), f"not a walkable scale: {floors}")

    def test_it_ignores_people_who_would_not_be_banded_anyway(self):
        """A missing pillar counts as zero in the score, and those rows are left unbanded in
        the UI. Letting them into the quantiles would drag the bottom floor down to make
        room for a data gap."""
        full = [{"score": 50 + i, "pillars": {"engagement": 1, "flow": 1}} for i in range(20)]
        thin = [{"score": 2, "pillars": {"engagement": 1, "flow": None}} for _ in range(20)]
        with patch.object(store, "developer_scores", return_value={
                "active_pillars": ["engagement", "flow"], "board": full + thin}):
            got = store.suggest_score_bands(None, "a", "b")
        self.assertGreater(got["Developing"], 40,
                           "the bottom floor followed the unbanded rows down")


if __name__ == "__main__":
    unittest.main()
