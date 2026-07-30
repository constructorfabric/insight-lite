"""Guards for the Discord / Telegram contact handles on identity records.

Two things are easy to break here and both would be silent:
  · a save from a client that does not know the fields must not wipe them — the
    legacy identity editor is exactly such a client
  · the handles must stay OUT of identity resolution. The resolver attributes
    commits through email evidence and scores identity_confidence from it; a chat
    handle is not evidence of authorship, and letting it in would dilute the one
    signal on that page worth trusting.
"""
import os
import re
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import directory
import server
import store



def _src(name):
    """Read a backend module's source. Resolved from THIS file, not the working
    directory: the modules moved to backend/ and a bare Path("x.py") silently
    depended on being run from the repo root."""
    from pathlib import Path as _P
    return (_P(__file__).resolve().parents[1] / "backend" / name).read_text()


class NormalizeHandleTest(unittest.TestCase):
    def test_strips_the_at_everyone_pastes(self):
        self.assertEqual(directory.normalize_handle("telegram", "@alexey_p"), "alexey_p")
        self.assertEqual(directory.normalize_handle("telegram", "  @a_b_c  "), "a_b_c")
        self.assertEqual(directory.normalize_handle("discord", "@someuser"), "someuser")

    def test_accepts_both_discord_generations(self):
        """Discord moved from name#1234 to a plain username; both exist in the wild."""
        self.assertEqual(directory.normalize_handle("discord", "someuser"), "someuser")
        self.assertEqual(directory.normalize_handle("discord", "Old#1234"), "Old#1234")
        self.assertEqual(directory.normalize_handle("discord", "with.dots"), "with.dots")

    def test_empty_stays_empty(self):
        for v in ("", "   ", "@", None):
            self.assertEqual(directory.normalize_handle("telegram", v), "")

    def test_rejects_what_cannot_be_a_handle(self):
        for kind, bad in (("telegram", "https://t.me/x"), ("telegram", "two words"),
                          ("discord", "a" * 70), ("telegram", "cyrillic-ник")):
            with self.assertRaises(ValueError, msg=f"{kind} {bad!r}"):
                directory.normalize_handle(kind, bad)

    def test_error_message_says_what_to_do(self):
        with self.assertRaises(ValueError) as cm:
            directory.normalize_handle("telegram", "https://t.me/someone")
        self.assertIn("not a link", str(cm.exception))

    def test_normalize_contacts_drops_cleared_fields(self):
        """A cleared field must remove the key, not store an empty string — otherwise the
        override row grows `discord: ""` entries that read back as data."""
        self.assertEqual(directory.normalize_contacts({"discord": "@dz", "telegram": ""}),
                         {"discord": "dz"})
        self.assertEqual(directory.normalize_contacts({"name": "x"}), {})


class SaveRoundTripTest(unittest.TestCase):
    def _save(self, tmp, yaml_text):
        with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
            store.connect()
            server.write_people_yaml(yaml_text)
            conn = store.connect()
            return store.read_overrides(conn, "person")

    def test_handles_persist_to_the_override_row(self):
        with TemporaryDirectory() as tmp:
            ov = self._save(tmp, """
people:
  alice:
    company: Acme
    emails: [a@x.com]
    discord: "@alice_d"
    telegram: alice_t
""")
            self.assertEqual(ov["alice"]["discord"], "alice_d")
            self.assertEqual(ov["alice"]["telegram"], "alice_t")

    def test_a_bad_handle_fails_the_save_naming_the_person(self):
        """The roster is saved as one document; a rejected value must say whose it is
        or the editor cannot point at the field."""
        with TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError) as cm:
                self._save(tmp, """
people:
  bob:
    company: Acme
    emails: [b@x.com]
    telegram: "https://t.me/bob"
""")
            self.assertIn("bob", str(cm.exception))

    def test_absent_handles_do_not_create_keys(self):
        with TemporaryDirectory() as tmp:
            ov = self._save(tmp, """
people:
  carol:
    company: Acme
    emails: [c@x.com]
""")
            self.assertNotIn("discord", ov["carol"])
            self.assertNotIn("telegram", ov["carol"])


