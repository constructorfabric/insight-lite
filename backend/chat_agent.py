#!/usr/bin/env python3
"""In-process metrics chat — a Gemini agent grounded in the report's own data.

Answers questions about the report ("why did contribution drop for Insight last
week?") by letting Gemini call the READ-ONLY tools in ``tooldefs`` (the same
functions the MCP server exposes) and explaining the numbers it gets back. Every
figure in an answer comes from a tool result, never from the model's memory.

    GEMINI_API_KEY=…  python chat_agent.py "why did PR merge rate fall in June?"
    GEMINI_API_KEY=…  python chat_agent.py            # interactive REPL

Model access (env):
  GEMINI_API_KEY        Google AI Studio key (default path)
  GEMINI_MODEL          model id (default 'gemini-2.5-flash')
  GOOGLE_GENAI_USE_VERTEXAI=1 + GOOGLE_CLOUD_PROJECT/LOCATION  → Vertex AI instead
"""
from __future__ import annotations

import json
import os
import sys

import store
import tooldefs

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
# Tool round-trips per turn. MAX_HOPS is what every turn gets; a turn that keeps EARNING
# it may reach MAX_HOPS_CEILING. The axis is progress, not the question: which questions
# deserve more cannot be known when the turn starts, but "this round got somewhere" is
# observable — a round is productive when at least one call succeeded and it was not a
# repeat of a call already made in this turn.
#
# Why not simply raise the flat cap: hops are not equal in cost. Each one re-sends the
# whole transcript, so the last hop of a long turn is the expensive one — a turn in the
# production log reached 102,779 input tokens with 35 KB of tool results being re-sent.
# Paying that for a turn that is thrashing buys nothing, which is why an unproductive round
# does not extend the budget. TOKEN_CEILING stops extension regardless, since a turn can be
# productive and still be too expensive to keep going.
MAX_HOPS = 8
MAX_HOPS_CEILING = 14
TOKEN_CEILING = 250_000

_SYSTEM = """You are the Constructor Insight metrics assistant. You explain the \
contribution/delivery report using ONLY live data fetched through the provided tools.

Hard rules:
- Every number you state MUST come from a tool result in THIS turn. Never invent,
  estimate, or recall figures. If a tool didn't return it, say the data isn't available.
- When you explain what a metric means, cite its definition/formula from
  metrics_catalog — don't describe metrics from general knowledge.
- Dates are 'YYYY-MM-DD', UTC. A scope is '<org|element|repo|project>:<target>'
  (e.g. 'element:Insight'). Discover valid targets with list_dimension. A scope that came
  from the page the person is looking at is a DEFAULT, not part of their question: if a
  scoped lookup comes back empty or unscored for a PERSON, try it again with scope='' before
  concluding the data is unavailable. Never answer "no data" while an unscoped call would
  answer the question.
- SAY WHAT THE ANSWER COVERS whenever it is not the whole org: name the slice in the answer,
  because the person may have set a scope earlier and forgotten it. If you dropped or widened
  a scope to answer, say that too — an org-wide number presented while they are looking at
  one element is the same misunderstanding in reverse. Tool results carry a `covers` line for
  exactly this; quote it rather than inventing wording. There is NO
  person scope: 'person:<login>' is invalid and will error. For ONE person use
  person(login=…) for their profile/all-time dimension, list_items(author=<login>) to
  count or list their items, or sql_query — never scope=person:…
- sql_query: the table names and the join rule are in GROUNDING below — use them. Call
  describe_schema() when you need a table's COLUMNS, and use the exact names it returns
  rather than guessing. A failed query answers with the right names; read it and fix the
  query rather than retrying variants blindly.
- Prefer trend() for "how did X change over time" and list_items() to show the
  rows behind a number (each row has a GitHub URL you can cite).
- Exclude bots/migration rows unless the user asks for them.
- First person: if the report context carries `asking_as=<login>`, then I/me/my/mine
  refer to that signed-in person — use that login (e.g. person(login), or list_items
  author=<login>). If `asking_as` is absent and the question is first-person, ask who
  they mean rather than guessing.
- Be concise. Show the concrete numbers, then a short 'why'. Don't dump raw JSON."""


