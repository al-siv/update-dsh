#!/usr/bin/env python3
"""Counterexamples using controlled subprocess faults and real filesystem state.

Run: python3 -B tests/test_update.py
Set DSH_UPDATE_SCRIPT to test an isolated copy (used for mutation checks).
This fixture does not model npm configuration, engine checks, or installability.
Those observations require test_npm.py or the published-package probe.
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
NODE = shutil.which("node")
PYTHON = shutil.which("python3")

# A deliberately small fault injector, not a second implementation of npm.
# It supplies versions, writes a launcher, and can pause or fail after a write.
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
    tag = next(arg for arg in args if arg.startswith("@deepseek-ai/dsh@")).rsplit("@", 1)[1]
    version = os.environ.get(f"TEST_{tag.upper()}", {"latest": "1.2.0", "next": "1.3.0-rc.1"}[tag])
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
    if os.environ.get("TEST_FAIL_AFTER_WRITE"):
        print("injected failure after writing files", file=sys.stderr)
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
            'console.log(require("./package.json").version);\n'
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

    def run_update(self, *args, **environment):
        return run_command(
            ["/bin/bash", str(SCRIPT), *args], env=self.env | environment,
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

    def test_comparison_process_failure_is_not_treated_as_a_version_order(self):
        """A failed version-comparison process is distinguishable from 'not newer'."""
        node = self.bin / "node"
        node.unlink()  # Replace the fixture symlink, never the real Node executable.
        node.write_text(
            '#!/usr/bin/env python3\n'
            'import os, sys\n'
            'args = sys.argv[1:]\n'
            'if args[-2:] == ["1.2.0", "1.1.0"]:\n'
            '    print("comparison process failed", file=sys.stderr)\n'
            '    sys.exit(42)\n'
            f'os.execv({NODE!r}, [{NODE!r}, *args])\n'
        )
        node.chmod(0o755)
        result = self.run_update("--channel", "next", TEST_NEXT="1.2.0")
        self.assertEqual(result.returncode, 42, result.stdout + result.stderr)
        self.assertIn("comparison process failed", result.stderr)
        self.assertEqual(self.version(), "1.1.0")
        self.assertEqual(self.calls("install"), [])

    def test_failed_process_can_leave_a_durable_version_for_retry(self):
        """Exit failure and persisted version can disagree; retry observes the disk."""
        result = self.run_update(TEST_FAIL_AFTER_WRITE="1")
        self.assertEqual(result.returncode, 29)
        self.assertEqual(self.version(), "1.2.0")
        retry = self.run_update()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.version(), "1.2.0")
        self.assertEqual(len(self.calls("install")), 1)

    def test_cli_diagnostics_survive_failure_after_files_changed(self):
        """A failed child writes its diagnostic to stdout after npm changed files."""
        result = self.run_update(TEST_CLI_FAIL="1", TEST_CLI_FAILURE_OUTPUT="Cannot load runtime")
        self.assertEqual(result.returncode, 27)
        self.assertIn("Cannot load runtime", result.stderr)
        self.assertNotIn("Cannot load runtime", result.stdout)
        self.assertEqual(self.version(), "1.2.0")

    def test_path_mismatch_prevents_lookup_and_installation(self):
        """Two actual launchers coexist; the foreign one must not cause a write."""
        other = self.root / "other-bin"
        other.mkdir()
        foreign = other / "dsh"
        foreign.write_text("#!/bin/sh\nexit 0\n")
        foreign.chmod(0o755)
        result = self.run_update(PATH=f"{other}:{self.env['PATH']}")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(foreign.read_text(), "#!/bin/sh\nexit 0\n")
        self.assertEqual(self.version(), "1.1.0")
        self.assertEqual(self.calls("view"), [])
        self.assertEqual(self.calls("install"), [])

    def test_concurrent_first_installations_use_the_same_lock(self):
        """Two processes race while the global root contains no package manifest."""
        self.remove_installation()
        first = self.start_update("--install", TEST_INSTALL_WAIT="1")
        self.wait_for_install(first)
        second = self.run_update("--install")
        self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.calls("view")), 1)
        self.assertEqual(len(self.calls("install")), 1)
        (self.root / "install-release").touch()
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, stdout + stderr)
        self.assertEqual(self.version(), "1.2.0")

    def test_next_installs_only_a_version_of_higher_semver_precedence(self):
        """Discriminating vectors derived from SemVer 2.0.0 clauses 10 and 11."""
        # Oracle: https://semver.org/spec/v2.0.0.html, independent of update.sh.
        # '-foo' distinguishes SemVer identifier classes from plain ASCII order.
        cases = (
            ("1.2.0-rc.9", "1.2.0-rc.10", True), ("1.2.0-rc.10", "1.2.0-rc.9", False),
            ("1.9.0", "1.10.0", True), ("1.10.0", "1.9.0", False),
            ("1.2.0-rc.2", "1.2.0", True), ("1.2.0", "1.2.0-rc.3", False),
            ("1.2.0-alpha.9", "1.2.0-alpha.-foo", True), ("1.2.0-beta", "1.2.0-alpha.9", False),
            ("1.2.0-alpha", "1.2.0-alpha.1", True), ("1.2.0-alpha.1", "1.2.0-alpha", False),
            ("1.2.0+build.1", "1.2.0+build.2", False),
        )
        for installed, tagged, newer in cases:
            with self.subTest(installed=installed, tagged=tagged):
                self.manifest.write_text(json.dumps({"version": installed}))
                result = self.run_update("--channel", "next", TEST_NEXT=tagged)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.version(), tagged if newer else installed)

    def test_concurrent_next_updates_reject_overlap_and_retry_reads_current_version(self):
        """A paused older update overlaps a newer tag; retry cannot roll it back."""
        first = self.start_update("--channel", "next", TEST_NEXT="1.2.0", TEST_INSTALL_WAIT="1")
        self.wait_for_install(first)
        second = self.run_update("--channel", "next", TEST_NEXT="1.3.0")
        self.assertNotEqual(second.returncode, 0, second.stdout + second.stderr)
        self.assertEqual(len(self.calls("view")), 1)
        self.assertEqual(len(self.calls("install")), 1)
        (self.root / "install-release").touch()
        stdout, stderr = first.communicate(timeout=10)
        self.assertEqual(first.returncode, 0, stdout + stderr)
        self.assertEqual(self.version(), "1.2.0")

        retry = self.run_update("--channel", "next", TEST_NEXT="1.3.0")
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.version(), "1.3.0")
        older = self.run_update("--channel", "next", TEST_NEXT="1.2.0")
        self.assertEqual(older.returncode, 0, older.stderr)
        self.assertEqual(self.version(), "1.3.0")
        self.assertEqual(len(self.calls("install")), 2)

    def test_terminated_update_releases_lock_for_retry(self):
        """Real SIGTERM during a waiting subprocess exercises Bash trap delivery."""
        process = self.start_update(TEST_INSTALL_WAIT="1")
        self.wait_for_install(process)
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=10)
        self.assertNotEqual(process.returncode, 0)
        self.assertEqual(self.version(), "1.1.0")
        retry = self.run_update()
        self.assertEqual(retry.returncode, 0, retry.stderr)
        self.assertEqual(self.version(), "1.2.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