class EditorCarriesHandlesTest(unittest.TestCase):
    def test_build_roster_surfaces_stored_handles(self):
        roster = directory.build_roster(
            {"alice": {"emails": ["a@x.com"], "commits": 3}},
            {"alice": {"company": "Acme", "discord": "alice_d", "telegram": "alice_t"}})
        self.assertEqual(roster["alice"]["discord"], "alice_d")
        self.assertEqual(roster["alice"]["telegram"], "alice_t")

    def test_build_roster_defaults_to_empty_not_missing(self):
        """The editor binds inputs to these keys; a missing key renders `undefined`."""
        roster = directory.build_roster({"bob": {"emails": [], "commits": 0}}, {})
        for f in directory.CONTACT_FIELDS:
            self.assertEqual(roster["bob"][f], "")

    def test_a_reload_after_save_still_sees_them(self):
        """Used to pin this on directory.write_yaml's people.yaml backup ("dropping the
        fields there would lose them on any restore from it"). That file is gone, so the
        same property is pinned on the reload path that replaced it: what load_existing
        reads back out of the override row is what the editor repopulates from."""
        with TemporaryDirectory() as tmp:
            with patch.dict(os.environ, {"REPORT_DB": str(Path(tmp) / "t.db")}):
                server.save_people({"alice": {
                    "name": "A", "company": "Acme", "emails": ["a@x.com"],
                    "discord": "alice_d", "telegram": "alice_t"}})
                row = directory.load_existing()["alice"]
        self.assertEqual(row["discord"], "alice_d")
        self.assertEqual(row["telegram"], "alice_t")

    # test_the_legacy_editor_round_trips_them lived here until
    # templates/editors/identity.html was removed with the legacy editor layer. The
    # React editor's own savePayload() is pinned by the test above; there is no second
    # editor left to drift from it.

    def test_no_resolution_path_reads_the_handles(self):
        """The point of the design: contact attributes, not authorship evidence."""
        for module in ("identity.py", "semantic.py"):
            src = _src(module)
            for field in directory.CONTACT_FIELDS:
                self.assertNotIn(field, src, f"{module} must not read {field}")

    def test_collect_does_not_treat_them_as_identity_input(self):
        src = _src("collect.py")
        # a mention inside build_identity / confidence scoring would be the regression
        for field in directory.CONTACT_FIELDS:
            for line in src.splitlines():
                if field in line and "confidence" in line.lower():
                    self.fail(f"collect.py mixes {field} into confidence: {line.strip()}")


if __name__ == "__main__":
    unittest.main()


class PayloadCarriesHandlesTest(unittest.TestCase):
    """The regression that unit tests on build_roster could not see.

    A person field had to be listed in FOUR places: build_roster, editor_payload, and
    each editor's own YAML serialiser. The serialisers are gone (both editors POST the
    roster as JSON), so it is two — but missing it in editor_payload is still invisible
    to a roster test and surfaces only as a field that saves fine and then reads back
    empty, which is exactly what happened, caught in a browser rather than here. So the
    payload is pinned too.
    """

    def test_editor_payload_includes_the_handles(self):
        payload = directory.editor_payload(
            {"alice": {"name": "A", "company": "Acme", "emails": [], "aliases": [],
                       "commits": 1, "is_member": True,
                       "discord": "a_d", "telegram": "a_t"}},
            {"bots": {}})
        entry = payload["people"][0]
        self.assertEqual(entry["discord"], "a_d")
        self.assertEqual(entry["telegram"], "a_t")

    def test_payload_always_has_the_keys_even_when_unset(self):
        payload = directory.editor_payload(
            {"bob": {"name": "", "company": "Other", "emails": [], "aliases": [],
                     "commits": 0, "is_member": False}},
            {"bots": {}})
        entry = payload["people"][0]
        for f in directory.CONTACT_FIELDS:
            self.assertIn(f, entry)
            self.assertEqual(entry[f], "")

    def test_every_contact_field_reaches_every_layer(self):
        """Adding a third handle later must not silently stop at one layer."""
        roster = directory.build_roster({"z": {"emails": [], "commits": 0}}, {})
        payload_entry = directory.editor_payload(
            {"z": {**roster["z"], "company": "Other"}}, {"bots": {}})["people"][0]
        editor_js = Path("frontend/src/pages/IdentityEditor.tsx").read_text()
        for f in directory.CONTACT_FIELDS:
            self.assertIn(f, roster["z"], f"build_roster drops {f}")
            self.assertIn(f, payload_entry, f"editor_payload drops {f}")
            self.assertIn(f, editor_js, f"the React editor never mentions {f}")
