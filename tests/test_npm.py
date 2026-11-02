#!/usr/bin/env python3
"""Real npm integration with tiny packages served by a temporary local registry.

Run: python3 tests/test_npm.py
No external registry, user npm configuration, or global installation is used.
"""

import base64
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import threading
import unittest
from urllib.parse import unquote, urlsplit

from process_helpers import run_command


SCRIPT = Path(os.environ.get("DSH_UPDATE_SCRIPT", Path(__file__).resolve().parent.parent / "update.sh")).resolve()
NEXT_SCRIPT = Path(__file__).resolve().parent.parent / "next.sh"
NPM = shutil.which("npm")
NODE = shutil.which("node")
PACKAGE = "@deepseek-ai/dsh"


def archive(manifest):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in {
            "package.json": json.dumps(manifest),
            "bin.js": '#!/usr/bin/env node\nconsole.log(require("./package.json").version);\n',
        }.items():
            data = content.encode()
            entry = tarfile.TarInfo(f"package/{name}")
            entry.size = len(data)
            entry.mode = 0o755 if name == "bin.js" else 0o644
            tar.addfile(entry, io.BytesIO(data))
    return buffer.getvalue()


class NpmTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not NPM or not NODE:
            raise RuntimeError("INCONCLUSIVE: real npm observations require npm and node")

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-local-npm-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        scripts = self.root / "scripts"
        scripts.mkdir()
        shutil.copyfile(SCRIPT, scripts / "update.sh")
        self.next_script = scripts / "next.sh"
        shutil.copyfile(NEXT_SCRIPT, self.next_script)
        self.prefix = self.root / "global prefix"
        self.manifest = self.prefix / f"lib/node_modules/{PACKAGE}/package.json"
        self.tags = {"latest": "1.2.0", "next": "1.2.0"}
        self.versions = {}
        self.tarballs = {}
        self.requests = []
        owner = self

        class Registry(BaseHTTPRequestHandler):
            def do_GET(self):
                path = unquote(urlsplit(self.path).path)
                owner.requests.append(path)
                if path == f"/{PACKAGE}":
                    data = json.dumps({
                        "name": PACKAGE, "dist-tags": owner.tags, "versions": owner.versions,
                    }).encode()
                    kind = "application/json"
                elif path in owner.tarballs:
                    data = owner.tarballs[path]
                    kind = "application/octet-stream"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Registry)
        self.registry = f"http://127.0.0.1:{self.server.server_port}"
        for version in ("1.1.0", "1.2.0", "2.0.0"):
            manifest = {"name": PACKAGE, "version": version, "bin": {"dsh": "bin.js"}}
            if version == "2.0.0":
                manifest["engines"] = {"node": ">=999"}
            data = archive(manifest)
            path = f"/dsh-{version}.tgz"
            self.tarballs[path] = data
            self.versions[version] = manifest | {"dist": {
                "tarball": self.registry + path,
                "integrity": "sha512-" + base64.b64encode(hashlib.sha512(data).digest()).decode(),
            }}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_registry)

        self.tools_bin = self.root / "tools-bin"
        self.tools_bin.mkdir()
        (self.tools_bin / "node").symlink_to(NODE)
        (self.tools_bin / "npm").symlink_to(NPM)
        userconfig = self.root / "user.npmrc"
        userconfig.write_text(f"registry={self.registry}/\n")
        globalconfig = self.root / "global.npmrc"
        globalconfig.touch()
        self.project = self.root / "unrelated project"
        self.project.mkdir()
        (self.project / "package.json").write_text('{"name":"unrelated","version":"1.0.0"}')
        (self.project / ".npmrc").write_text("registry=http://127.0.0.1:1/\njson=true\n")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.lower().startswith("npm_config_")}
        self.env.update(
            npm_config_prefix=str(self.prefix),
            npm_config_cache=str(self.root / "cache"),
            npm_config_userconfig=str(userconfig),
            npm_config_globalconfig=str(globalconfig),
            npm_config_update_notifier="false",
            npm_config_fetch_retries="0",
            npm_config_fetch_timeout="5000",
            npm_config_json="true",
            npm_config_force="true",
            PATH=f"{self.prefix / 'bin'}:{self.tools_bin}:/usr/bin:/bin",
        )

    def stop_registry(self):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()

    def run_script(self, *args, expected=0, script=SCRIPT, **environment):
        result = run_command(
            ["/bin/bash", str(script), *args], env=self.env | environment,
            cwd=self.project, timeout=30,
        )
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        if expected:
            self.assertNotIn("Done:", result.stdout)
        self.assertFalse((self.prefix / "lib/node_modules/.dsh-update.lock").exists())
        return result

    def test_real_npm_configuration_scope_and_launcher_creation(self):
        """Real npm ignores the project registry and creates a runnable launcher."""
        self.run_script("--install", "--check")
        self.assertFalse(self.prefix.exists())
        self.run_script("--install")
        self.assertEqual(json.loads(self.manifest.read_text())["version"], "1.2.0")
        actual = run_command(
            [str(self.prefix / "bin/dsh"), "--version"], env=self.env, timeout=10,
        )
        actual.check_returncode()
        self.assertEqual(actual.stdout.strip(), "1.2.0")

    def test_changed_next_tag_is_fetched_without_rolling_back_files(self):
        """Publisher metadata changes after installation; npm must refetch it."""
        self.run_script("--install", script=self.next_script)
        self.tags["next"] = "1.1.0"
        self.requests.clear()
        self.run_script("--install", script=self.next_script)
        self.assertIn(f"/{PACKAGE}", self.requests)
        self.assertNotIn("/dsh-1.1.0.tgz", self.requests)
        self.assertEqual(json.loads(self.manifest.read_text())["version"], "1.2.0")

    def test_engine_failure_is_not_bypassed_by_inherited_force(self):
        """Actual npm rejects an impossible engine even with inherited force=true."""
        self.tags["latest"] = "2.0.0"
        failed = self.run_script("--install", expected=1)
        self.assertIn("EBADENGINE", failed.stderr)
        self.assertFalse(self.manifest.exists())

    def test_missing_bin_link_is_a_verification_error(self):
        """npm can persist the package while omitting its command; retry sees this."""
        failed = self.run_script("--install", expected=1, npm_config_bin_links="false")
        self.assertIn("npm did not create an executable dsh launcher", failed.stderr)
        self.assertTrue(self.manifest.exists())
        self.assertFalse((self.prefix / "bin/dsh").exists())
        retry = self.run_script("--install", expected=1)
        self.assertIn("Global dsh installation is incomplete", retry.stderr)
        self.assertNotIn(f"Add {self.prefix / 'bin'} to PATH", retry.stderr)

    def test_missing_path_is_an_error_after_package_installation(self):
        """The absolute CLI runs despite the updater rejecting shell reachability."""
        failed = self.run_script("--install", expected=1, PATH=f"{self.tools_bin}:/usr/bin:/bin")
        self.assertIn(f"Add {self.prefix / 'bin'} to PATH", failed.stderr)
        self.assertEqual(json.loads(self.manifest.read_text())["version"], "1.2.0")
        actual = run_command([str(self.prefix / "bin/dsh"), "--version"], env=self.env, timeout=10)
        actual.check_returncode()
        self.assertEqual(actual.stdout.strip(), "1.2.0")

    def test_real_npm_dry_run_success_does_not_establish_an_update(self):
        """Observe npm exit 0 with unchanged files, then the updater's rejection."""
        self.tags["latest"] = "1.1.0"
        self.run_script("--install")
        self.tags["latest"] = "1.2.0"
        env = self.env | {"npm_config_dry_run": "true"}
        npm = run_command(
            [NPM, "install", "--global", "--prefix", str(self.prefix),
             "--engine-strict", "--force=false", "--no-fund", "--no-audit", f"{PACKAGE}@1.2.0"],
            env=env, cwd=self.project, timeout=30,
        )
        self.assertEqual(npm.returncode, 0, npm.stdout + npm.stderr)
        self.assertEqual(json.loads(self.manifest.read_text())["version"], "1.1.0")
        rejected = self.run_script(expected=1, npm_config_dry_run="true")
        self.assertIn("dsh --version returned", rejected.stderr)
        self.assertEqual(json.loads(self.manifest.read_text())["version"], "1.1.0")


if __name__ == "__main__":
    unittest.main(verbosity=2)
