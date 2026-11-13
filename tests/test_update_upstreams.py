import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from scripts import update_upstreams as updater


class VersionTests(unittest.TestCase):
    def test_largest_change_wins(self):
        cases = [
            ([("2.9.0", "2.10.0"), ("0.1.0", "0.1.1")], "v1.1.0"),
            ([("2.9.0", "2.9.1"), ("0.1.0", "0.2.0")], "v1.1.0"),
            ([("2.9.0", "3.0.0"), ("0.1.0", "0.2.0")], "v2.0.0"),
            ([("2.9.0", "2.9.0"), ("0.1.0", "0.1.1")], "v1.0.2"),
            ([("2.9.0", "2.9.1"), ("0.1.0", "0.1.0")], "v1.0.2"),
            ([("2.9.0", "2.15.7"), ("0.1.0", "0.1.8")], "v1.1.0"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                self.assertEqual(updater.next_version("v1.0.1", changes), expected)

    def test_numeric_tag_order_and_prerelease_filter(self):
        self.assertEqual(
            updater.latest_tag(["v2.9.9", "v2.10.0", "v3.0.0-rc.1", "nightly"]),
            "v2.10.0",
        )
        self.assertEqual(updater.latest_tag(["0.1.0", "v0.2.0"]), "v0.2.0")

    def test_invalid_or_unchanged_versions_fail(self):
        for value in ["v1.2", "v01.2.3", "1.2.3-rc.1", "1.2.3+build"]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                updater.version(value)
        with self.assertRaises(ValueError):
            updater.latest_tag(["nightly", "v1.0.0-rc.1"])
        with self.assertRaises(ValueError):
            updater.next_version("v1.0.1", [("2.0.0", "1.9.0")])
        with self.assertRaises(ValueError):
            updater.next_version("v1.0.1", [("2.0.0", "2.0.0")])

    def test_upstream_discovery_uses_every_tag_page(self):
        pages = '[[{"name": "v0.9.9"}], [{"name": "v0.10.0"}, {"name": "v1.0.0-rc.1"}]]'
        with patch.object(updater, "run", return_value=pages) as command:
            self.assertEqual(updater.upstream_version("sjtug/cerberus"), "v0.10.0")
        command.assert_called_once_with(
            "gh", "api", "--paginate", "--slurp",
            "repos/sjtug/cerberus/tags?per_page=100",
        )


HASH = "sha256-9DfWN6AU3zfa6I2oHJgRvo7SpO3rqTzKM09pMBqA70Y="
HASH_LOG = (
    "error: hash mismatch in fixed-output derivation "
    "'/nix/store/abc-caddy-src-with-plugins-def-2.10.0.drv':\n"
    f"  specified: {updater.FAKE_HASH}\n"
    f"        got: {HASH}\n"
)


class HashTests(unittest.TestCase):
    def test_only_accepts_the_expected_source_mismatch(self):
        self.assertEqual(updater.plugin_hash(HASH_LOG), HASH)
        for log in [
            "error: network unavailable",
            HASH_LOG.replace("caddy-src-with-plugins", "unrelated-source"),
            HASH_LOG.replace(updater.FAKE_HASH, HASH),
            HASH_LOG + HASH_LOG,
        ]:
            with self.subTest(log=log), self.assertRaises(ValueError):
                updater.plugin_hash(log)

    def test_duplicate_pins_are_rejected(self):
        with self.assertRaises(ValueError):
            updater.replace_pin('hash = "a";\nhash = "b";', "hash", HASH)


README_TEMPLATE = (
    "Version rule example: Caddy 2.9.0, Cerberus v0.1.0, package v1.0.1\n"
    "<!-- BEGIN GENERATED PINS -->\nold pins\n<!-- END GENERATED PINS -->\n"
    "Keep this explanation.\n"
    "<!-- BEGIN GENERATED INSTALL -->\nold command\n<!-- END GENERATED INSTALL -->\n"
)


class ReadmeTests(unittest.TestCase):
    def test_updates_pins_link_and_install_without_changing_examples(self):
        updated = updater.update_readme(
            README_TEMPLATE, "example/project", "v1.1.0", "2.10.0", "v0.1.1", "b" * 40
        )
        self.assertIn("| Package release | `v1.1.0` |", updated)
        self.assertIn("| Caddy | `2.10.0` |", updated)
        self.assertIn("| Cerberus | `v0.1.1` |", updated)
        self.assertIn(f'| nixpkgs | `{"b" * 40}` |', updated)
        self.assertIn(
            "[cerberus-example]: https://github.com/SJTUG/cerberus/blob/v0.1.1/Caddyfile",
            updated,
        )
        self.assertIn("nix profile install github:example/project/v1.1.0", updated)
        self.assertIn(README_TEMPLATE.splitlines()[0], updated)
        self.assertIn("Keep this explanation.", updated)
        self.assertNotIn("old pins", updated)
        self.assertNotIn("old command", updated)

    def test_missing_duplicate_or_malformed_markers_fail(self):
        for name in ("PINS", "INSTALL"):
            start = f"<!-- BEGIN GENERATED {name} -->"
            end = f"<!-- END GENERATED {name} -->"
            for text in (
                README_TEMPLATE.replace(start, ""),
                README_TEMPLATE.replace(end, ""),
                README_TEMPLATE + start,
                README_TEMPLATE + end,
                README_TEMPLATE.replace(start + "\n", "").replace(end, end + "\n" + start),
                README_TEMPLATE.replace(start + "\n", start + " "),
            ):
                with self.subTest(name=name, text=text), self.assertRaises(ValueError):
                    updater.update_readme(
                        text, "example/project", "v1.1.0", "2.10.0", "v0.1.1", "b" * 40
                    )


class PrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.old_cwd = Path.cwd()
        os.chdir(self.temp.name)
        self.addCleanup(os.chdir, self.old_cwd)
        self.flake = (
            'description = "Caddy 2.9.0 with the Cerberus plugin";\n'
            f'inputs.nixpkgs.url = "github:NixOS/nixpkgs/{"a" * 40}";\n'
            'expectedCaddyVersion = "2.9.0";\n'
            'cerberusVersion = "v0.1.0";\n'
            f'hash = "{HASH}";\n'
        )
        Path("flake.nix").write_text(self.flake)
        Path("README.md").write_text(README_TEMPLATE)
        self.env = patch.dict(os.environ, {
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_OUTPUT": str(Path("outputs").resolve()),
        })
        self.env.start()
        self.addCleanup(self.env.stop)
        self.published = [{"tag_name": "v1.0.1", "draft": False, "prerelease": False}]
        self.caddy = "v2.10.0"
        self.cerberus = "v0.1.1"
        self.packaged_caddy = "2.10.0"
        self.tags = "v1.0.1"

    def api(self, endpoint):
        if endpoint == "repos/example/project/releases?per_page=100":
            return self.published
        if endpoint == "repos/caddyserver/caddy/tags?per_page=100":
            return [{"name": self.caddy}, {"name": "v3.0.0-rc.1"}]
        if endpoint == "repos/sjtug/cerberus/tags?per_page=100":
            return [{"name": self.cerberus}]
        self.fail(f"Unexpected API endpoint: {endpoint}")

    def command(self, *args):
        if args == ("git", "tag", "--merged", "HEAD"):
            return "v1.0.1"
        if args == ("git", "tag", "--list"):
            return self.tags
        if args == ("git", "show", "v1.0.1:flake.nix"):
            return self.flake
        if args == ("gh", "api", "repos/NixOS/nixpkgs/commits/master"):
            return '{"sha": "' + "b" * 40 + '"}'
        if args == (
            "nix", "eval", "--raw",
            f'github:NixOS/nixpkgs/{"b" * 40}#legacyPackages.x86_64-linux.caddy.version',
        ):
            return self.packaged_caddy
        self.fail(f"Unexpected command: {args}")

    def execute(self, *, build_failure=False, hash_log=HASH_LOG):
        def nix(args, **kwargs):
            if ".#packages.x86_64-linux.caddy-cerberus.src" in args:
                return subprocess.CompletedProcess(args, 1, stdout=hash_log)
            if build_failure and args[:3] == ["nix", "flake", "check"]:
                raise subprocess.CalledProcessError(1, args)
            return subprocess.CompletedProcess(args, 0)

        with (
            patch.object(updater, "api_items", side_effect=self.api),
            patch.object(updater, "run", side_effect=self.command),
            patch.object(updater.subprocess, "run", side_effect=nix) as commands,
            contextlib.redirect_stdout(io.StringIO()),
        ):
            updater.prepare()
        return [call.args[0] for call in commands.call_args_list]

    def test_full_update_validates_before_emitting_tag(self):
        commands = self.execute()
        self.assertEqual(Path("outputs").read_text(), "tag=v1.1.0\nchanged=true\n")
        self.assertEqual(updater.pins(Path("flake.nix").read_text()), ("2.10.0", "v0.1.1"))
        self.assertNotIn(updater.FAKE_HASH, Path("flake.nix").read_text())
        self.assertIn("b" * 40, Path("README.md").read_text())
        self.assertIn(
            "nix profile install github:example/project/v1.1.0",
            Path("README.md").read_text(),
        )
        self.assertEqual(commands[0], ["nix", "flake", "update", "nixpkgs"])
        self.assertEqual(commands[-2][0:3], ["nix", "flake", "check"])
        self.assertEqual(commands[-1][0:2], ["nix", "build"])

    def test_cerberus_only_does_not_update_nixpkgs(self):
        self.caddy = "v2.9.0"
        commands = self.execute()
        self.assertEqual(Path("outputs").read_text(), "tag=v1.0.2\nchanged=true\n")
        self.assertNotIn(["nix", "flake", "update", "nixpkgs"], commands)
        self.assertIn("a" * 40, Path("flake.nix").read_text())
        readme = Path("README.md").read_text()
        self.assertIn(f'| nixpkgs | `{"a" * 40}` |', readme)
        self.assertIn("nix profile install github:example/project/v1.0.2", readme)

    def test_no_update_or_upstream_rollback_does_not_release(self):
        self.caddy, self.cerberus = "v2.8.0", "v0.1.0"
        self.assertEqual(self.execute(), [])
        self.assertEqual(Path("outputs").read_text(), "tag=\nchanged=false\n")
        self.assertEqual(Path("flake.nix").read_text(), self.flake)

    def test_unpublished_tag_is_retried_without_another_bump(self):
        self.published = []
        self.assertEqual(self.execute(), [])
        self.assertEqual(Path("outputs").read_text(), "tag=v1.0.1\nchanged=false\n")

    def test_nixpkgs_lag_is_an_explicit_failure_without_edits(self):
        self.packaged_caddy = "2.9.0"
        with self.assertRaisesRegex(ValueError, "retry after nixpkgs"):
            self.execute()
        self.assertEqual(Path("flake.nix").read_text(), self.flake)
        self.assertFalse(Path("outputs").exists())

    def test_colliding_tag_is_not_overwritten(self):
        self.tags += "\nv1.1.0"
        with self.assertRaisesRegex(ValueError, "already exists"):
            self.execute()
        self.assertFalse(Path("outputs").exists())

    def test_failed_checks_do_not_emit_release_outputs(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.execute(build_failure=True)
        self.assertFalse(Path("outputs").exists())

    def test_unrelated_nix_failure_is_not_treated_as_a_hash(self):
        with self.assertRaisesRegex(ValueError, "plugin source hash mismatch"):
            self.execute(hash_log="error: failed to download source")
        self.assertFalse(Path("outputs").exists())

    def test_api_failure_cannot_be_mistaken_for_no_changes(self):
        with (
            patch.object(updater, "run", side_effect=self.command),
            patch.object(
                updater, "api_items", side_effect=subprocess.CalledProcessError(1, ["gh"])
            ),
            self.assertRaises(subprocess.CalledProcessError),
        ):
            updater.prepare()
        self.assertFalse(Path("outputs").exists())
        self.assertEqual(Path("flake.nix").read_text(), self.flake)


if __name__ == "__main__":
    unittest.main()
