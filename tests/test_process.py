#!/usr/bin/env python3
"""Check test subprocess capture and cleanup of entire process groups."""

import json
import os
import shutil
import signal
import subprocess
import sys
import unittest

from process_helpers import run_command


PS = shutil.which("ps")


class ProcessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not PS:
            raise RuntimeError("INCONCLUSIVE: process-state observations require ps")

    @staticmethod
    def kill_group(pid):
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def assert_timeout_stops_group(self, parent_exits):
        child = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)"
        parent = (
            "import json, os, subprocess, sys, time\n"
            f"child = subprocess.Popen([sys.executable, '-c', {child!r}])\n"
            "print(json.dumps({'parent': os.getpid(), 'child': child.pid}), flush=True)\n"
            + ("sys.exit(0)\n" if parent_exits else "time.sleep(60)\n")
        )
        with self.assertRaises(subprocess.TimeoutExpired) as caught:
            run_command([sys.executable, "-c", parent], timeout=1)
        pids = json.loads(caught.exception.stdout)
        self.addCleanup(self.kill_group, pids["parent"])
        for name, pid in pids.items():
            with self.subTest(process=name):
                state = run_command([PS, "-o", "stat=", "-p", str(pid)], timeout=5)
                self.assertIn(state.returncode, (0, 1), state.stderr)
                # A zombie has terminated and cannot write into the test prefix.
                self.assertTrue(
                    not state.stdout.strip() or state.stdout.strip().startswith("Z"),
                    f"{name} is still running after timeout: {state.stdout}",
                )

    def test_timeout_stops_parent_and_descendant(self):
        """A live parent and its TERM-ignoring child both stop at the deadline."""
        self.assert_timeout_stops_group(parent_exits=False)

    def test_timeout_stops_descendant_even_if_parent_has_exited(self):
        """An exited leader still has a live child holding captured pipes open."""
        self.assert_timeout_stops_group(parent_exits=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
