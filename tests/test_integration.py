#!/usr/bin/env python3
"""Opt-in probe of published dsh packages, including their real dependency graph.

Run: python3 -B tests/test_integration.py
Uses temporary npm prefixes/cache and executes normal npm lifecycle scripts.
A failed or inconclusive probe does not establish that real dsh can be installed.
"""

import json
import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile

from process_helpers import run_command


SCRIPT = Path(os.environ.get("DSH_UPDATE_SCRIPT", Path(__file__).resolve().parent.parent / "update.sh")).resolve()
NPM = shutil.which("npm")
BASELINE = "0.1.5-alpha.2"
PACKAGE = "@deepseek-ai/dsh"


class Inconclusive(Exception):
    pass


def run(argv, *, env, cwd, timeout=600):
    result = run_command(argv, env=env, cwd=cwd, timeout=timeout)
    if result.returncode:
        raise AssertionError(
            f"Command failed ({result.returncode}): {argv}\n"
            f"{result.stdout}\n{result.stderr}"
        )
    return result.stdout.strip()


def main():
    node = shutil.which("node")
    if not NPM or not node:
        raise Inconclusive("npm and node are required; published-package integration was not exercised")
    with tempfile.TemporaryDirectory(prefix="dsh-real-update-") as temporary:
        root = Path(temporary)
        tools = root / "tools-bin"
        tools.mkdir()
        (tools / "node").symlink_to(node)
        (tools / "npm").symlink_to(NPM)
        userconfig = root / "user.npmrc"
        userconfig.write_text("registry=https://registry.npmjs.org/\n")
        globalconfig = root / "global.npmrc"
        globalconfig.touch()
        base_env = {key: value for key, value in os.environ.items()
                    if not key.lower().startswith("npm_config_")}
        base_env.update(
            npm_config_cache=str(root / "cache"),
            npm_config_userconfig=str(userconfig),
            npm_config_globalconfig=str(globalconfig),
            npm_config_update_notifier="false",
            npm_config_fetch_retries="1",
            npm_config_fetch_timeout="30000",
            DSH_HOME=str(root / "dsh-home"),
        )

        def environment(prefix):
            return base_env | {
                "npm_config_prefix": str(prefix),
                "PATH": f"{prefix / 'bin'}:{tools}:/usr/bin:/bin",
            }

        prefix = root / "existing prefix"
        env = environment(prefix)
        print(json.dumps({
            "probe": "published dsh", "platform": platform.platform(),
            "node": run([node, "--version"], env=env, cwd=root),
            "npm": run([NPM, "--version"], env=env, cwd=root),
        }), flush=True)
        print(f"Preparing published baseline {BASELINE}...", flush=True)
        run([NPM, "install", "--global", "--engine-strict", "--force=false",
             "--no-fund", "--no-audit", f"{PACKAGE}@{BASELINE}"], env=env, cwd=root)
        if run([str(prefix / "bin/dsh"), "--version"], env=env, cwd=root) != BASELINE:
            raise AssertionError("The baseline CLI does not report the requested version")

        for operation, destination, args in (
            ("update", prefix, []),
            ("first install", root / "fresh prefix", ["--install"]),
        ):
            env = environment(destination)
            target = run([NPM, "view", "--global", f"{PACKAGE}@latest", "version", "--json=false"], env=env, cwd=root)
            if operation == "update" and target == BASELINE:
                raise Inconclusive("latest equals the baseline; a real version transition cannot be observed")
            print(f"Probing {operation}: {target}...", flush=True)
            run(["/bin/bash", str(SCRIPT), *args], env=env, cwd=root)
            manifest = destination / f"lib/node_modules/{PACKAGE}/package.json"
            persisted = json.loads(manifest.read_text())["version"]
            actual = run([str(destination / "bin/dsh"), "--version"], env=env, cwd=root)
            current = run([NPM, "view", "--global", f"{PACKAGE}@latest", "version", "--json=false"], env=env, cwd=root)
            if current != target:
                raise Inconclusive(f"latest moved from {target} to {current} during {operation}; rerun against a stable tag")
            if persisted != target or actual != target:
                raise AssertionError(f"{operation}: published={target}, manifest={persisted}, CLI={actual}")
            print(json.dumps({"observed": operation, "published": target, "manifest": persisted, "CLI": actual}), flush=True)
        print("Not exercised: profile startup, desktop application, web UI, model calls, other platforms.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except Inconclusive as error:
        print(f"INCONCLUSIVE: {error}", file=sys.stderr)
        raise SystemExit(2)