def _grounding() -> str:
    """The table names and the repo-key rule, appended to the system instruction.

    The rule especially. Every fact table's `repo` column holds repo.key ('org/name'), and
    a query that filters on repo.name instead does not error — it returns zero rows. That
    is in describe_schema's output now, but only for a turn that calls it; in the
    transcript one turn answered a question about an element from an empty result and then
    spent three hops working out why. A silent wrong answer is worth carrying in the
    always-present context rather than behind a tool call.

    Best-effort: if the database cannot be read while the config is being built, the
    assistant runs without this rather than failing to start."""
    try:
        conn = store.connect()
        try:
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name")
                      if r[0] not in tooldefs.BLOCKED_TABLES]
        finally:
            conn.close()
    except Exception:                              # noqa: BLE001 — grounding is optional
        return ""
    if not tables:
        return ""
    return ("\n\nGROUNDING (live, do not re-derive):\n"
            "- Tables: " + ", ".join(tables) + "\n"
            "- Joining repos: every table with a `repo` column holds repo.key, the "
            "'org/name' form. repo.name is the bare name and matches NOTHING — filtering "
            "on it does not error, it silently returns zero rows. "
            "list_dimension(kind='repos') returns keys.\n"
            "- Joining people: person.login, via author_login / reviewer_login / "
            "actor_login / login depending on the table.")


_SYSTEM_FULL = None


def _system() -> str:
    """System instruction plus grounding, resolved once per process. It feeds the cache
    key, so a schema change rebuilds the cached prefix instead of serving a stale one."""
    global _SYSTEM_FULL
    if _SYSTEM_FULL is None:
        _SYSTEM_FULL = _SYSTEM + _grounding()
    return _SYSTEM_FULL


def _build_client():
    """Construct the genai Client from env. Raises RuntimeError (never exits) so the
    server can turn a missing key into an error event instead of crashing."""
    from google import genai                       # imported lazily so --help works
    if os.environ.get("GOOGLE_GENAI_USE_VERTEXAI") in ("1", "true", "True"):
        return genai.Client(
            vertexai=True,
            project=os.environ.get("GOOGLE_CLOUD_PROJECT"),
            location=os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        )
    key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not key:
        raise RuntimeError("Set GEMINI_API_KEY (or GOOGLE_GENAI_USE_VERTEXAI=1 for Vertex).")
    return genai.Client(api_key=key)


def _tools_config():
    from google.genai import types
    decls = [types.FunctionDeclaration(**d) for d in tooldefs.declarations()]
    return types.GenerateContentConfig(
        system_instruction=_system(),
        tools=[types.Tool(function_declarations=decls)],
        temperature=0,
    )


_FINAL = None

# What a turn says when it runs out of steps. Text, not silence: the server records the
# assistant row only when there IS text, so an empty finish left no row at all — the turn
# vanished from the transcript AND from the panel, and the user's next message was
# a one-word "is it stuck?".
OUT_OF_STEPS = ("I used all the tool steps I have for one question and could not finish "
                "an answer. Ask for one piece at a time, or narrow the period or scope.")


def _final_config():
    """Config for the forced last word when a turn exhausts its hops.

    The tool DECLARATIONS stay. They used to be dropped, on the reasoning that a config
    without tools cannot call any — but by then the transcript is full of function_call
    and function_response parts, and a request whose declarations no longer cover them is
    not guaranteed to be accepted. That is the shape of the silent failure this replaces.
    What stops another call is the mode, not the absence of the declarations:
    FunctionCallingConfigMode.NONE means "answer in text"."""
    global _FINAL
    if _FINAL is None:
        from google.genai import types
        decls = [types.FunctionDeclaration(**d) for d in tooldefs.declarations()]
        _FINAL = types.GenerateContentConfig(
            system_instruction=_system(),
            tools=[types.Tool(function_declarations=decls)],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.NONE)),
            temperature=0,
        )
    return _FINAL


# ---- explicit context caching (opt-in) -------------------------------------
# The stable prefix — system instruction + tool declarations, ~1.7k tokens — is
# re-sent on every hop of every turn. Caching it explicitly bills those tokens at the
# cache-read rate instead of full input. It carries a storage cost ($/token/hour), so
# it only pays off above a traffic threshold — hence opt-in via GEMINI_CACHE_TTL
# (seconds; unset/0 = off). The cache is keyed by model + a hash of the prefix, so a
# prompt/tool/model change rebuilds it; creation failure (too small / unsupported)
# degrades silently to uncached.
_CACHE = {"name": None, "key": None, "expire": 0.0}


def _cache_ttl() -> int:
    try:
        return int((os.environ.get("GEMINI_CACHE_TTL") or "").strip() or 0)
    except ValueError:
        return 0


