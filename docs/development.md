# Development

[Back to the guide](../README.md)

## Project language

Use English throughout the project: documentation, CLI messages, comments, tests,
development instructions, and repository metadata. Keep the README as the single
English guide rather than maintaining localized copies.

## Update contract

`update.sh` updates a global npm installation of `@deepseek-ai/dsh` from an npm
channel: `latest` by default, or `next` with `--channel next`, which `next.sh` passes.
`--install` also permits a first installation when the package is absent. It uses
npm from `PATH` and the Unix global layout (`bin/dsh` under the prefix).

1. Require Node.js and npm. Without `--install`, require an installed package.
   Install/update runs acquire an exclusive directory lock in the global npm root
   before reading versions. The lock covers lookup, installation, and verification;
   another update run fails immediately and can be retried after the first exits.
   `--install` may create the global npm root before acquiring the lock. Recheck
   the manifest under the lock so simultaneous first installations share it.
   `--check` does not acquire a lock or require write access to the installation.
2. For an existing package, require an active `dsh` and compare its real path to
   npm's launcher; reject a mismatch before lookup or installation. For a first
   installation, refuse to shadow a different `dsh` already in `PATH`.
   Check that npm's launcher is an executable file before diagnosing `PATH`:
   a missing or non-executable launcher is an incomplete installation to repair.
3. Read the installed manifest version and query the channel's tag in global context.
   Project `.npmrc` settings do not apply; user registry and authentication do.
   Force plain version output with `--json=false`. Validate both versions as SemVer;
   an installed manifest may have a leading `v`, which is ignored for comparison.
   The comparator prints precedence and exits successfully for every valid order;
   invalid versions and process failures abort instead of skipping an update.
4. If an existing version matches, or the channel is `next` and its version lacks
   higher SemVer precedence than the installed one, verify `dsh --version` and skip
   installation. Without an installed version, validate the available version
   without a fabricated comparison baseline. `--install --check` also works for
   a missing package and does not create the global root or install anything.
5. Install the exact resolved version into the discovered prefix, with
   `--engine-strict` and `--force=false`.
6. Verify an executable launcher exists, it reports the expected version, and the
   active `dsh` resolves to it. Missing `PATH` entries are errors with a remedy;
   the script does not edit shell configuration. Any npm or verification failure
   means failure, even if files have already changed. Script validation errors
   return 1; npm failures retain npm's status and installation failures add a
   short stderr summary after npm's own output. Successful checks return 0.
   A failing `dsh --version` keeps its exit status and emits a stderr message;
   captured stdout from that failed command is also forwarded to stderr.

On `latest`, this follows the publisher's tag, even if it moves to an older version:
it is not a semver upgrade-only policy, and it is the way back from `next`. On `next`,
it is upgrade-only, so a `next` tag that falls behind does not downgrade dsh.
Installation is not atomic and the script does not roll back npm changes. A retry
rechecks the on-disk installation; broken installations may need repair through npm.
Running dsh sessions must be restarted.

The lock is the empty `.dsh-update.lock` directory under `npm root --global`.
It is released on normal exit, errors, and handled HUP/INT/TERM signals. After an
uncatchable termination, it may remain: confirm that no updater or npm install is
still running for this prefix before removing the empty directory reported in the
error. The lock coordinates these scripts; direct npm commands do not use it.

The script does not launch profiles or contact a model. Normal npm lifecycle
scripts can run during installation. It manages the npm CLI; the separate desktop
application and its bundled runtime use the desktop application's updater.

## Local checks

From the repository root, with Node.js, Python 3.9+ and ShellCheck available:

```sh
bash -n update.sh
bash -n next.sh
shellcheck update.sh next.sh
python3 tests/test_update.py
python3 tests/test_npm.py
python3 tests/test_process.py
```

The behavior suite runs the real Bash scripts with a controlled npm subprocess
in a temporary directory. It covers configuration, channel selection, installation
selection including SemVer precedence on `next`, `--check`, repeated runs, failure
before and after file changes, final version verification, retries, and `next.sh`
beside a downloaded `update.sh`. Regression cases cover invalid installed versions,
comparator process failure, read-only checks, overlapping updates, and lock release
after termination. First-install cases cover a missing global root, `--check`,
both channels, retries, conflicting launchers, missing `PATH` entries, invalid
versions, npm failures, and simultaneous installers. This tests the scripts'
logic, not npm's internals.

`test_npm.py` runs real npm against a temporary local registry with tiny packages.
It checks fresh installation, repeat runs, `next` precedence, engine rejection,
missing bin links, and missing `PATH` entries. It needs no external registry and
isolates npm configuration, cache, prefix, and tool paths.

All test command runners use a shared process helper. It starts each command in
its own session and, on a timeout or runner exception, kills its process group
and collects output before temporary files are removed. This also stops children
holding pipes open after their parent has exited. `test_process.py` checks these
cases with real parent/child processes and verifies exit status and output capture.
These forced test shutdowns may leave a lock inside the temporary prefix; the
prefix is removed only after the command group has been stopped.

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
It also checks a first installation with `--install` in a fresh prefix, reusing
the isolated cache and keeping the user's existing `dsh` out of that test's PATH.
It also checks that the user's existing global CLI version is unchanged.
Profile startup and model calls are outside this test's scope, and so is the
`next` channel, which the behavior suite covers.

The behavior and npm integration suites accept `DSH_UPDATE_SCRIPT` to test an
isolated copy of the script.
