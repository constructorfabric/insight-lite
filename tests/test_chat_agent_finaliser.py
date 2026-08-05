"""A chat turn that runs out of tool steps must still say something.

This is the regression test for four turns in the production transcript that produced
no text at all. The fingerprint is unambiguous: 24 rows in chat_tool_call with
message_id NULL, in groups of exactly 8 — MAX_HOPS — because server.py records the
assistant row only `if answer_text`. The user saw an empty panel; one of those sessions
is a question at 08:35, silence, and then "is it stuck?" at 08:36. Another is from today.

The loop was not looping: no capped turn repeats an identical (tool, args) pair. It
exhausted its hops on real work and then the safety net failed, and the net's failure
was invisible by construction — `except Exception: pass` with no log.

Two guarantees are pinned here, because either one alone leaves the hole open:
  * the finaliser's failure is REPORTED, not swallowed;
  * whatever happens, the turn emits text — through on_text, since the server assembles
    the answer from the streamed frames and a return value alone reaches nobody.
"""
import contextlib
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import chat_agent  # noqa: E402


class _Part:
    """Stands in for a genai response part: text, a function_call, or both absent."""

    def __init__(self, text=None, call=None):
        self.text = text
        self.function_call = call


class _Call:
    # Default to a name that is NOT in DISPATCH: the call then resolves to nothing, which
    # keeps these tests off the real database. Using a real tool here made the first round
    # succeed by accident, which quietly changed what the budget assertions were measuring.
    def __init__(self, name="not_a_tool", args=None):
        self.name = name
        self.args = args or {}


class _Chunk:
    def __init__(self, parts, prompt_tokens=None):
        content = type("C", (), {"parts": parts})()
        self.candidates = [type("D", (), {"content": content})()]
        # None unless a test cares: the budget's token ceiling is now counted inside ask()
        # from what the model reports, not read back out of the caller's optional
        # accounting dict, so a test about the ceiling has to make the model report.
        self.usage_metadata = None if prompt_tokens is None else type(
            "U", (), {"prompt_token_count": prompt_tokens, "candidates_token_count": 0,
                      "thoughts_token_count": 0, "cached_content_token_count": 0,
                      "total_token_count": prompt_tokens})()


class _Models:
    """Scripted model: every hop asks for a tool, and the finaliser behaves as told.

    `final` is either a list of parts to stream, or an exception to raise — the two
    shapes of "the net did not catch it" that the transcript cannot tell apart.
    """

    def __init__(self, final):
        self.final = final
        self.hops = 0
        self.final_calls = 0

    def generate_content_stream(self, model=None, contents=None, config=None):
        tool_cfg = getattr(config, "tool_config", None)
        if tool_cfg is not None:                      # the forced last word
            self.final_calls += 1
            if isinstance(self.final, BaseException):
                raise self.final
            return [_Chunk(self.final)]
        self.hops += 1
        return [_Chunk([_Part(call=_Call())])]        # ...and never any text


class _Client:
    def __init__(self, final):
        self.models = _Models(final)


@contextlib.contextmanager
def _one_tool_that_works():
    """Make tool success DELIBERATE. Left to the real DISPATCH, a stub call's success is
    incidental — passing an argument the real function does not take makes the round
    unproductive for a reason that has nothing to do with what is being tested."""
    import tooldefs
    with patch.dict(tooldefs.DISPATCH, {"probe": lambda **kw: {"rows": [kw]}}, clear=False):
        yield


def _texts(contents):
    """Every plain text part fed back to the model, in order."""
    out = []
    for c in contents:
        for part in getattr(c, "parts", None) or []:
            t = getattr(part, "text", None)
            if t:
                out.append(t)
    return out


def _run(final):
    """Drive one exhausting turn; return (text, emitted, degraded, client)."""
    emitted, degraded = [], []
    client = _Client(final)
    out = chat_agent.ask(
        client, object(), [],
        on_text=emitted.append,
        on_degraded=lambda where, exc: degraded.append((where, type(exc).__name__)),
    )
    return out, emitted, degraded, client