def _cache_key() -> str:
    import hashlib
    blob = MODEL + "\n" + _system() + "\n" + json.dumps(tooldefs.declarations(), sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def _get_cache(client):
    """Name of a CachedContent holding the system+tools prefix, or None when caching
    is off/unavailable. Lazily (re)creates on key change or near expiry."""
    ttl = _cache_ttl()
    if ttl <= 0:
        return None
    import time
    from google.genai import types
    key, now = _cache_key(), time.time()
    if _CACHE["key"] == key and now < _CACHE["expire"] - 60:
        return _CACHE["name"]                      # valid, or a cached "None" backoff
    try:
        decls = [types.FunctionDeclaration(**d) for d in tooldefs.declarations()]
        cache = client.caches.create(model=MODEL, config=types.CreateCachedContentConfig(
            system_instruction=_system(),
            tools=[types.Tool(function_declarations=decls)],
            ttl=f"{ttl}s", display_name="insight-metrics-assistant"))
        _CACHE.update(name=cache.name, key=key, expire=now + ttl)
    except Exception:                              # noqa: BLE001 — run uncached, retry in 5m
        _CACHE.update(name=None, key=key, expire=now + 300)
    return _CACHE["name"]


def _cached_config(name):
    from google.genai import types
    # cached_content supplies system_instruction + tools; must NOT repeat them here.
    return types.GenerateContentConfig(cached_content=name, temperature=0)


def ask(client, config, contents, on_text=None, on_tool=None, usage_acc=None,
        tool_log=None, on_degraded=None) -> str:
    """Run one user turn to completion: stream text, execute any tool calls, loop
    until the model stops calling tools. `contents` is the running conversation
    (list of types.Content); it is mutated in place so multi-turn REPL keeps history.
    `on_text(delta)` receives streamed text; `on_tool(names)` is called once per
    round with the tool names about to run. If `usage_acc` (a dict) is given, token
    counts are accumulated into it across every model call in the turn. Returns the
    final answer text."""
    from google.genai import types
    answer = []
    # A fresh cache is valid for its whole TTL (>> a turn), so resolve once per turn;
    # when set, the config carries only cached_content (system+tools come from cache).
    cache_name = _get_cache(client)
    eff_config = _cached_config(cache_name) if cache_name else config
    # Input tokens are tracked HERE, not read back out of usage_acc: that argument is
    # optional external accounting, and `(usage_acc or {}).get("input", 0)` read as zero for
    # every caller that does not pass one — the CLI and any direct caller — so the token
    # ceiling silently did not apply to them. Caught in review on #9.
    budget, hop, seen, spent_in = MAX_HOPS, 0, set(), 0
    while hop < budget:
        calls, text_parts, model_parts, last_um = [], [], [], None
        for chunk in client.models.generate_content_stream(
                model=MODEL, contents=contents, config=eff_config):
            if getattr(chunk, "usage_metadata", None):
                last_um = chunk.usage_metadata        # cumulative for THIS call
            for cand in (chunk.candidates or []):
                for part in (cand.content.parts if cand.content else []):
                    model_parts.append(part)          # keep raw parts (thought_signature)
                    if getattr(part, "text", None):
                        text_parts.append(part.text)
                        if on_text:
                            on_text(part.text)
                    if getattr(part, "function_call", None):
                        calls.append(part.function_call)
        if last_um is not None:
            pin = getattr(last_um, "prompt_token_count", 0) or 0
            out = (getattr(last_um, "candidates_token_count", 0) or 0) \
                + (getattr(last_um, "thoughts_token_count", 0) or 0)   # thoughts billed as output
            cached = getattr(last_um, "cached_content_token_count", 0) or 0
            spent_in += pin
            if usage_acc is not None:
                usage_acc["input"] = usage_acc.get("input", 0) + pin
                usage_acc["output"] = usage_acc.get("output", 0) + out
                usage_acc["cached"] = usage_acc.get("cached", 0) + cached
                usage_acc["total"] = usage_acc.get("total", 0) \
                    + (getattr(last_um, "total_token_count", 0) or (pin + out))
        if text_parts:
            answer.append("".join(text_parts))
        if not calls:
            break
        if on_tool:
            on_tool([c.name for c in calls])
        # Record the model's turn using the ORIGINAL parts — Gemini 3 requires the
        # thought_signature carried on each function_call part to be echoed back, so
        # rebuilding from bare function_call values fails with 400 INVALID_ARGUMENT.
        contents.append(types.Content(role="model", parts=model_parts))
        results, productive = [], False
        for c in calls:
            fn = tooldefs.DISPATCH.get(c.name)
            args = dict(c.args or {})
            ok = True
            try:
                out = fn(**args) if fn else {"error": f"unknown tool {c.name}"}
                ok = fn is not None and not (isinstance(out, dict) and "error" in out)
            except Exception as exc:               # noqa: BLE001 — surface to the model
                out = {"error": f"{type(exc).__name__}: {exc}"}
                ok = False
            if tool_log is not None:
                tool_log.append({"name": c.name, "args": args, "result": out, "ok": ok})
            results.append(types.Part.from_function_response(name=c.name, response=out))
            sig = (c.name, json.dumps(args, sort_keys=True, default=str))
            productive = productive or (ok and sig not in seen)
            seen.add(sig)
        # Earn the next round, or don't — then say how many are left. The warning is the
        # fix the transcript asks for directly: one capped turn composed its final combined
        # query on the last hop it had and then had nothing left to answer with, and the
        # finaliser is a net rather than a plan. It is computed AFTER the extension so the
        # countdown the model sees is the budget it will actually get.
        if productive and budget < MAX_HOPS_CEILING and spent_in < TOKEN_CEILING:
            budget += 1
        left = budget - hop - 1
        if left <= 1:
            results.append(types.Part.from_text(
                text=(f"[{left} tool round{'' if left == 1 else 's'} left in this turn. "
                      "Answer now with what you already have, and say plainly what is "
                      "missing, rather than calling another tool.]")))
        contents.append(types.Content(role="user", parts=results))
        hop += 1
    else:
        # Loop ran the full MAX_HOPS without ever finishing (kept calling tools). Force ONE
        # final reply so the user gets a response instead of an empty turn — and if even
        # that produces nothing, SAY so. Four turns in the production transcript ended here
        # with no text at all: the tool calls were recorded with message_id NULL, no
        # assistant row was written, and the panel showed nothing. One of them is the user
        # asking "is it stuck?" a minute later, and the next is a question from today.
        if not "".join(answer).strip():
            try:
                last_um = None
                for chunk in client.models.generate_content_stream(
                        model=MODEL, contents=contents, config=_final_config()):
                    if getattr(chunk, "usage_metadata", None):
                        last_um = chunk.usage_metadata
                    for cand in (chunk.candidates or []):
                        for part in (cand.content.parts if cand.content else []):
                            if getattr(part, "text", None):
                                answer.append(part.text)
                                if on_text:
                                    on_text(part.text)
                if usage_acc is not None and last_um is not None:
                    pin = getattr(last_um, "prompt_token_count", 0) or 0
                    out = (getattr(last_um, "candidates_token_count", 0) or 0) \
                        + (getattr(last_um, "thoughts_token_count", 0) or 0)
                    usage_acc["input"] = usage_acc.get("input", 0) + pin
                    usage_acc["output"] = usage_acc.get("output", 0) + out
                    usage_acc["cached"] = usage_acc.get("cached", 0) \
                        + (getattr(last_um, "cached_content_token_count", 0) or 0)
                    usage_acc["total"] = usage_acc.get("total", 0) \
                        + (getattr(last_um, "total_token_count", 0) or (pin + out))
            except Exception as exc:                   # noqa: BLE001 — best-effort finaliser
                # NOT silent. server.log_degraded's own docstring says it: the best-effort
                # block is right, doing it without a log is the defect. This one hid the
                # only evidence of why a turn came back empty.
                _report(on_degraded, "chat finaliser after MAX_HOPS", exc)
            if not "".join(answer).strip():
                # Still nothing. Anything is better than silence, and it has to go through
                # on_text: the server assembles the answer from the streamed frames, so a
                # message that is only returned is a message nobody sees.
                answer.append(OUT_OF_STEPS)
                if on_text:
                    on_text(OUT_OF_STEPS)
    return "".join(answer)


# ---- server-facing entry point ---------------------------------------------
_CLIENT = None
_CONFIG = None


def _ensure():
    """Lazily build and cache the client + tool config (shared across requests)."""
    global _CLIENT, _CONFIG
    if _CLIENT is None:
        client = _build_client()                   # may raise RuntimeError (no key)
        _CLIENT, _CONFIG = client, _tools_config()
    return _CLIENT, _CONFIG


def _price(name):
    v = (os.environ.get(name) or "").strip()
    try:
        return float(v) if v else None
    except ValueError:
        return None


def _cost(tokens_in, tokens_out, cached=0):
    """USD cost from configured per-1M-token prices, or None when unpriced. Prices are
    model-specific and MUST come from env — never guessed, so cost is exact or absent.
    `cached` input tokens (served from an explicit cache) are billed at the cache-read
    rate GEMINI_PRICE_CACHED_PER_M when set; otherwise they fall back to the input
    rate (no discount assumed). Cache storage ($/token/hour) is a background charge,
    not folded into per-message cost."""
    pin, pout = _price("GEMINI_PRICE_IN_PER_M"), _price("GEMINI_PRICE_OUT_PER_M")
    pcached = _price("GEMINI_PRICE_CACHED_PER_M")
    if pin is None and pout is None and pcached is None:
        return None
    cached = max(0, min(cached, tokens_in))
    fresh_in = tokens_in - cached
    return round(fresh_in / 1e6 * (pin or 0)
                 + cached / 1e6 * (pcached if pcached is not None else (pin or 0))
                 + tokens_out / 1e6 * (pout or 0), 6)


def answer(history, message, on_event, on_degraded=None) -> dict:
    """Drive one chat turn for the HTTP endpoint. `history` is a list of
    {role: 'user'|'assistant'|'model', text: str}; `message` is the new user text,
    already carrying any server-built context/identity annotations. `on_event(dict)`
    receives frames: {type:'text', text}, {type:'tool', tools:[…]}, {type:'error',
    error}, and always a final {type:'done'}. Never raises. Returns a usage dict
    {tokens_in, tokens_out, tokens, tokens_cached, cost_usd} for server accounting."""
    from google.genai import types

    acc = {"input": 0, "output": 0, "total": 0, "cached": 0}
    tool_log = []

    def usage():
        return {"tokens_in": acc["input"], "tokens_out": acc["output"],
                "tokens": acc["total"] or (acc["input"] + acc["output"]),
                "tokens_cached": acc["cached"], "tool_calls": tool_log,
                "cost_usd": _cost(acc["input"], acc["output"], acc["cached"])}

    try:
        client, config = _ensure()
    except Exception as exc:                       # noqa: BLE001 — e.g. missing key
        _safe(on_event, {"type": "error", "error": str(exc)})
        _safe(on_event, {"type": "done"})
        return usage()

    contents = []
    for h in (history or []):
        role = "model" if h.get("role") in ("assistant", "model") else "user"
        contents.append(types.Content(role=role,
                                       parts=[types.Part(text=str(h.get("text", "")))]))
    contents.append(types.Content(role="user", parts=[types.Part(text=message)]))
    try:
        ask(client, config, contents,
            on_text=lambda t: on_event({"type": "text", "text": t}),
            on_tool=lambda names: on_event({"type": "tool", "tools": names}),
            usage_acc=acc, tool_log=tool_log, on_degraded=on_degraded)
    except Exception as exc:                        # noqa: BLE001 — incl. broken pipe
        _safe(on_event, {"type": "error", "error": f"{type(exc).__name__}: {exc}"})
    finally:
        _safe(on_event, {"type": "done"})
    return usage()


def _safe(fn, arg) -> None:
    try:
        fn(arg)
    except Exception:                               # noqa: BLE001 — client may be gone
        pass


def _report(sink, where: str, exc: BaseException) -> None:
    """Report a caught-and-degraded failure. Injected rather than imported: the server owns
    log_degraded and imports THIS module, so reaching back for it would close a cycle.
    Falls back to stderr with the traceback, which is what the CLI needs anyway."""
    if sink is not None:
        try:
            sink(where, exc)
            return
        except Exception:                           # noqa: BLE001 — a logger must not raise
            pass
    import traceback
    print(f"[degraded] {where}: {type(exc).__name__}: {exc}", file=sys.stderr)
    traceback.print_exc(file=sys.stderr)


def main() -> None:
    from google.genai import types
    try:
        client, config = _build_client(), _tools_config()
    except RuntimeError as exc:
        sys.exit(str(exc))
    contents: list = []

    def turn(q: str) -> None:
        contents.append(types.Content(role="user", parts=[types.Part(text=q)]))
        ask(client, config, contents, on_text=lambda t: print(t, end="", flush=True))
        print()

    if len(sys.argv) > 1:
        turn(" ".join(sys.argv[1:]))
        return
    print(f"Metrics chat ({MODEL}). Ask about the report; Ctrl-D to exit.\n")
    try:
        while True:
            q = input("› ").strip()
            if q:
                turn(q)
                print()
    except (EOFError, KeyboardInterrupt):
        print()


if __name__ == "__main__":
    main()
