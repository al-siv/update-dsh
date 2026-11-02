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
import signal
import subprocess
import tempfile
import time
import unittest

from process_helpers import run_command, stop_process_group


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
import shutil
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
    if os.environ.get("TEST_INSTALL_WAIT"):
        import time
        (root / "install-waiting").touch()
        deadline = time.monotonic() + 15
        while not (root / "install-release").exists():
            if time.monotonic() >= deadline:
                sys.exit("timed out waiting for installation release")
            time.sleep(0.01)
    if os.environ.get("TEST_INSTALL_FAIL"):
        print("installation failed", file=sys.stderr)
        sys.exit(25)
    if os.environ.get("TEST_INCOMPATIBLE_NODE"):
        force = os.environ.get("npm_config_force") == "true" and "--force=false" not in args
        if "--engine-strict" in args and not force:
            print("EBADENGINE", file=sys.stderr)
            sys.exit(26)
    if not os.environ.get("TEST_SKIP_WRITE"):
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({"version": args[-1].split("@")[-1]}))
        launcher = manifest.parent / "bin.js"
        if not launcher.exists():
            shutil.copyfile(root / "cli-fixture.js", launcher)
            launcher.chmod(0o755)
        (prefix / "bin").mkdir(parents=True, exist_ok=True)
        link = prefix / "bin/dsh"
        if not link.exists() and not link.is_symlink():
            link.symlink_to(launcher)
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
            'if (process.env.TEST_CLI_FAIL) {\n'
            '  if (process.env.TEST_CLI_FAILURE_OUTPUT) console.log(process.env.TEST_CLI_FAILURE_OUTPUT);\n'
            '  process.exit(27);\n'
            '}\n'
            'console.log(process.env.TEST_CLI_VERSION || require("./package.json").version);\n'
        )
        launcher.chmod(0o755)
        shutil.copyfile(launcher, self.root / "cli-fixture.js")
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
        return run_command(
            ["/bin/bash", str(script), *args], env=self.env | environment,
            cwd=self.root, timeout=20,
        )

    def start_update(self, *args, **environment):
        process = subprocess.Popen(
            ["/bin/bash", str(SCRIPT), *args], env=self.env | environment,
            cwd=self.root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
        self.addCleanup(stop_process_group, process)
        return process

    def wait_for_install(self, process):
        deadline = time.monotonic() + 10
        while not (self.root / "install-waiting").exists():
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                self.fail(f"Updater exited before installation: {stdout}\n{stderr}")
            self.assertLess(time.monotonic(), deadline, "Updater did not reach installation")
            time.sleep(0.01)

    def calls(self, command):
        log = self.root / "calls.jsonl"
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return [args for args in calls if args[0] == command]

    def version(self):
        return json.loads(self.manifest.read_text())["version"]

    def remove_installation(self):
        (self.bin / "dsh").unlink()
        shutil.rmtree(self.prefix / "lib")

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
        for value in ("", "null", '"1.2.0"', "1.2.0\n1.3.0", "01.2.0",
                      "1.2.0-01", "1.2.0-alpha..1", "1.2.0+build..1"):
            with self.subTest(value=value):
                result = self.run_update(TEST_LATEST=value)
                self.assert_failed(result, "invalid version")
        self.assertEqual(self.calls("install"), [])

    def test_invalid_installed_version_is_an_error_instead_of_a_successful_skip(self):
        for channel in ("latest", "next"):
            for value in ("broken", "1.1", "1.1.0.0", "01.1.0", "1.1.0-01", None):
                with self.subTest(channel=channel, installed=value):
                    self.manifest.write_text(json.dumps({"version": value}))
                    result = self.run_update("--channel", channel)
                    self.assert_failed(result, "Installed package has an invalid version")
                    self.assertNotIn("nothing to install", result.stdout)
        self.assertEqual(self.calls("install"), [])

    def test_comparison_process_failure_is_not_treated_as_a_version_order(self):
        node = self.bin / "node"
        node.unlink()  # Replace the fixture symlink, never the real Node executable.
        node.write_text(
            '#!/usr/bin/env python3\n'
            'import os, sys\n'
            'args = sys.argv[1:]\n'
            'if len(args) == 4 and args[:2] == ["--input-type=commonjs", "-"] and args[2] == "1.2.0":\n'
            '    print("comparison process failed", file=sys.stderr)\n'
            '    sys.exit(42)\n'
            f'os.execv({NODE!r}, [{NODE!r}, *args])\n'
        )
        node.chmod(0o755)
        for channel in ("latest", "next"):
            with self.subTest(channel=channel):
                self.manifest.write_text(json.dumps({"version": "1.1.0"}))
                result = self.run_update("--channel", channel, TEST_NEXT="1.2.0")
                self.assert_failed(result, "comparison process failed")
                self.assertEqual(result.returncode, 42)
                self.assertNotIn("nothing to install", result.stdout)
        self.assertEqual(self.calls("install"), [])

    def test_check_works_without_write_access_to_global_installation(self):
        for directory in (self.prefix, self.package.parent.parent):
            directory.chmod(0o555)
            self.addCleanup(directory.chmod, 0o755)
        result = self.run_update("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), "1.1.0")
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
        self.assert_failed(result, "dsh --version failed (exit 27)")
        self.assertEqual(result.returncode, 27)
        # The install has taken effect; no rollback is promised by the script.
        self.assertEqual(self.version(), "1.2.0")

    def test_cli_failure_when_current_reports_error_and_preserves_status(self):
        result = self.run_update(TEST_LATEST="1.1.0", TEST_CLI_FAIL="1")
        self.assert_failed(result, "dsh --version failed (exit 27)")
        self.assertEqual(result.returncode, 27)
        self.assertNotIn("Already on the latest", result.stdout)
        self.assertEqual(self.calls("install"), [])

    def test_cli_failure_forwards_captured_stdout_to_stderr(self):
        result = self.run_update(TEST_CLI_FAIL="1", TEST_CLI_FAILURE_OUTPUT="Cannot load runtime")
        self.assert_failed(result, "dsh --version failed (exit 27)")
        self.assertEqual(result.returncode, 27)
        self.assertIn("Cannot load runtime", result.stderr)
        self.assertNotIn("Cannot load runtime", result.stdout)

    def test_missing_launcher_reports_incomplete_installation_instead_of_path(self):
        (self.bin / "dsh").unlink()
        result = self.run_update("--install")
        self.assert_failed(result, "Global dsh installation is incomplete")
        self.assertIn("Repair @deepseek-ai/dsh with npm", result.stderr)
        self.assertNotIn("Add", result.stderr)
        self.assertEqual(self.calls("view"), [])
        self.assertEqual(self.calls("install"), [])

    def test_non_executable_launcher_reports_incomplete_installation(self):
        (self.package / "bin.js").chmod(0o644)
        result = self.run_update()
        self.assert_failed(result, "Global dsh installation is incomplete")
        self.assertNotIn("Add", result.stderr)
        self.assertEqual(self.calls("view"), [])
        self.assertEqual(self.calls("install"), [])

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
        self.assertIn("--install", result.stderr)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.calls("view"), [])
        self.assertEqual(self.calls("install"), [])

    def test_install_creates_missing_global_package_and_is_idempotent(self):
        self.remove_installation()
        result = self.run_update("--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Installed: not installed", result.stdout)
        self.assertIn("Done: dsh 1.2.0", result.stdout)
        self.assertEqual(self.version(), "1.2.0")
        retry = self.run_update("--install")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("Already on the latest", retry.stdout)
        self.assertEqual(len(self.calls("install")), 1)

    def test_install_flag_updates_an_existing_installation(self):
        result = self.run_update("--install")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Installed: 1.1.0", result.stdout)
        self.assertEqual(self.version(), "1.2.0")

    def test_install_check_does_not_create_missing_global_root(self):
        self.remove_installation()
        result = self.run_update("--install", "--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Installed: not installed", result.stdout)
        self.assertIn("Available: 1.2.0", result.stdout)
        self.assertFalse((self.prefix / "lib").exists())
        self.assertEqual(self.calls("install"), [])

    def test_next_wrapper_can_install_without_an_installed_version(self):
        self.remove_installation()
        download = self.root / "download"
        download.mkdir()
        shutil.copyfile(SCRIPT, download / "update.sh")
        shutil.copyfile(NEXT_SCRIPT, download / "next.sh")
        result = self.run_update("--install", script=download / "next.sh", TEST_NEXT="0.0.0-0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.version(), "0.0.0-0")
        self.assertIn("@deepseek-ai/dsh@next", self.calls("view")[0])

    def test_install_refuses_to_shadow_a_foreign_dsh(self):
        self.remove_installation()
        foreign = self.bin / "dsh"
        content = "#!/bin/sh\nexit 0\n"
        foreign.write_text(content)
        foreign.chmod(0o755)
        result = self.run_update("--install")
        self.assert_failed(result, "dsh in PATH")
        self.assertEqual(foreign.read_text(), content)
        self.assertEqual(self.calls("view"), [])
        self.assertEqual(self.calls("install"), [])

    def test_install_reports_npm_failure_and_preserves_its_exit_code(self):
        self.remove_installation()
        result = self.run_update("--install", TEST_INSTALL_FAIL="1")
        self.assert_failed(result, "npm installation failed (exit 25)")
        self.assertEqual(result.returncode, 25)
        self.assertFalse(self.manifest.exists())
        retry = self.run_update("--install")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.version(), "1.2.0")

    def test_install_does_not_claim_success_if_npm_created_no_launcher(self):
        self.remove_installation()
        result = self.run_update("--install", TEST_SKIP_WRITE="1")
        self.assert_failed(result, "npm did not create an executable dsh launcher")
        self.assertEqual(result.returncode, 1)

    def test_install_reports_missing_path_even_if_package_was_written(self):
        self.remove_installation()
        tools_bin = self.root / "tools-bin"
        tools_bin.mkdir()
        for name in ("npm", "node", "python3"):
            (tools_bin / name).symlink_to(self.bin / name)
        result = self.run_update("--install", PATH=f"{tools_bin}:/usr/bin:/bin")
        self.assert_failed(result, f"Add {self.bin} to PATH")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(self.version(), "1.2.0")

    def test_install_validates_registry_version_before_installing(self):
        self.remove_installation()
        result = self.run_update("--install", TEST_LATEST="1.2.0-alpha..1")
        self.assert_failed(result, "npm returned an invalid version")
        self.assertEqual(self.calls("install"), [])

    def test_concurrent_first_installations_use_the_same_lock(self):
        self.remove_installation()
        first = self.start_update("--install", TEST_INSTALL_WAIT="1")
        self.wait_for_install(first)
        second = self.run_update("--install")
        self.assert_failed(second, "Could not acquire update lock")
        self.assertEqual(len(self.calls("view")), 1)
        self.assertEqual(len(self.calls("install")), 1)
        (self.root / "install-release").touch()
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, stdout + stderr)
        self.assertEqual(self.version(), "1.2.0")

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
            ("v1.1.0", "1.2.0", True), ("v1.2.0", "1.2.0", False),
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

    def test_concurrent_next_updates_reject_overlap_and_retry_reads_current_version(self):
        first = self.start_update("--channel", "next", TEST_NEXT="1.2.0", TEST_INSTALL_WAIT="1")
        self.wait_for_install(first)
        second = self.run_update("--channel", "next", TEST_NEXT="1.3.0")
        self.assert_failed(second, "Could not acquire update lock")
        self.assertEqual(len(self.calls("view")), 1)
        self.assertEqual(len(self.calls("install")), 1)
        (self.root / "install-release").touch()
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, stdout + stderr)
        self.assertEqual(self.version(), "1.2.0")

        retry = self.run_update("--channel", "next", TEST_NEXT="1.3.0")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertIn("Installed: 1.2.0", retry.stdout)
        self.assertEqual(self.version(), "1.3.0")
        older = self.run_update("--channel", "next", TEST_NEXT="1.2.0")
        self.assertEqual(older.returncode, 0, older.stderr)
        self.assertIn("not newer", older.stdout)
        self.assertEqual(self.version(), "1.3.0")
        self.assertEqual(len(self.calls("install")), 2)

    def test_terminated_update_releases_lock_for_retry(self):
        process = self.start_update(TEST_INSTALL_WAIT="1")
        self.wait_for_install(process)
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.version(), "1.1.0")
        retry = self.run_update()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.version(), "1.2.0")

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
