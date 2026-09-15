#!/usr/bin/env python3
"""Opt-in network test: install and update real dsh in a temporary npm prefix.

Downloads packages and executes their normal npm lifecycle scripts. Does not
modify the user's global dsh, npm configuration, or dsh profiles. Requires npm,
node, and internet access. Run explicitly: python3 tests/test_integration.py
"""

import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


SCRIPT = Path(os.environ.get("DSH_UPDATE_SCRIPT", Path(__file__).resolve().parent.parent / "update.sh")).resolve()
NPM = shutil.which("npm")
BASELINE = "0.1.5-alpha.2"


def run(argv, *, env, cwd, expected=0):
    result = subprocess.run(argv, env=env, cwd=cwd, text=True,
                            capture_output=True, timeout=600)
    if result.returncode != expected:
        raise AssertionError(
            f"Command failed ({result.returncode}): {argv}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout


def main():
    if not NPM:
        raise SystemExit("npm is required")
    original_cli = shutil.which("dsh")
    original_version = subprocess.check_output([original_cli, "--version"], text=True) if original_cli else None
    with tempfile.TemporaryDirectory(prefix="dsh-real-update-") as temporary:
        root = Path(temporary)
        prefix = root / "global prefix"
        project = root / "unrelated project"
        project.mkdir()
        (project / "package.json").write_text('{"name":"unrelated-project","version":"1.0.0"}')
        (project / ".npmrc").write_text("registry=http://127.0.0.1:1/\njson=true\n")
        userconfig = root / "user.npmrc"
        userconfig.write_text("registry=https://registry.npmjs.org/\n")
        globalconfig = root / "global.npmrc"
        globalconfig.touch()
        env = {key: value for key, value in os.environ.items()
               if not key.lower().startswith("npm_config_")}
        env.update(
            npm_config_prefix=str(prefix),
            npm_config_cache=str(root / "cache"),
            npm_config_userconfig=str(userconfig),
            npm_config_globalconfig=str(globalconfig),
            npm_config_fetch_retries="1",
            npm_config_fetch_timeout="30000",
            DSH_HOME=str(root / "dsh-home"),
            PATH=f"{prefix / 'bin'}:{os.environ['PATH']}",
        )
        latest = run([NPM, "view", "--global", "@deepseek-ai/dsh@latest", "version", "--json=false"], env=env, cwd=project).strip()
        if latest == BASELINE:
            raise AssertionError("Baseline must differ from latest for an actual update test")
        print(f"Installing real baseline dsh {BASELINE} in temporary prefix...", flush=True)
        run([NPM, "install", "--global", "--engine-strict", "--force=false", "--no-fund", "--no-audit",
             f"@deepseek-ai/dsh@{BASELINE}"], env=env, cwd=project)
        manifest = prefix / "lib/node_modules/@deepseek-ai/dsh/package.json"
        if json.loads(manifest.read_text())["version"] != BASELINE:
            raise AssertionError("Baseline installation did not persist")

        # These preferences must not affect the lookup format or engine check.
        env.update(npm_config_json="true", npm_config_force="true")
        print("Checking with project registry override and inherited JSON/force...", flush=True)
        checked = run(["/bin/bash", str(SCRIPT), "--check"], env=env, cwd=project)
        if latest not in checked or json.loads(manifest.read_text())["version"] != BASELINE:
            raise AssertionError("--check did not report latest without changing baseline")

        print(f"Updating real dsh {BASELINE} -> {latest}...", flush=True)
        updated = run(["/bin/bash", str(SCRIPT)], env=env, cwd=project)
        if f"Done: dsh {latest}" not in updated:
            raise AssertionError(f"Missing verified success: {updated}")
        if json.loads(manifest.read_text())["version"] != latest:
            raise AssertionError("Updated version did not persist")
        actual = run([str(prefix / "bin/dsh"), "--version"], env=env, cwd=project).strip()
        if actual != latest:
            raise AssertionError(f"Actual CLI version is {actual}, expected {latest}")
        print("Checking repeated execution...", flush=True)
        repeated = run(["/bin/bash", str(SCRIPT)], env=env, cwd=project)
        if "Already on the latest" not in repeated:
            raise AssertionError(f"Repeated update failed: {repeated}")
        if original_cli and subprocess.check_output([original_cli, "--version"], text=True) != original_version:
            raise AssertionError("User's global dsh version changed")
        print(f"PASS: real npm update {BASELINE} -> {latest}; --check; repeat; original dsh unchanged.", flush=True)


if __name__ == "__main__":
    main()
