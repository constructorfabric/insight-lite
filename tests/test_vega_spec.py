import unittest
import vega_spec


class VegaSpecTest(unittest.TestCase):
    def test_config_uses_our_palette(self):
        cfg = vega_spec.vega_config()
        self.assertIn("#5b5bf0", cfg["range"]["category"])      # --acc leads the palette
        self.assertIsNone(cfg["view"]["stroke"])                # no frame box

    def test_color_scale_stable_by_label(self):
        import render
        sc = vega_spec.color_scale(["Acme", "Beta"])
        self.assertEqual(sc["domain"], ["Acme", "Beta"])
        self.assertEqual(sc["range"], [render._element_color("Acme"), render._element_color("Beta")])

    def test_build_column_spec_is_measure_values(self):
        result = {"totals": {"bugs": 3, "prs": 7}}
        spec = vega_spec.build_spec("column", result, ["totals.bugs", "totals.prs"], "Volume")
        self.assertEqual(spec["mark"]["type"] if isinstance(spec["mark"], dict) else spec["mark"], "bar")
        self.assertEqual(len(spec["data"]["values"]), 2)
        self.assertIn("config", spec)

    def test_build_line_spec_multi_series(self):
        result = {"dates": ["Q1", "Q2"],
                  "commit_rows": [{"key": "A", "vals": [1, 2]}, {"key": "B", "vals": [3, 4]}]}
        spec = vega_spec.build_spec("line", result, ["commit_rows"], "Commits")
        self.assertEqual(len(spec["data"]["values"]), 4)        # 2 series x 2 dates, long form
        self.assertEqual(spec["encoding"]["color"]["field"], "series")

    def test_build_pie_spec(self):
        result = {"by_company": [{"company": "A", "commits": 5}, {"company": "B", "commits": 3}]}
        spec = vega_spec.build_spec("pie", result, ["by_company"], "By company")
        self.assertEqual(spec["mark"]["type"], "arc")

    def test_unknown_or_empty_returns_none(self):
        self.assertIsNone(vega_spec.build_spec("column", {"totals": {}}, ["totals.x"], "t"))


