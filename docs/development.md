# Development

[Back to the guide](../README.md)

## Project language

Use English throughout the project: documentation, CLI messages, comments, tests,
development instructions, and repository metadata. Keep the README as the single
English guide rather than maintaining localized copies.

## Update contract

`update.sh` updates an existing global npm installation of `@deepseek-ai/dsh` from
an npm channel: `latest` by default, or `next` with `--channel next`, which `next.sh`
passes. It uses npm from `PATH` and the Unix global layout (`bin/dsh` under the prefix).

1. Require Node.js, npm, the installed package manifest, and an active `dsh`.
2. Resolve the active launcher and npm's launcher to real paths; reject a mismatch
   before querying the registry or installing anything.
3. Read the installed manifest version and query the channel's tag in global context.
   Project `.npmrc` settings do not apply; user registry and authentication do.
   Force plain version output with `--json=false` and validate its format.
4. If the versions match, or the channel is `next` and its version lacks higher
   SemVer precedence than the installed one, verify `dsh --version` and skip
   installation. Otherwise, `--check` reports the available version and exits
   without installing.
5. Install the exact resolved version into the discovered prefix, with
   `--engine-strict` and `--force=false`.
6. Verify the installed launcher reports the expected version. Any npm failure or
   CLI verification failure means failure, even if files have already changed.

On `latest`, this follows the publisher's tag, even if it moves to an older version:
it is not a semver upgrade-only policy, and it is the way back from `next`. On `next`,
it is upgrade-only, so a `next` tag that falls behind does not downgrade dsh.
Installation is not atomic and the script does not roll back npm changes. A retry
rechecks the on-disk installation; broken installations may need repair through npm.
Running dsh sessions must be restarted.

The script does not launch profiles or contact a model. Normal npm lifecycle
scripts can run during installation.

## Local checks

From the repository root, with Node.js, Python 3.9+ and ShellCheck available:

```sh
bash -n update.sh
bash -n next.sh
shellcheck update.sh next.sh
python3 tests/test_update.py
```

The behavior suite runs the real Bash scripts with a controlled npm subprocess
in a temporary directory. It covers configuration, channel selection, installation
selection including SemVer precedence on `next`, `--check`, repeated runs, failure
before and after file changes, final version verification, retries, and `next.sh`
beside a downloaded `update.sh`. This tests the scripts' logic, not npm's internals.

## Real npm integration

```sh
python3 tests/test_integration.py
```

This opt-in test downloads and installs real dsh `0.1.5-alpha.2`, then updates it
to the current `latest` in a temporary npm prefix. It requires internet access,
a Node.js version compatible with both releases, and execution of normal npm
lifecycle scripts. It isolates npm configuration, cache and `DSH_HOME`.

It checks an unrelated project's registry override, inherited JSON/force settings,
`--check`, the persisted version, actual CLI version output, and a repeated run.
It also checks that the user's existing global CLI version is unchanged.
Profile startup and model calls are outside this test's scope, and so is the
`next` channel, which the behavior suite covers.

Both suites accept `DSH_UPDATE_SCRIPT` to test an isolated copy of the script.
