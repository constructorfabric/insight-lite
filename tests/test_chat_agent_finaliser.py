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
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
import chat_agent  # noqa: E402


class _Part:
    """Stands in for a genai response part: text, a function_call, or both absent."""

    def __init__(self, text=None, call=None):
        self.text = text
        self.function_call = call


class _Call:
    def __init__(self, name="describe_schema", args=None):
        self.name = name
        self.args = args or {}


class _Chunk:
    def __init__(self, parts):
        content = type("C", (), {"parts": parts})()
        self.candidates = [type("D", (), {"content": content})()]
        self.usage_metadata = None


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


class OutOfStepsTest(unittest.TestCase):
    def test_the_loop_is_bounded_and_the_finaliser_is_called_once(self):
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
