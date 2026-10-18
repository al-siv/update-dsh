#!/usr/bin/env python3
"""Behavior tests for update.sh and next.sh; npm is a controlled subprocess fixture here.

Run: python3 tests/test_update.py
Set DSH_UPDATE_SCRIPT to test an isolated copy (used for mutation checks).
The real npm installation test lives in test_integration.py.
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(os.environ.get("DSH_UPDATE_SCRIPT", Path(__file__).resolve().parent.parent / "update.sh")).resolve()
NEXT_SCRIPT = Path(__file__).resolve().parent.parent / "next.sh"
NODE = shutil.which("node")
PYTHON = shutil.which("python3")

# This double models the subprocess boundary, not npm's installation internals.
# Every call is recorded so tests can detect an unwanted installation attempt.
NPM_FIXTURE = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
root = Path(os.environ["TEST_ROOT"])
with (root / "calls.jsonl").open("a") as log:
    log.write(json.dumps(args) + "\n")
prefix = root / "global prefix"
manifest = prefix / "lib/node_modules/@deepseek-ai/dsh/package.json"
if args[0] == "prefix":
    print(prefix)
elif args[0] == "root":
    print(prefix / "lib/node_modules")
elif args[0] == "view":
    if os.environ.get("TEST_VIEW_FAIL"):
        print("registry unavailable", file=sys.stderr)
        sys.exit(23)
    if os.environ.get("TEST_PROJECT_CONFIG") and "--global" not in args:
        print("project registry must not be used", file=sys.stderr)
        sys.exit(24)
    tag = next(arg for arg in args if arg.startswith("@deepseek-ai/dsh@")).rsplit("@", 1)[1]
    version = os.environ.get(f"TEST_{tag.upper()}", {"latest": "1.2.0", "next": "1.3.0-rc.1"}[tag])
    if os.environ.get("npm_config_json") == "true" and "--json=false" not in args:
        print(json.dumps(version))
    else:
        print(version)
elif args[0] == "install":
    if os.environ.get("TEST_INSTALL_FAIL"):
        print("installation failed", file=sys.stderr)
        sys.exit(25)
    if os.environ.get("TEST_INCOMPATIBLE_NODE"):
        force = os.environ.get("npm_config_force") == "true" and "--force=false" not in args
        if "--engine-strict" in args and not force:
            print("EBADENGINE", file=sys.stderr)
            sys.exit(26)
    if not os.environ.get("TEST_SKIP_WRITE"):
        manifest.write_text(json.dumps({"version": args[-1].split("@")[-1]}))
    if os.environ.get("TEST_PARTIAL_INSTALL_FAIL"):
        (manifest.parent / "bin.js").unlink()
        print("partial installation failed", file=sys.stderr)
        sys.exit(28)
    if os.environ.get("TEST_FINALIZE_FAIL"):
        print("installation finalization failed", file=sys.stderr)
        sys.exit(29)
else:
    sys.exit("unexpected npm command")
'''


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(NODE and PYTHON, "node and python3 are required")
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-update-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.prefix = self.root / "global prefix"
        self.bin = self.prefix / "bin"
        self.bin.mkdir(parents=True)
        self.package = self.prefix / "lib/node_modules/@deepseek-ai/dsh"
        self.package.mkdir(parents=True)
        self.manifest = self.package / "package.json"
        self.manifest.write_text(json.dumps({"version": "1.1.0"}))
        launcher = self.package / "bin.js"
        launcher.write_text(
            '#!/usr/bin/env node\n'
            'if (process.env.TEST_CLI_FAIL) process.exit(27);\n'
            'console.log(process.env.TEST_CLI_VERSION || require("./package.json").version);\n'
        )
        launcher.chmod(0o755)
        (self.bin / "dsh").symlink_to(launcher)
        npm = self.bin / "npm"
        npm.write_text(NPM_FIXTURE)
        npm.chmod(0o755)
        (self.bin / "node").symlink_to(NODE)
        (self.bin / "python3").symlink_to(PYTHON)
        self.env = {key: value for key, value in os.environ.items()
                    if not key.lower().startswith(("npm_config_", "test_"))}
        # Never fall through to the user's npm or dsh.
        self.env.update(PATH=f"{self.bin}:/usr/bin:/bin", TEST_ROOT=str(self.root))

    def run_update(self, *args, script=SCRIPT, **environment):
        return subprocess.run(
            ["/bin/bash", str(script), *args], env=self.env | environment,
            cwd=self.root, text=True, capture_output=True, timeout=20,
        )

    def calls(self, command):
        log = self.root / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [args for args in calls if args[0] == command]

    def version(self):
        return json.loads(self.manifest.read_text())["version"]

    def assert_failed(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(message, result.stderr)
        self.assertNotIn("Done:", result.stdout)

    def test_update_installs_resolved_version_and_reports_verified_result(self):
        result = self.run_update()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), "1.2.0")
        self.assertIn("Done: dsh 1.2.0", result.stdout)
        calls = self.calls("install")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][-1], "@deepseek-ai/dsh@1.2.0")
        self.assertIn("--global", calls[0])
        self.assertEqual(calls[0][calls[0].index("--prefix") + 1], str(self.prefix))

    def test_check_does_not_install_when_update_available(self):
        result = self.run_update("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("1.2.0", result.stdout)
        self.assertEqual(self.version(), "1.1.0")
        self.assertEqual(self.calls("install"), [])

    def test_current_version_is_verified_without_reinstall(self):
        result = self.run_update(TEST_LATEST="1.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Already on the latest", result.stdout)
        self.assertEqual(self.calls("install"), [])

    def test_json_preference_does_not_break_lookup(self):
        result = self.run_update("--check", npm_config_json="true")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Available: 1.2.0", result.stdout)

    def test_lookup_ignores_unrelated_project_config(self):
        result = self.run_update("--check", TEST_PROJECT_CONFIG="1")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_force_cannot_bypass_incompatible_node_rejection(self):
        result = self.run_update(npm_config_force="true", TEST_INCOMPATIBLE_NODE="1")
        self.assert_failed(result, "EBADENGINE")
        self.assertEqual(self.version(), "1.1.0")

    def test_registry_error_prevents_installation(self):
        result = self.run_update(TEST_VIEW_FAIL="1")
        self.assert_failed(result, "registry unavailable")
        self.assertEqual(self.calls("install"), [])

    def test_invalid_version_prevents_installation(self):
        for value in ("", "null", '"1.2.0"', "1.2.0\n1.3.0"):
            with self.subTest(value=value):
                result = self.run_update(TEST_LATEST=value)
                self.assert_failed(result, "invalid version")
        self.assertEqual(self.calls("install"), [])

    def test_install_error_keeps_failure_status_and_allows_retry(self):
        result = self.run_update(TEST_INSTALL_FAIL="1")
        self.assert_failed(result, "installation failed")
        self.assertEqual(result.returncode, 25)
        self.assertEqual(self.version(), "1.1.0")
        retry = self.run_update()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.version(), "1.2.0")

    def test_install_without_effect_does_not_report_success(self):
        result = self.run_update(TEST_SKIP_WRITE="1")
        self.assert_failed(result, "After installation, dsh --version")
        self.assertEqual(self.version(), "1.1.0")

    def test_npm_failure_after_commit_is_reported_and_retry_checks_durable_version(self):
        result = self.run_update(TEST_FINALIZE_FAIL="1")
        self.assert_failed(result, "installation finalization failed")
        self.assertEqual(result.returncode, 29)
        self.assertEqual(self.version(), "1.2.0")
        retry = self.run_update()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("Already on the latest", retry.stdout)
        self.assertEqual(len(self.calls("install")), 1)

    def test_partial_npm_failure_and_retry_do_not_claim_usable_installation(self):
        result = self.run_update(TEST_PARTIAL_INSTALL_FAIL="1")
        self.assert_failed(result, "partial installation failed")
        self.assertEqual(result.returncode, 28)
        self.assertEqual(self.version(), "1.2.0")
        self.assertFalse((self.bin / "dsh").exists())
        retry = self.run_update()
        self.assert_failed(retry, "Error:")
        self.assertNotIn("Already on the latest", retry.stdout)
        self.assertEqual(len(self.calls("install")), 1)

    def test_cli_failure_after_install_is_not_success(self):
        result = self.run_update(TEST_CLI_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("Done:", result.stdout)
        # The install has taken effect; no rollback is promised by the script.
        self.assertEqual(self.version(), "1.2.0")

    def test_wrong_cli_version_when_current_is_not_success(self):
        result = self.run_update(TEST_LATEST="1.1.0", TEST_CLI_VERSION="0.0.1")
        self.assert_failed(result, "dsh --version returned")
        self.assertNotIn("Already on the latest", result.stdout)

    def test_path_mismatch_prevents_lookup_and_installation(self):
        other = self.root / "other-bin"
        other.mkdir()
        foreign = other / "dsh"
        foreign.write_text("#!/bin/sh\nexit 0\n")
        foreign.chmod(0o755)
        result = self.run_update(PATH=f"{other}:{self.env['PATH']}")
        self.assert_failed(result, "differs")
        self.assertEqual(self.calls("view"), [])
        self.assertEqual(self.calls("install"), [])

    def test_missing_installation_does_not_install(self):
        self.manifest.unlink()
        result = self.run_update()
        self.assert_failed(result, "was not found")
        self.assertEqual(self.calls("install"), [])

    def test_channel_selects_npm_tag(self):
        for channel, version in (("latest", "1.2.0"), ("next", "1.3.0-rc.1")):
            with self.subTest(channel=channel):
                result = self.run_update("--check", "--channel", channel)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"Checking npm {channel}...\nAvailable: {version}\n", result.stdout)
                self.assertIn(f"@deepseek-ai/dsh@{channel}", self.calls("view")[-1])
        self.assertEqual(self.calls("install"), [])

    def test_next_installs_only_a_version_of_higher_semver_precedence(self):
        # A release outranks its prereleases; numeric identifiers compare as
        # numbers and below alphanumeric ones; more identifiers rank higher;
        # build metadata does not count.
        cases = (
            ("1.2.0-rc.9", "1.2.0-rc.10", True), ("1.2.0-rc.10", "1.2.0-rc.9", False),
            ("1.9.0", "1.10.0", True), ("1.10.0", "1.9.0", False),
            ("1.2.0-rc.2", "1.2.0", True), ("1.2.0", "1.2.0-rc.3", False),
            ("1.2.0-alpha.9", "1.2.0-alpha.beta", True), ("1.2.0-beta", "1.2.0-alpha.9", False),
            ("1.2.0-alpha", "1.2.0-alpha.1", True), ("1.2.0-alpha.1", "1.2.0-alpha", False),
            ("1.2.0+build.2", "1.2.0+build.1", False), ("1.2.0+build.2", "1.2.1+build.1", True),
        )
        for installed, tagged, newer in cases:
            with self.subTest(installed=installed, tagged=tagged):
                self.manifest.write_text(json.dumps({"version": installed}))
                result = self.run_update("--channel", "next", TEST_NEXT=tagged)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.version(), tagged if newer else installed)
                self.assertEqual("Done:" in result.stdout, newer, result.stdout)

    def test_next_keeps_installation_only_after_verifying_cli(self):
        for tagged, message in (("1.1.0", "Already on the next channel version."),
                                ("1.1.0-rc.1", "not newer than the installed one")):
            with self.subTest(tagged=tagged):
                result = self.run_update("--channel", "next", TEST_NEXT=tagged)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(message, result.stdout)
                failed = self.run_update("--channel", "next", TEST_NEXT=tagged, TEST_CLI_VERSION="0.0.1")
                self.assert_failed(failed, "dsh --version returned")
                self.assertNotIn(message, failed.stdout)
        self.assertEqual(self.calls("install"), [])

    def test_next_script_runs_next_channel_beside_update_script(self):
        # As downloaded: both files in one directory, without the executable bit.
        download = self.root / "download"
        download.mkdir()
        shutil.copyfile(SCRIPT, download / "update.sh")
        shutil.copyfile(NEXT_SCRIPT, download / "next.sh")
        result = self.run_update("--check", script=download / "next.sh")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Available: 1.3.0-rc.1", result.stdout)
        self.assertIn("@deepseek-ai/dsh@next", self.calls("view")[0])
        self.assertEqual(self.calls("install"), [])

    def test_help_and_invalid_arguments_do_not_call_npm(self):
        result = self.run_update("--help")
        self.assertEqual(result.returncode, 0)
        self.assertIn("--check", result.stdout)
        self.assertIn("--channel", result.stdout)
        for args in (("--unknown",), ("--check", "extra"), ("--channel",), ("--channel", "beta")):
            with self.subTest(args=args):
                result = self.run_update(*args)
                self.assert_failed(result, "Error:")
        self.assertEqual(self.calls("prefix"), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
