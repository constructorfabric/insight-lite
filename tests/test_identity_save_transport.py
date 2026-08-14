"""The identity save transport: JSON in, no hand-written YAML anywhere.

The editors used to BUILD a people.yaml by string concatenation and POST it as the
save body, which the server parsed back. `out += "    company: " + p.company` with a
company typed into a free-text prompt meant the YAML scanner read parts of the value
as syntax. Measured by parsing what the editor emitted:

    "Acme"      -> "Acme"
    "Foo: bar"  -> ScannerError, the whole save fails
    "Acme #1"   -> "Acme"      ('#' opened a comment — silent)
    "yes"       -> True        (coerced to a boolean — silent)
    "no"        -> False
    "*star"     -> ComposerError ("undefined alias")

Two of those corrupt data with no error at all. The transport is JSON now; these tests
pin the round-trip, the guards that must survive the change (empty roster, one email
per person, the concurrency token, contact normalisation), the roster-drop guard that a
truncated payload has to fail, and — since 2026-07-28 — that a save touches the
override table and NOTHING on disk.
"""
import http.client
import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import yaml

import directory
import server
import store



def _src(name):
    """Read a backend module's source. Resolved from THIS file, not the working
    directory: the modules moved to backend/ and a bare Path("x.py") silently
    depended on being run from the repo root."""
    from pathlib import Path as _P
    return (_P(__file__).resolve().parents[1] / "backend" / name).read_text()


class QuietHandler(server.Handler):
    def log_message(self, fmt: str, *args) -> None:
        pass


def _roster(n: int, company: str = "Acme") -> dict:
    return {f"dev{i}": {"company": company, "emails": [f"dev{i}@x.com"]} for i in range(n)}


class SavedTempStore:
    """A temp DB, so nothing here touches the real report.db. `people_yaml` /
    `people_backups` are kept as ABSENCE probes: a save must not create either."""

    def __init__(self, tmp: str):
        self.tmp = Path(tmp)
        self.env = patch.dict(os.environ, {"REPORT_DB": str(self.tmp / "t.db")})
        self.people_yaml = self.tmp / "people.yaml"
        self.people_backups = self.tmp / "history" / "people"

    def __enter__(self):
        self.env.start()
        store.connect().close()
        return self

    def __exit__(self, *exc):
        self.env.stop()
        return False

    def overrides(self) -> dict:
        conn = store.connect()
        try:
            return store.read_overrides(conn, "person")
        finally:
            conn.close()

    def stored_json(self) -> dict:
        """The raw `value` column per login — the bytes a restore actually reads back."""
        conn = store.connect()
        try:
            return {r["key"]: json.loads(r["value"]) for r in
                    conn.execute("SELECT key, value FROM override WHERE scope='person'")}
        finally:
            conn.close()


