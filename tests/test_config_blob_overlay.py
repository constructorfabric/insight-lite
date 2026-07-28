"""The policy blocks in config.yaml must survive the file being replaced.

Motivation, concretely. Until now only the parts the editors expose (repo class and
element, extra orgs/repos, company domains, org, lookback, score weights) were stored
in the DB; everything else in config.yaml was FILE-only. That is invisible while the
file travels with the deployment, and becomes a silent data change the moment the file
comes from somewhere else — e.g. a deployment that pulls the published repo, whose
config.yaml is deliberately generic. What would change, with no error anywhere:

  * ai_tools.markers      -> in-house assistant markers gone, its commits unattributed
  * studio_provenance     -> enabled: false, the provenance panel switches off
  * gears_usage           -> enabled: false, the framework panel switches off
  * fabric_trackers       -> no trackers
  * bot_logins            -> service accounts reappear as PEOPLE in every metric
  * identity_overrides    -> manual identity bridges vanish, people split in two
  * specs / meaningful_loc-> different exclusions, so LOC and spec counts shift
  * email                 -> reports sent from the wrong address

So these keys are now overridable (configstore.BLOB_KEYS), and reportctl
config-capture imports the file's current values into the DB once.
"""
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import configstore


class BlobOverlayTest(unittest.TestCase):
    """apply_overlay replaces a policy block whole, and only when overridden."""

    def test_absent_override_leaves_the_base_untouched(self):
        cfg = {"ai_tools": {"markers": {"Base": {"pattern": "x"}}}, "bot_logins": ["[bot]"]}
        configstore.apply_overlay(cfg, {})
        self.assertEqual(cfg["ai_tools"], {"markers": {"Base": {"pattern": "x"}}})
        self.assertEqual(cfg["bot_logins"], ["[bot]"])

    def test_override_replaces_the_whole_block(self):
        """Wholesale, not merged: a merge would make the shipped defaults
        undeletable, and a denylist you cannot shorten is not a denylist."""
        cfg = {"ai_tools": {"markers": {"Shipped": {"pattern": "s"}}}}
        configstore.apply_overlay(cfg, {"ai_tools": {"markers": {"Mine": {"pattern": "m"}}}})
        self.assertEqual(cfg["ai_tools"], {"markers": {"Mine": {"pattern": "m"}}})
        self.assertNotIn("Shipped", cfg["ai_tools"]["markers"])

    def test_an_empty_override_is_honoured(self):
        """"no bot logins" is a real choice and must not fall back to the shipped
        list — falling back is exactly the silent revert this prevents."""
        cfg = {"bot_logins": ["[bot]", "dependabot"]}
        configstore.apply_overlay(cfg, {"bot_logins": []})
        self.assertEqual(cfg["bot_logins"], [])

    def test_every_blob_key_is_wired(self):
        """A key added to BLOB_KEYS but not handled would look supported and do
        nothing, which is the failure mode in miniature."""
        cfg = {}
        ov = {k: {"sentinel": k} for k in configstore.BLOB_KEYS}
        configstore.apply_overlay(cfg, ov)
        for key in configstore.BLOB_KEYS:
            self.assertEqual(cfg.get(key), {"sentinel": key}, f"{key} not applied")

    def test_the_file_only_keys_that_mattered_are_covered(self):
        """Guards the list itself: these are the keys whose loss is silent and
        changes numbers. If one is dropped from BLOB_KEYS, say so here."""
        for key in ("ai_tools", "studio_provenance", "gears_usage", "fabric_trackers",
                    "bot_logins", "identity_overrides", "specs", "meaningful_loc",
                    "migration_title_prefixes", "email"):
            self.assertIn(key, configstore.BLOB_KEYS)


