"""The score screen lays pillars out heaviest-first, in ONE place: byWeight().

This is a guard for a defect that has now arrived twice from the same cause. Oleg
reported it the first time — "why is the pillar order different top and bottom?" —
when the change table iterated PILLAR_ORDER (declaration order, Engagement first)
while the ingredients table sorted by weight, so one screen listed the same four
pillars two ways. Both were routed through byWeight.

It came back in the team table's Make-up bar, which had not been looked at: the
segments read left to right in declaration order while the drill that opens directly
underneath them listed the same pillars heaviest-first, and the bar's tooltip named
them in the wrong order too.

PILLAR_ORDER itself is legitimate — it is the canonical set of keys, and byWeight
sorts a copy of it — so the rule is not "never mention it". The rule is that nothing
RENDERS from it: iterating it with .map or .filter outside byWeight is how the order
gets published to a reader, and that is what has to go through the sort.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "frontend" / "src"


class PillarOrderTest(unittest.TestCase):
    # byWeight is the one place allowed to walk the raw list, because sorting it IS
    # its job. Everything else must consume byWeight's output.
    SORTER = "function byWeight"

    def _files(self):
        return sorted(list(SRC.rglob("*.tsx")) + list(SRC.rglob("*.ts")))

    def test_nothing_renders_from_the_declaration_order(self):
        offenders = []
        for path in self._files():
            text = re.sub(r"//[^\n]*|/\*.*?\*/", "", path.read_text(), flags=re.S)
            if self.SORTER in text:
                # Drop the sorter's own body so its internal .slice().sort() is not a hit.
                start = text.index(self.SORTER)
                end = text.index("}", text.index("{", start)) + 1
                text = text[:start] + text[end:]
            # PILLAR_ORDER followed by a chained iteration, across newlines.
            for m in re.finditer(r"PILLAR_ORDER\s*(?:\.\s*)?\n?\s*\.\s*(map|filter|forEach)\b", text):
                line = text[:m.start()].count("\n") + 1
                offenders.append(
                    f"{path.relative_to(ROOT)}:~{line} PILLAR_ORDER.{m.group(1)} — render "
                    f"from byWeight(weights) so the order matches every other pillar list")
        self.assertEqual(offenders, [])

    def test_the_sorter_still_sorts_by_descending_weight(self):
        """A guard that only bans the raw list is worthless if the sort is wrong."""
        text = (SRC / "widgets" / "score" / "PersonScore.tsx").read_text()
        body = text[text.index(self.SORTER):]
        body = body[:body.index("\n}")]
        self.assertIn("PILLAR_ORDER.slice()", body,
                      "byWeight must sort a COPY — sorting in place would reorder the "
                      "canonical list for every later reader")
        self.assertRegex(body.replace(" ", ""),
                         r"weights\[b\]\|\|0\)-\(weights\[a\]\|\|0",
                         "descending by weight, with a missing weight treated as 0")


if __name__ == "__main__":
    unittest.main()
