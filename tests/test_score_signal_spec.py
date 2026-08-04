"""score_signal_spec() is the ONE description of the score's inputs the UI is allowed
to use, so it must not be able to disagree with the model it describes.

The direction of a signal — whether higher or lower is better — lives nowhere else that
a client can reach. The metric registry (store._m) carries a unit and a group but no
direction, so a UI that wants to draw "you: 20.3, team: 5.2, and that is bad" has only
two options: hardcode its own copy of the mapping, or be told. It was hardcoded in prose
inside PersonScore's ScoreChain, which is how a UI ends up describing a model that has
since moved. These tests pin the spec to _SCORE_SIGNALS so the drift cannot happen
quietly, and they are cheap because both are plain data.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import store  # noqa: E402


class ScoreSignalSpecTest(unittest.TestCase):
    def setUp(self):
        self.spec = store.score_signal_spec()

    def test_it_describes_every_signal_and_nothing_else(self):
        self.assertEqual({(s["pillar"], s["key"]) for s in self.spec},
                         {(p, k) for p, k, _ in store._SCORE_SIGNALS},
                         "a signal the model scores but the spec omits is invisible in the UI, "
                         "and one the spec invents has no value to show")

    def test_direction_matches_the_model(self):
        want = {k: d > 0 for _, k, d in store._SCORE_SIGNALS}
        got = {s["key"]: s["higher_is_better"] for s in self.spec}
        self.assertEqual(got, want,
                         "a flipped direction paints an improvement red, which is worse "
                         "than showing nothing")

    def test_every_signal_has_a_label_and_a_known_format(self):
        known = {"int", "f1", "f2", "f3", "hours", "pct01"}
        for s in self.spec:
            self.assertTrue(s["label"].strip(), f"{s['key']} has no label")
            self.assertIn(s["fmt"], known, f"{s['key']} has an unrenderable fmt")

    def test_labels_are_distinct(self):
        labels = [s["label"] for s in self.spec]
        self.assertEqual(len(labels), len(set(labels)),
                         "two factors with the same label are indistinguishable in a drill")

    def test_pillars_come_heaviest_first_and_group_together(self):
        """The client renders in payload order, so the order IS the layout. Pillars must
        arrive by descending weight and a pillar's signals must not be interleaved."""
        seen, order = [], []
        for s in self.spec:
            if not order or order[-1] != s["pillar"]:
                self.assertNotIn(s["pillar"], seen, f"{s['pillar']} appears in two runs")
                seen.append(s["pillar"])
                order.append(s["pillar"])
        weights = [store._SCORE_WEIGHTS[p] for p in order]
        self.assertEqual(weights, sorted(weights, reverse=True),
                         f"pillars out of weight order: {order}")

    def test_the_meta_table_covers_exactly_the_signal_keys(self):
        self.assertEqual(set(store._SCORE_SIGNAL_META),
                         {k for _, k, _ in store._SCORE_SIGNALS},
                         "_SCORE_SIGNAL_META and _SCORE_SIGNALS describe the same ten keys")


if __name__ == "__main__":
    unittest.main()