class BudgetWarningTest(unittest.TestCase):
    """The transcript's real complaint: one capped turn composed its final combined query
    on hop 8 of 8, leaving no round to answer in. The finaliser is a net, not a plan — so
    the turn is told when the budget is nearly gone, while it can still act on it."""

    def _contents_after_an_exhausting_turn(self):
        contents = []
        chat_agent.ask(_Client([_Part(text="done")]), object(), contents)
        return _texts(contents)

    def test_it_warns_before_the_last_round_not_after(self):
        warnings = [t for t in self._contents_after_an_exhausting_turn()
                    if "tool round" in t]
        self.assertEqual(len(warnings), 2,
                         "one warning at 1 round left and one at 0 — earlier is noise, "
                         "later is useless")

    def test_the_warning_counts_down_and_says_what_to_do(self):
        warnings = [t for t in self._contents_after_an_exhausting_turn()
                    if "tool round" in t]
        self.assertIn("1 tool round left", warnings[0])
        self.assertIn("0 tool rounds left", warnings[1])
        for w in warnings:
            self.assertIn("Answer now", w)
            self.assertIn("what is missing", w)

    def test_a_short_turn_is_never_warned(self):
        """A model that answers on hop 1 must not see budget talk at all."""
        class _Quick(_Models):
            def generate_content_stream(self, model=None, contents=None, config=None):
                self.hops += 1
                return [_Chunk([_Part(text="the answer")])]     # no calls -> loop breaks

        client = _Client(None)
        client.models = _Quick(None)
        contents = []
        out = chat_agent.ask(client, object(), contents)
        self.assertEqual(out, "the answer")
        self.assertEqual(client.models.hops, 1)
        self.assertEqual([t for t in _texts(contents) if "tool round" in t], [])

    @staticmethod
    def _client(fresh_args: bool, prompt_tokens=None):
        """A model that calls `probe` every round — with new arguments each time, or the
        same ones. That is the whole difference between earning rounds and not.
        `prompt_tokens` makes it report usage, for the ceiling."""
        class _M(_Models):
            def generate_content_stream(self, model=None, contents=None, config=None):
                if getattr(config, "tool_config", None) is not None:
                    self.final_calls += 1
                    return [_Chunk([_Part(text="done")])]
                self.hops += 1
                args = {"n": self.hops} if fresh_args else {"n": 1}
                return [_Chunk([_Part(call=_Call(name="probe", args=args))], prompt_tokens)]

        c = _Client(None)
        c.models = _M(None)
        return c

    def test_a_turn_that_keeps_getting_somewhere_reaches_the_ceiling(self):
        with _one_tool_that_works():
            client = self._client(fresh_args=True)
            chat_agent.ask(client, object(), [])
        self.assertEqual(client.models.hops, chat_agent.MAX_HOPS_CEILING)

    def test_repeating_a_call_earns_nothing_after_the_first(self):
        """Only NEW work extends the budget. A turn whose first call succeeds and is then
        repeated gets exactly that one round more — the first was real progress, the rest
        is the thrash the ceiling exists to bound."""
        with _one_tool_that_works():
            client = self._client(fresh_args=False)
            chat_agent.ask(client, object(), [])
        self.assertEqual(client.models.hops, chat_agent.MAX_HOPS + 1)

    def test_failing_calls_earn_nothing_at_all(self):
        """New arguments every round, but the tool errors every time. Novelty alone is not
        progress — otherwise a turn could extend itself by varying a query that never works,
        which is exactly the shape of the fourteen failures in the transcript."""
        import tooldefs
        with patch.dict(tooldefs.DISPATCH,
                        {"probe": lambda **kw: {"error": "nope"}}, clear=False):
            client = self._client(fresh_args=True)
            chat_agent.ask(client, object(), [])
        self.assertEqual(client.models.hops, chat_agent.MAX_HOPS)

    def test_the_token_ceiling_stops_extension_even_when_productive(self):
        """A turn can be productive and still be too expensive to continue, because every
        hop re-sends the whole transcript."""
        with _one_tool_that_works():
            client = self._client(fresh_args=True,
                                  prompt_tokens=chat_agent.TOKEN_CEILING + 1)
            chat_agent.ask(client, object(), [])
        self.assertEqual(client.models.hops, chat_agent.MAX_HOPS)

    def test_the_ceiling_holds_for_a_caller_that_wants_no_accounting(self):
        """The review finding: the gate read `(usage_acc or {}).get("input", 0)`, so a
        caller that passes no accumulator — the CLI, or any direct caller — measured zero
        spend and got every extension however expensive the turn became."""
        with _one_tool_that_works():
            client = self._client(fresh_args=True,
                                  prompt_tokens=chat_agent.TOKEN_CEILING + 1)
            chat_agent.ask(client, object(), [], usage_acc=None)
        self.assertEqual(client.models.hops, chat_agent.MAX_HOPS)

    def test_accounting_still_reaches_the_caller_that_asks_for_it(self):
        """Counting internally must not stop usage_acc being filled in — the server bills
        from it."""
        acc = {}
        with _one_tool_that_works():
            client = self._client(fresh_args=True, prompt_tokens=1000)
            chat_agent.ask(client, object(), [], usage_acc=acc)
        self.assertGreaterEqual(acc["input"], 1000 * client.models.hops)

    def test_the_budget_bounds_are_ordered(self):
        self.assertLess(chat_agent.MAX_HOPS, chat_agent.MAX_HOPS_CEILING)
        self.assertGreater(chat_agent.TOKEN_CEILING, 0)