class CompanyRoundTripTest(unittest.TestCase):
    """The values the YAML transport silently rewrote must come back byte-identical."""

    CASES = ["Acme", "Acme #1", "Foo: bar", "yes", "no", "*star", "Acme: Inc  ",
             "#1 Consulting", "true", "null", "0755", "{brace}", "- dash"]

    def test_the_yaml_transport_really_did_corrupt_them(self):
        """Guard the premise: this is the emitter that used to run in the browser."""
        emitted = {}
        for co in self.CASES:
            text = "people:\n  alice:\n    company: " + co + "\n    emails: []\n"
            try:
                emitted[co] = yaml.safe_load(text)["people"]["alice"]["company"]
            except yaml.YAMLError:
                emitted[co] = "<save failed>"
        self.assertEqual(emitted["Acme #1"], "Acme")      # silent truncation
        self.assertIs(emitted["yes"], True)               # silent boolean coercion
        self.assertIs(emitted["no"], False)
        self.assertEqual(emitted["Foo: bar"], "<save failed>")
        self.assertEqual(emitted["*star"], "<save failed>")

    def test_json_transport_round_trips_every_case(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            people = {f"dev{i}": {"company": co, "emails": [f"dev{i}@x.com"]}
                      for i, co in enumerate(self.CASES)}
            server.save_people(people)
            ov = st.overrides()
            for i, co in enumerate(self.CASES):
                self.assertEqual(ov[f"dev{i}"]["company"], co, f"company {co!r} was rewritten")

    def test_the_stored_representation_reparses_to_the_same_values(self):
        """Used to pin this on the safe_dump'd people.yaml backup. That file is gone, so
        the property is pinned where the values actually persist: the override row's JSON
        is what a restore from a report.db snapshot reads back."""
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            people = {f"dev{i}": {"company": co, "emails": [f"dev{i}@x.com"]}
                      for i, co in enumerate(self.CASES)}
            server.save_people(people)
            back = st.stored_json()
            for i, co in enumerate(self.CASES):
                self.assertEqual(back[f"dev{i}"]["company"], co)

    def test_names_and_handles_survive_the_same_way(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            server.save_people({"alice": {"name": "Alice: #1 \"the\" dev",
                                          "company": "Acme #1",
                                          "emails": ["a@x.com"],
                                          "discord": "@alice_d", "telegram": "alice_t"}})
            row = st.overrides()["alice"]
            self.assertEqual(row["name"], "Alice: #1 \"the\" dev")
            self.assertEqual(row["discord"], "alice_d")      # normalisation still runs
            self.assertEqual(row["telegram"], "alice_t")


class PreservedGuardsTest(unittest.TestCase):
    """Everything write_people_yaml guarded before must still guard."""

    def test_empty_roster_is_refused(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            for payload in ({}, None, [], "people"):
                with self.assertRaises(ValueError):
                    server.save_people(payload)

    def test_one_email_belongs_to_one_person(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            with self.assertRaises(ValueError) as cm:
                server.save_people({"alice": {"emails": ["shared@x.com"]},
                                    "bob": {"emails": ["Shared@X.com"]}})
            self.assertIn("shared@x.com", str(cm.exception).lower())

    def test_a_bad_handle_names_the_person(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            with self.assertRaises(ValueError) as cm:
                server.save_people({"bob": {"telegram": "https://t.me/bob"}})
            self.assertIn("bob", str(cm.exception))

    def test_json_types_are_checked_not_stored_blindly(self):
        """JSON can carry shapes YAML never did; the override value is read back as a
        roster, so a nested object in `emails` must be rejected at the door."""
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            for bad in ({"company": {"x": 1}}, {"emails": "a@x.com"},
                        {"emails": [{"a": 1}]}, {"name": 7}, {"aliases": "alt"}):
                with self.assertRaises(ValueError, msg=f"{bad!r} was accepted"):
                    server.save_people({"alice": bad})

    def test_a_save_writes_the_override_row_and_no_file(self):
        """Used to assert people.yaml plus a dated copy under history/people/ were
        written on every save. That was the leak: the suite's own saves overwrote all 50
        of the checkout's dated copies with `alice`/`bob`, so when a roster had to be
        recovered there was no real backup left. Same properties, inverted — the
        curation lands in the row (including the bot mark, which the backup carried) and
        nothing lands on disk."""
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            server.save_people({"alice": {"company": "Acme", "emails": ["a@x.com"],
                                          "is_bot": False}})
            self.assertIs(st.overrides()["alice"]["is_bot"], False)
            server.save_people({"alice": {"company": "Acme", "emails": ["a@x.com"]},
                                "bob": {"company": "Acme", "emails": ["b@x.com"]}})
            self.assertEqual(sorted(st.overrides()), ["alice", "bob"])
            self.assertFalse(st.people_yaml.exists(), "a save wrote a people.yaml again")
            self.assertFalse(st.people_backups.exists(),
                             "a save wrote a dated history/people/ copy again")


class RosterDropGuardTest(unittest.TestCase):
    """A save REPLACES the person scope. A short payload silently deletes curation —
    a single-person body was accepted during testing and took a local roster with it."""

    def _seed(self, n: int) -> None:
        server.save_people(_roster(n))

    def test_a_truncated_payload_is_refused_and_says_what_it_would_cost(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            self._seed(200)
            with self.assertRaises(ValueError) as cm:
                server.save_people(_roster(1))
            msg = str(cm.exception)
            self.assertIn("199", msg)             # how many would be lost
            self.assertIn("200", msg)
            self.assertIn("X-Allow-Drop", msg)    # how to do it deliberately
            self.assertEqual(len(st.overrides()), 200)   # nothing was written

    def test_a_legitimate_single_person_deletion_passes(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            self._seed(30)
            keep = _roster(30)
            keep.pop("dev7")                      # e.g. a merge folded dev7 into dev8
            server.save_people(keep)
            self.assertEqual(len(st.overrides()), 29)
            self.assertNotIn("dev7", st.overrides())

    def test_the_budget_scales_with_the_roster(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            self._seed(200)
            server.save_people(_roster(180))      # 20 dropped == 10% — a merge session
            with self.assertRaises(ValueError):
                server.save_people(_roster(160))  # 20 of 180 is over the budget

    def test_a_small_roster_keeps_a_floor_of_two(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            self._seed(6)
            server.save_people(_roster(4))        # 10% of 6 rounds to 0; the floor allows 2
            with self.assertRaises(ValueError):
                server.save_people(_roster(1))    # 3 of 4 is a truncation

    def test_an_explicit_confirmation_of_the_exact_count_passes(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            self._seed(50)
            with self.assertRaises(ValueError):
                server.save_people(_roster(2), allow_drop=10)   # stale/blanket → still no
            server.save_people(_roster(2), allow_drop=48)
            self.assertEqual(len(st.overrides()), 2)

    def test_growing_the_roster_is_never_guarded(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            self._seed(3)
            server.save_people(_roster(300))
            self.assertEqual(len(st.overrides()), 300)


class NoYamlReadPathTest(unittest.TestCase):
    """Each of these puts a readable people.yaml exactly where the removed fallback
    looked and then breaks the store. The old code returned the file's roster; the
    current code has to raise."""

    def _stale_roster_on_disk(self, tmp: str):
        import paths
        Path(tmp, "people.yaml").write_text(
            "people:\n  ghost:\n    company: Gone\n    emails: []\n")
        return patch.object(paths, "DATA_DIR", Path(tmp))

    def test_an_unreadable_store_raises_instead_of_serving_a_stale_roster(self):
        """The fallback was a data-loss path: the editor would load the people.yaml
        copy and the next Save (a full replace) would make that stale copy canonical."""
        with TemporaryDirectory() as tmp:
            with self._stale_roster_on_disk(tmp), \
                 patch.object(store, "connect", side_effect=OSError("disk gone")):
                with self.assertRaises(OSError):
                    directory.load_existing()

    def test_collect_reads_the_same_way_with_no_fallback_either(self):
        """collect.load_person_overrides had the sibling fallback. A collect that
        silently ran on a stale roster would mis-attribute the whole window and then
        write that attribution into the run blob."""
        import collect
        with TemporaryDirectory() as tmp:
            with self._stale_roster_on_disk(tmp), \
                 patch.object(store, "connect", side_effect=OSError("disk gone")):
                with self.assertRaises(OSError):
                    collect.load_person_overrides()

    def test_a_people_yaml_beside_the_db_is_never_imported(self):
        """Inverts what this file used to assert. Seeding an EMPTY scope from the file
        read as safe — it cannot overwrite curation — but "only when empty" does not make
        the file trustworthy, it makes whatever it happens to hold into curated data. A
        test had written its fixture over the checkout's people.yaml and the seed imported
        it: prod carried `person/alice -> {"company": "Constructor"}` with 0 commits, 0
        PRs and no row in the person dim."""
        import configstore
        import paths
        with TemporaryDirectory() as tmp:
            # both files carrying exactly what the incident's fixtures carried, placed
            # where the seed looked. paths.DATA_DIR is patched, not the env var: it binds
            # at import, so an env patch alone would leave this test asserting nothing
            # (conftest re-pins it after every test).
            Path(tmp, "people.yaml").write_text(
                "people:\n  alice:\n    company: Constructor\n    emails: [a@x.com]\n")
            Path(tmp, "config.local.yaml").write_text("repo_class: {lib: sdk}\n")
            with patch.object(paths, "DATA_DIR", Path(tmp)), \
                 patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                self.assertEqual(directory.load_existing(), {})
                self.assertNotIn("repo_class", configstore.load_overlay())
            self.assertFalse(hasattr(store, "seed_overrides_from_yaml"))


class SaveEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=2)

    def post(self, body: bytes, ctype="application/json", extra=None):
        headers = {"Content-Type": ctype, "Content-Length": str(len(body))}
        # A real editor echoes the version it loaded, and the endpoint now REQUIRES it.
        # Default to the CURRENT version (a client that just reloaded) so a plain save
        # succeeds; a test that wants to simulate a stale tab passes its own via `extra`.
        if not (extra and "X-Override-Version" in extra):
            import store
            conn = store.connect()
            try:
                headers["X-Override-Version"] = store.overrides_version(conn, ("person",))
            finally:
                conn.close()
        headers.update(extra or {})
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("POST", "/api/people-yaml", body=body, headers=headers)
        r = conn.getresponse()
        payload = r.read()
        conn.close()
        return r.status, json.loads(payload)

    def post_json(self, people: dict, extra=None):
        return self.post(json.dumps({"people": people}).encode(), extra=extra)

    def setUp(self):
        # the apply step is a full reindex of the collected data — out of scope here,
        # and it must not run against the real store
        self.reindex = patch("reindex.apply", return_value={})
        self.reindex.start()
        self.addCleanup(self.reindex.stop)

    def test_a_json_body_saves_and_round_trips_a_hostile_company(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            status, body = self.post_json({"alice": {"company": "Acme #1",
                                                     "emails": ["a@x.com"],
                                                     "telegram": "@alice_t"}})
            self.assertEqual(status, 200)
            self.assertTrue(body["ok"])
            self.assertTrue(body["version"])
            row = st.overrides()["alice"]
            self.assertEqual(row["company"], "Acme #1")
            self.assertEqual(row["telegram"], "alice_t")

    def test_the_concurrency_token_still_rejects_a_stale_tab(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            self.post_json({"alice": {"company": "Acme", "emails": ["a@x.com"]}})
            status, body = self.post_json({"alice": {"company": "Other", "emails": []}},
                                          extra={"X-Override-Version": "stale-token"})
            self.assertEqual(status, 409)
            self.assertIn("reload", body["error"])

    def test_the_drop_guard_answers_400_and_the_header_overrides_it(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            self.post_json(_roster(40))
            status, body = self.post_json(_roster(1))
            self.assertEqual(status, 400)
            self.assertIn("X-Allow-Drop", body["error"])
            self.assertEqual(len(st.overrides()), 40)
            status, body = self.post_json(_roster(1), extra={"X-Allow-Drop": "39"})
            self.assertEqual(status, 200)
            self.assertEqual(len(st.overrides()), 1)

    def test_malformed_and_empty_json_are_400_not_500(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp):
            for body in (b"{not json", b'{"people": {}}', b'{"people": null}', b'[1,2]'):
                status, payload = self.post(body)
                self.assertEqual(status, 400, f"{body!r} → {status}")
                self.assertFalse(payload["ok"])

    def test_a_yaml_body_is_still_accepted_for_a_pre_json_tab(self):
        with TemporaryDirectory() as tmp, SavedTempStore(tmp) as st:
            body = b"people:\n  alice:\n    company: Constructor\n    emails: []\n"
            status, payload = self.post(body, ctype="text/yaml")
            self.assertEqual(status, 200)
            self.assertTrue(payload["ok"])
            self.assertEqual(st.overrides()["alice"]["company"], "Constructor")


class NoHandRolledYamlLeftTest(unittest.TestCase):
    # One editor now: templates/editors/identity.html went with the legacy layer.
    EDITORS = ("frontend/src/pages/IdentityEditor.tsx",)

    def test_the_editor_serialises_no_yaml_and_posts_json(self):
        for path in self.EDITORS:
            src = Path(path).read_text()
            self.assertNotIn("toYaml", src, f"{path} still builds YAML by hand")
            self.assertNotIn("text/yaml", src, f"{path} still posts YAML")
            self.assertIn("application/json", src, f"{path} does not post JSON")
            # the body is a serialised object, not text assembled from values
            self.assertRegex(src, r"JSON\.stringify\(savePayload\(\)\)",
                             f"{path} does not send the roster as JSON data")

    def test_no_yaml_emitter_is_left_at_all(self):
        """directory.dump_yaml was the one remaining emitter, for the backup file. Both
        are gone, so there is no quoting implementation left to get wrong."""
        for name in ("dump_yaml", "write_yaml"):
            self.assertFalse(hasattr(directory, name), f"directory.{name} is back")
        self.assertNotIn("dump_yaml", _src("server.py"))


if __name__ == "__main__":
    unittest.main()