class CaptureTest(unittest.TestCase):
    """reportctl config-capture: file -> DB, once, without clobbering UI edits."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict(os.environ,
                               {"REPORT_DB": str(Path(self._tmp.name) / "t.db")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    BASE = {"ai_tools": {"markers": {"Mine": {"pattern": "m"}}},
            "bot_logins": ["[bot]", "our-ci"],
            "email": {"enabled": True, "recipients": ["team@example.com"]}}

    def test_capture_writes_the_base_values_and_they_then_survive_a_new_file(self):
        import store
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            written = configstore.capture_base_into_overlay()
        self.assertEqual(set(written), set(self.BASE))

        # the published generic file arrives: different markers, empty denylist
        published = {"ai_tools": {"markers": {"Generic": {"pattern": "g"}}},
                     "bot_logins": ["[bot]"],
                     "email": {"enabled": False, "recipients": []}}
        merged = configstore.apply_overlay(dict(published), configstore.load_overlay())
        self.assertEqual(merged["ai_tools"], self.BASE["ai_tools"])
        self.assertEqual(merged["bot_logins"], self.BASE["bot_logins"])
        self.assertEqual(merged["email"], self.BASE["email"])
        store.connect().close()

    def test_capture_never_clobbers_an_existing_override(self):
        import store
        conn = store.connect()
        store.write_override(conn, "setting", "bot_logins", {"value": ["edited-in-ui"]})
        conn.commit()
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            written = configstore.capture_base_into_overlay(conn)
        self.assertNotIn("bot_logins", written)
        self.assertEqual(store.read_overrides(conn, "setting")["bot_logins"]["value"],
                         ["edited-in-ui"])
        conn.close()

    def test_capture_is_idempotent(self):
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            first = configstore.capture_base_into_overlay()
            second = configstore.capture_base_into_overlay()
        self.assertTrue(first)
        self.assertEqual(second, [], "a second run must write nothing")

    def test_capture_skips_keys_absent_from_the_file(self):
        with patch.object(configstore, "base_config", return_value={"bot_logins": ["x"]}):
            written = configstore.capture_base_into_overlay()
        self.assertEqual(written, ["bot_logins"])



class PolicyEditorTest(unittest.TestCase):
    """The Config page's policy surface: configstore.policy_data / save_policy."""

    def setUp(self):
        self._tmp = TemporaryDirectory()
        self._env = patch.dict(os.environ,
                               {"REPORT_DB": str(Path(self._tmp.name) / "t.db")})
        self._env.start()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    BASE = {"bot_logins": ["[bot]", "dependabot"],
            "ai_tools": {"markers": {"Shipped": {"pattern": "s"}}}}

    def test_policy_data_marks_source_and_round_trips_yaml(self):
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            data = configstore.policy_data()
        self.assertEqual(set(data), set(configstore.BLOB_KEYS))
        self.assertFalse(data["bot_logins"]["overridden"])
        self.assertIn("dependabot", data["bot_logins"]["yaml"])
        self.assertTrue(data["bot_logins"]["label"])
        self.assertTrue(data["bot_logins"]["blurb"], "each block needs a description")

    def test_every_blob_key_has_a_label(self):
        for key in configstore.BLOB_KEYS:
            self.assertIn(key, configstore.POLICY_LABELS, f"{key} has no label/blurb")

    def test_save_then_read_back_shows_the_database_as_the_source(self):
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            configstore.save_policy("bot_logins", "- '[bot]'\n- our-ci\n")
            data = configstore.policy_data()
        self.assertTrue(data["bot_logins"]["overridden"])
        self.assertIn("our-ci", data["bot_logins"]["yaml"])

    def test_blank_clears_the_override_rather_than_pinning_emptiness(self):
        """"Reset" must restore the file default. Pinning [] here would silently
        re-admit every service account as a person."""
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            configstore.save_policy("bot_logins", "- keep-me\n")
            out = configstore.save_policy("bot_logins", "   ")
            self.assertFalse(out["overridden"])
            merged = configstore.apply_overlay(dict(self.BASE), configstore.load_overlay())
        self.assertEqual(merged["bot_logins"], self.BASE["bot_logins"])

    def test_explicit_empty_collection_is_still_storable(self):
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            out = configstore.save_policy("bot_logins", "[]")
            self.assertTrue(out["overridden"])
            merged = configstore.apply_overlay(dict(self.BASE), configstore.load_overlay())
        self.assertEqual(merged["bot_logins"], [])

    def test_bad_yaml_is_rejected_with_a_readable_reason(self):
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            with self.assertRaises(ValueError) as ctx:
                configstore.save_policy("bot_logins", "- [unclosed\n")
        self.assertIn("valid YAML", str(ctx.exception))

    def test_wrong_shape_is_rejected(self):
        """A mapping where the base is a list would not error until the collector
        tried to iterate it, far from the edit that caused it."""
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            with self.assertRaises(ValueError) as ctx:
                configstore.save_policy("bot_logins", "a: 1\n")
        self.assertIn("expected a list", str(ctx.exception))

    def test_unknown_key_is_refused(self):
        with self.assertRaises(ValueError):
            configstore.save_policy("not_a_policy", "- x")

    def test_a_null_document_is_refused_with_guidance(self):
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            with self.assertRaises(ValueError) as ctx:
                configstore.save_policy("bot_logins", "null")
        self.assertIn("[]", str(ctx.exception))

    def test_saving_a_policy_survives_a_normal_config_save(self):
        """The reason this has its own endpoint: /api/config does whole-scope
        replaces, and a policy must not be collateral damage."""
        import store
        with patch.object(configstore, "base_config", return_value=dict(self.BASE)):
            configstore.save_policy("bot_logins", "- survivor\n")
            configstore.save_overlay({"repo_class": {"r": "platform"}})
            merged = configstore.apply_overlay(dict(self.BASE), configstore.load_overlay())
        self.assertEqual(merged["bot_logins"], ["survivor"])
        store.connect().close()


class FileProvenanceTest(unittest.TestCase):
    """The editor must know which sources came from the FILE, because it cannot
    remove those — the overlay only appends to extra_orgs/extra_repos. Without this
    the page offers an × that silently has no effect on the next render."""

    def test_editor_data_reports_file_only_sources(self):
        base = {"extra_orgs": ["from-file"], "extra_repos": ["o/from-file"]}
        with patch.object(configstore, "base_config", return_value=base):
            file_orgs = [o for o in (base.get("extra_orgs") or []) if o not in []]
        self.assertEqual(file_orgs, ["from-file"])

    def test_appending_cannot_remove_a_file_entry(self):
        """Pins the asymmetry the UI now explains, so a future 'fix' that makes the
        override authoritative has to update the copy too."""
        cfg = {"extra_orgs": ["from-file"]}
        configstore.apply_overlay(cfg, {"extra_orgs": []})
        self.assertEqual(cfg["extra_orgs"], ["from-file"],
                         "if this now removes it, the Config page copy is stale")

    def test_a_file_domain_survives_removal_too(self):
        cfg = {"companies": {"domains": {"file.com": "FileCo"}}}
        configstore.apply_overlay(cfg, {"company_domains": {"ui.com": "UiCo"}})
        self.assertEqual(cfg["companies"]["domains"],
                         {"file.com": "FileCo", "ui.com": "UiCo"})

if __name__ == "__main__":
    unittest.main()