class ReportSpecTest(unittest.TestCase):
    def test_line_spec_multi_series_with_colors(self):
        spec = vega_spec.line_spec(
            [{"name": "Opened", "vals": [1, 2], "color": "#2f80ed"},
             {"name": "Merged", "vals": [3, 4], "color": "#10b981"}], ["Q1", "Q2"])
        self.assertEqual(len(spec["data"]["values"]), 4)
        base = spec["layer"][0]      # report specs are layered: base chart + hover layer
        self.assertEqual(base["encoding"]["color"]["scale"]["range"], ["#2f80ed", "#10b981"])
        self.assertEqual(base["mark"]["type"], "line")

    def test_line_spec_area_first_is_area(self):
        spec = vega_spec.line_spec([{"name": "TTM", "vals": [1, 2]}], ["a", "b"], area_first=True)
        self.assertEqual(spec["layer"][0]["mark"]["type"], "area")

    def test_line_spec_x_sorted_by_dates(self):
        spec = vega_spec.line_spec([{"name": "s", "vals": [1, 2, 3]}], ["Q3 25", "Q4 25", "Q1 26"])
        self.assertEqual(spec["layer"][0]["encoding"]["x"]["sort"], ["Q3 25", "Q4 25", "Q1 26"])

    def test_stacked_area_spec(self):
        spec = vega_spec.stacked_area_spec(
            [{"company": "A", "vals": [1, 2]}, {"company": "B", "vals": [3, 4]}],
            ["Q1", "Q2"], [{"company": "A", "color": "#111"}, {"company": "B", "color": "#222"}])
        base = spec["layer"][0]
        self.assertEqual(base["mark"]["type"], "area")
        self.assertEqual(len(spec["data"]["values"]), 4)
        self.assertEqual(base["encoding"]["color"]["scale"]["range"], ["#111", "#222"])
        self.assertTrue(base["encoding"]["y"].get("stack"))

    def test_shared_hover_layer_combined_tooltip(self):
        # the old ax-hit behaviour: a nearest-x hover param + a pivot so ONE tooltip
        # lists every series at the hovered x (not a per-point tooltip).
        spec = vega_spec.line_spec(
            [{"name": "Opened", "vals": [1, 2]}, {"name": "Merged", "vals": [3, 4]}], ["Q1", "Q2"])
        hover = spec["layer"][1]
        self.assertEqual(hover["params"][0]["name"], "hover")
        self.assertTrue(hover["params"][0]["select"]["nearest"])
        self.assertEqual(hover["params"][0]["select"]["fields"], ["x"])
        self.assertEqual(hover["transform"][0]["pivot"], "series")
        tip_fields = [t["field"] for t in hover["encoding"]["tooltip"]]
        self.assertIn("Opened", tip_fields)
        self.assertIn("Merged", tip_fields)   # both series in one tooltip
        # the base line/area carries no per-mark tooltip (the hover layer owns it)
        self.assertFalse(spec["layer"][0]["mark"].get("tooltip", True))

    def test_stacked_hover_tooltip_has_total_and_all_bands(self):
        # stacked areas: the hover tooltip shows every band PLUS the stack total.
        spec = vega_spec.stacked_area_spec(
            [{"company": "A", "vals": [1, 2]}, {"company": "B", "vals": [3, 4]}],
            ["Q1", "Q2"], [{"company": "A", "color": "#111"}, {"company": "B", "color": "#222"}])
        hover = spec["layer"][1]
        tip_fields = [t["field"] for t in hover["encoding"]["tooltip"]]
        self.assertEqual(tip_fields, ["x", "Total", "A", "B"])   # total first, then bands
        calc = [t for t in hover["transform"] if "calculate" in t][0]
        self.assertIn('datum["A"]', calc["calculate"])
        self.assertIn('datum["B"]', calc["calculate"])

    def test_line_hover_has_no_total(self):
        # plain multi-line: no misleading "total" (summing opened+merged is meaningless)
        spec = vega_spec.line_spec(
            [{"name": "Opened", "vals": [1]}, {"name": "Merged", "vals": [2]}], ["Q1"])
        tip_fields = [t["field"] for t in spec["layer"][1]["encoding"]["tooltip"]]
        self.assertNotIn("Total", tip_fields)

    def test_specs_empty_return_none(self):
        self.assertIsNone(vega_spec.line_spec([], ["a"]))
        self.assertIsNone(vega_spec.stacked_area_spec([], ["a"], []))

    def test_value_axis_and_tooltip_are_compact_formatted(self):
        # counts/LOC → SI compact (~s: 2.6M/12k); hours → trimmed float. Keeps the
        # readability the hand-rolled charts had; the hover tooltip carries the format.
        loc = vega_spec.line_spec([{"name": "LOC", "vals": [2588408, 3000000]}], ["a", "b"], unit="LOC")
        self.assertEqual(loc["layer"][0]["encoding"]["y"]["axis"]["format"], "~s")
        tip_val = [t for t in loc["layer"][1]["encoding"]["tooltip"] if t["field"] == "LOC"][0]
        self.assertEqual(tip_val["format"], "~s")
        hrs = vega_spec.line_spec([{"name": "TTM", "vals": [3.2, 5.1]}], ["a", "b"], unit="hours")
        self.assertEqual(hrs["layer"][0]["encoding"]["y"]["axis"]["format"], ".2~f")
        sa = vega_spec.stacked_area_spec(
            [{"company": "A", "vals": [1, 2]}], ["a", "b"], [{"company": "A", "color": "#111"}], unit="LOC")
        self.assertEqual(sa["layer"][0]["encoding"]["y"]["axis"]["format"], "~s")

    def test_panel_html_wraps_and_escapes(self):
        html = vega_spec.panel_html({"mark": "line", "x": "</script>"})
        self.assertIn('class="vl-panel"', html)
        self.assertIn('class="vl-spec"', html)
        self.assertNotIn("</script></script>", html)   # spec's </ is escaped


if __name__ == "__main__":
    unittest.main()