class OutOfStepsTest(unittest.TestCase):
    def test_the_loop_is_bounded_and_the_finaliser_is_called_once(self):
        """_run's model calls a tool that does not exist, so no round is productive and the
        turn stops at the base budget. The ceiling is exercised in BudgetWarningTest."""
        _, _, _, client = _run([_Part(text="here is what I found")])
        self.assertEqual(client.models.hops, chat_agent.MAX_HOPS)
        self.assertEqual(client.models.final_calls, 1)

    def test_a_working_finaliser_supplies_the_answer(self):
        text, emitted, degraded, _ = _run([_Part(text="here is what I found")])
        self.assertEqual(text, "here is what I found")
        self.assertEqual(emitted, ["here is what I found"])
        self.assertEqual(degraded, [], "nothing failed, so nothing to report")

    def test_a_raising_finaliser_is_reported_and_still_answers(self):
        """The shipped bug: this path returned "" and logged nothing."""
        text, emitted, degraded, _ = _run(RuntimeError("400 INVALID_ARGUMENT"))
        self.assertEqual(text, chat_agent.OUT_OF_STEPS)
        self.assertEqual(emitted, [chat_agent.OUT_OF_STEPS],
                         "the server builds the answer from streamed frames, so the "
                         "message must be EMITTED, not just returned")
        self.assertEqual([w for w, _ in degraded], ["chat finaliser after MAX_HOPS"])
        self.assertEqual([e for _, e in degraded], ["RuntimeError"])

    def test_a_silent_finaliser_still_answers(self):
        """Same hole without an exception: the call succeeds and streams no text."""
        text, emitted, degraded, _ = _run([_Part(text="")])
        self.assertEqual(text, chat_agent.OUT_OF_STEPS)
        self.assertEqual(emitted, [chat_agent.OUT_OF_STEPS])
        self.assertEqual(degraded, [], "it did not fail, it just said nothing")

    def test_the_message_names_the_cause_and_what_to_do(self):
        self.assertIn("tool steps", chat_agent.OUT_OF_STEPS)
        self.assertTrue(chat_agent.OUT_OF_STEPS.strip(),
                        "an empty constant would reopen the hole this test closes")

    def test_a_logger_that_raises_cannot_break_the_turn(self):
        emitted = []
        client = _Client(RuntimeError("400"))
        text = chat_agent.ask(client, object(), [], on_text=emitted.append,
                              on_degraded=lambda *_: (_ for _ in ()).throw(OSError("log is gone")))
        self.assertEqual(text, chat_agent.OUT_OF_STEPS)
        self.assertEqual(emitted, [chat_agent.OUT_OF_STEPS])

    def test_the_finaliser_keeps_the_tool_declarations(self):
        """Dropping them is what made the request rejectable while the transcript was
        full of function_call parts; NONE is what stops another call."""
        from google.genai import types
        cfg = chat_agent._final_config()
        self.assertTrue(cfg.tools, "declarations must still be there")
        self.assertEqual(cfg.tool_config.function_calling_config.mode,
                         types.FunctionCallingConfigMode.NONE)


if __name__ == "__main__":
    unittest.main()
