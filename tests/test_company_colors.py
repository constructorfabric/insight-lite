"""A company's chart colour must not depend on its rank.

It used to. Palette entries were handed out in descending-commit order, so two companies
swapping places swapped colours — and these charts are read week over week, which makes
that a quiet correctness bug rather than a cosmetic one. It hid for a long time because
three company names were pinned by name in the source, and the rank fallback only ever
caught strangers; de-hardcoding the names for open sourcing removed the pins and exposed
it. The report also had THREE separate palettes, so one company could be purple in the
contributors chart and amber in the company table of the same report.

These tests pin the properties, not the specific hues: derived from the name, independent
of input order, no duplicates within a chart, pins win, and "Other" stays grey.
"""
import unittest

import store


NAMES = ["Acme Corp", "Globex", "Initech", "Umbrella", "Other"]


class DerivedFromNameTest(unittest.TestCase):
    def test_input_order_does_not_change_any_colour(self):
        """The rank bug in one line: feed the same companies in a different order and
        the old rule produced a different mapping."""
        forward = store.company_color_map(NAMES, pinned={})
        backward = store.company_color_map(list(reversed(NAMES)), pinned={})
        self.assertEqual(forward, backward)

    def test_a_company_keeps_its_colour_when_others_come_and_go(self):
        """Adding or removing a non-colliding company must not repaint the rest."""
        base = store.company_color_map(["Acme Corp", "Globex"], pinned={})
        grown = store.company_color_map(["Acme Corp", "Globex", "Initech"], pinned={})
        self.assertEqual(base["Acme Corp"], grown["Acme Corp"])
        self.assertEqual(base["Globex"], grown["Globex"])

    def test_it_is_deterministic_and_not_python_hash(self):
        """hash() is randomised per process unless PYTHONHASHSEED is set, so using it
        would repaint every chart on restart. Pinning the computed index proves the
        implementation uses its own hash."""
        expected = store.COMPANY_PALETTE[store._name_hash("Globex")
                                         % len(store.COMPANY_PALETTE)]
        self.assertEqual(store.company_color_map(["Globex"], pinned={})["Globex"],
                         expected)

    def test_case_and_whitespace_do_not_produce_a_second_colour(self):
        a = store.company_color_map([" globex "], pinned={})[" globex "]
        b = store.company_color_map(["Globex"], pinned={})["Globex"]
        self.assertEqual(a, b)

    def test_every_company_gets_a_colour(self):
        m = store.company_color_map(NAMES, pinned={})
        self.assertEqual(sorted(m), sorted(NAMES))
        for name, colour in m.items():
            self.assertRegex(colour, r"^#[0-9a-fA-F]{6}$", f"{name} -> {colour!r}")


class NoDuplicatesTest(unittest.TestCase):
    def test_a_set_within_the_palette_gets_distinct_colours(self):
        """Two companies sharing a colour in one chart is the failure this trades a
        little stability for: collisions resolve to the next free slot."""
        names = [f"Company {c}" for c in "ABCDEFGH"][:len(store.COMPANY_PALETTE)]
        m = store.company_color_map(names, pinned={})
        self.assertEqual(len(set(m.values())), len(names), f"duplicate colours in {m}")

    def test_collision_resolution_does_not_depend_on_input_order(self):
        names = [f"Company {c}" for c in "ABCDEFGH"]
        self.assertEqual(store.company_color_map(names, pinned={}),
                         store.company_color_map(list(reversed(names)), pinned={}))

    def test_more_companies_than_colours_still_assigns_all(self):
        names = [f"Co {i}" for i in range(len(store.COMPANY_PALETTE) * 2)]
        m = store.company_color_map(names, pinned={})
        self.assertEqual(len(m), len(names))


class PinsTest(unittest.TestCase):
    def test_a_pin_wins_over_the_generated_colour(self):
        generated = store.company_color_map(["Globex"], pinned={})["Globex"]
        pinned = store.company_color_map(["Globex"], pinned={"Globex": "#123456"})
        self.assertEqual(pinned["Globex"], "#123456")
        self.assertNotEqual(pinned["Globex"], generated)

    def test_pinning_one_company_does_not_repaint_the_others(self):
        plain = store.company_color_map(["Acme Corp", "Globex"], pinned={})
        with_pin = store.company_color_map(["Acme Corp", "Globex"],
                                           pinned={"Acme Corp": "#123456"})
        self.assertEqual(with_pin["Globex"], plain["Globex"])

    def test_other_is_grey_and_never_generated(self):
        self.assertEqual(store.company_color_map(["Other"], pinned={})["Other"],
                         store.OTHER_COMPANY_COLOR)

    def test_other_can_still_be_pinned_deliberately(self):
        m = store.company_color_map(["Other"], pinned={"Other": "#000000"})
        self.assertEqual(m["Other"], "#000000")

    def test_pinned_colors_reads_the_config(self):
        """The config path, so `companies.colors` is actually wired and not just
        documented."""
        import ghclient
        from unittest.mock import patch
        cfg = {"companies": {"colors": {"Globex": "#abcdef", "": "#ignored",
                                        "Blank": ""}}}
        with patch.object(ghclient, "load_config", return_value=cfg):
            self.assertEqual(store.pinned_company_colors(), {"Globex": "#abcdef"})


class OneSourceTest(unittest.TestCase):
    """The report used to colour companies in four places with three palettes."""

    def test_the_report_and_the_identity_screen_agree(self):
        import directory
        roster = {"a": {"company": "Globex", "commits": 5},
                  "b": {"company": "Acme Corp", "commits": 500}}
        dots = directory._company_colors(roster)
        direct = store.company_color_map({"Globex", "Acme Corp"})
        self.assertEqual(dots, direct)

    def test_the_identity_screen_does_not_rank_by_commits(self):
        """It reimplemented the rank rule locally, so its dots agreed with the report
        only while both happened to rank companies the same way."""
        low_first = {"a": {"company": "Globex", "commits": 1},
                     "b": {"company": "Acme Corp", "commits": 999}}
        high_first = {"a": {"company": "Globex", "commits": 999},
                      "b": {"company": "Acme Corp", "commits": 1}}
        import directory
        self.assertEqual(directory._company_colors(low_first),
                         directory._company_colors(high_first))


if __name__ == "__main__":
    unittest.main()
