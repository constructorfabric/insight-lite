"""What the server sends a chart, now that it sends DATA rather than a Vega-Lite spec.

This replaces tests/test_vega_spec.py one behaviour at a time. The module it tested is
gone, but almost nothing it asserted was ABOUT Vega: colours stable per label, empty
input degrading to None instead of a broken panel, hours formatted differently from
counts, a payload that cannot close its own script tag. Those all still hold — of
render.chart_data, render._stack, dashboards.chart_panel_data and the panel container.

The parts that really were Vega's (mark types, encodings, the hand-built hover layer)
are the client's now and covered where they render.
"""
import unittest

import dashboards
import render


class ChartDataTest(unittest.TestCase):
    """render.chart_data — the report's envelope."""

    def test_carries_dates_and_a_series_per_line(self):
        out = render.chart_data(
            [{"name": "A", "vals": [1, 2], "color": "#111"},
             {"name": "B", "vals": [3, 4]}], ["x", "y"], "commits")
        self.assertEqual(out["dates"], ["x", "y"])
        self.assertEqual([s["name"] for s in out["series"]], ["A", "B"])
        self.assertEqual(out["unit"], "commits")
        self.assertFalse(out["stacked"])
        self.assertFalse(out["areaFirst"])

    def test_colour_is_stable_per_label_when_not_given(self):
        # the same name-hash the charts always used, so a company keeps its colour
        # across every panel it appears in
        out = render.chart_data([{"name": "Acme", "vals": [1]}], ["x"])
        self.assertEqual(out["series"][0]["color"], render._element_color("Acme"))

    def test_a_given_colour_wins(self):
        out = render.chart_data([{"name": "Acme", "vals": [1], "color": "#abcdef"}], ["x"])
        self.assertEqual(out["series"][0]["color"], "#abcdef")

    def test_series_with_nothing_in_them_are_dropped(self):
        out = render.chart_data(
            [{"name": "A", "vals": [None, None]}, {"name": "B", "vals": [1]}], ["x"])
        self.assertEqual([s["name"] for s in out["series"]], ["B"])

    def test_empty_input_is_none_rather_than_an_empty_chart(self):
        # every caller renders its own "no data" hint for this
        self.assertIsNone(render.chart_data([], ["x"]))
        self.assertIsNone(render.chart_data([{"name": "A", "vals": [1]}], []))
        self.assertIsNone(render.chart_data(None, None))

    def test_flags_ride_along(self):
        self.assertTrue(render.chart_data([{"name": "A", "vals": [1]}], ["x"],
                                          area_first=True)["areaFirst"])
        self.assertTrue(render.chart_data([{"name": "A", "vals": [1]}], ["x"],
                                          stacked=True)["stacked"])

    def test_unit_travels_so_the_client_can_pick_a_format(self):
        # counts render SI-compact and hours as a trimmed float — the split the Vega
        # axis format used to encode, made on the client now
        self.assertEqual(render.chart_data([{"name": "T", "vals": [3.2]}], ["x"],
                                           "hours")["unit"], "hours")


class StackTest(unittest.TestCase):
    """render._stack — the per-company stacks."""

    def test_colour_comes_from_the_company_map(self):
        out = render._stack([{"company": "A", "vals": [1, 2]}], ["x", "y"],
                            [{"company": "A", "color": "#123456"}], "LOC")
        self.assertTrue(out["stacked"])
        self.assertEqual(out["series"][0]["color"], "#123456")
        self.assertEqual(out["unit"], "LOC")

    def test_a_company_missing_from_the_map_still_gets_its_stable_colour(self):
        out = render._stack([{"company": "Zeta", "vals": [1]}], ["x"], [])
        self.assertEqual(out["series"][0]["color"], render._element_color("Zeta"))

    def test_rows_may_name_their_company_under_any_of_three_keys(self):
        for key in ("company", "key", "name"):
            out = render._stack([{key: "A", "vals": [1]}], ["x"], [])
            self.assertEqual(out["series"][0]["name"], "A", key)

    def test_order_is_preserved_because_it_is_the_stacking_order(self):
        out = render._stack([{"company": c, "vals": [1]} for c in ("A", "B", "C")],
                            ["x"], [])
        self.assertEqual([s["name"] for s in out["series"]], ["A", "B", "C"])


class PanelChartDataTest(unittest.TestCase):
    """dashboards.chart_panel_data — the five vizzes a saved dashboard can hold."""

    _TS = {"dates": ["x", "y"], "s": [{"name": "A", "vals": [1, 2]}]}

    def test_line_and_area_reuse_the_report_envelope(self):
        for viz in ("line", "area"):
            out = dashboards.chart_panel_data(viz, self._TS, ["s"], "T")
            self.assertEqual(out["kind"], viz, viz)
            self.assertEqual(out["dates"], ["x", "y"], viz)
            self.assertEqual(out["areaFirst"], viz == "area", viz)

    def test_scalars_become_one_row_per_measure(self):
        out = dashboards.chart_panel_data(
            "column", {"totals": {"bugs": 3, "prs": 7}},
            ["totals.bugs", "totals.prs"], "Volume")
        self.assertEqual(out["kind"], "column")
        self.assertEqual([r["value"] for r in out["rows"]], [3, 7])
        self.assertTrue(all(r["color"] for r in out["rows"]))

    def test_horizontal_bars_arrive_sorted_because_nothing_downstream_sorts(self):
        out = dashboards.chart_panel_data(
            "bar", {"totals": {"a": 1, "b": 9, "c": 5}},
            ["totals.a", "totals.b", "totals.c"], "T")
        self.assertEqual([r["value"] for r in out["rows"]], [9, 5, 1])

    def test_column_keeps_the_order_it_was_given(self):
        out = dashboards.chart_panel_data(
            "column", {"totals": {"a": 1, "b": 9}}, ["totals.a", "totals.b"], "T")
        self.assertEqual([r["value"] for r in out["rows"]], [1, 9])

    def test_nothing_to_draw_is_none(self):
        self.assertIsNone(dashboards.chart_panel_data("line", {}, [], "T"))
        self.assertIsNone(dashboards.chart_panel_data("pie", {}, ["nope"], "T"))
        self.assertIsNone(dashboards.chart_panel_data("line", None, ["s"], "T"))


class PanelContainerTest(unittest.TestCase):
    """render._panel_container — the div the dashboard editor mounts a chart into."""

    def test_payload_cannot_close_the_script_tag(self):
        html = render._panel_container(
            {"dates": ["</script><script>alert(1)</script>"], "series": []}, "line")
        self.assertIn('class="vl-panel"', html)
        self.assertNotIn("</script><script>alert(1)", html)
        self.assertIn("<\\/script>", html)

    def test_the_kind_travels_with_the_data(self):
        import json
        html = render._panel_container(render.chart_data(
            [{"name": "A", "vals": [1]}], ["x"]), "area")
        payload = json.loads(str(html).split('class="vl-spec">')[1].split("</script>")[0])
        self.assertEqual(payload["kind"], "area")

    def test_nothing_to_draw_renders_nothing(self):
        # so the caller's own "no data" hint is what shows
        self.assertEqual(str(render._panel_container(None, "line")), "")


if __name__ == "__main__":
    unittest.main()
