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

## What earns a test its place

Each retained test must add a distinct observation, an independent reference, or
an adversarial failure state that the other probes do not establish. The contract
states the required behavior; it is not evidence that the behavior occurred.
Test count and a green aggregate are not evidence of integration capability.
Each test's docstring identifies its additional observation.

Removed checks included mocked npm engine/configuration rules, ordinary success
paths repeated by real npm probes, exact flag spelling and CLI message assertions,
and a standalone check of standard subprocess output capture. The fault injector
no longer pretends to implement npm's JSON, project-config, force, or engine rules.
The old mocked no-write success was replaced by an actual npm dry-run experiment.

A test should not be added merely to mirror a branch or sentence in the contract.
Describe the competing failure explanation it distinguishes and its observable
consequence. Missing tools, skipped probes, and failed integration attempts must
be reported separately from successful observations.

## Local probes

```sh
python3 tests/test_update.py
python3 tests/test_npm.py
python3 tests/test_process.py
```

### Controlled counterexamples: test_update.py

Bash, Node.js, subprocesses, signals, and files are real. npm is only a fault
injector that supplies versions, writes files, pauses, or fails after a write.
These cases establish the updater's response to the supplied state; they do not
establish that real npm has a particular crash or rollback behavior.

| Probe | Additional observation |
| --- | --- |
| Comparison process failure | Exit 42 is not confused with a valid comparison saying the target is older; installation does not start. |
| Failure after a durable write | A nonzero installer exit can coexist with a new on-disk version; retry uses that state without another installation. |
| CLI failure after files changed | A child diagnostic written to stdout survives on stderr with its exit code, while the changed manifest remains visible. |
| Foreign launcher | Two real launcher paths coexist, and the foreign executable and global manifest remain unchanged when they disagree. |
| Concurrent first installations | The global root initially lacks a package manifest; a second process cannot pass the paused first installer. |
| Concurrent next updates | An older installation is paused while a newer tag is offered; retry and a subsequent older tag cannot reverse the resulting version. |
| SemVer reference vectors | Numeric/ASCII ordering, prerelease length, release precedence, and ignored build metadata are checked against externally derived data. |
| SIGTERM during installation | Actual Bash trap delivery permits another update after a subprocess is terminated, without a stale lock blocking it. |

The SemVer oracle is [SemVer 2.0.0, clauses 10 and 11](https://semver.org/spec/v2.0.0.html),
not a second copy of the production comparator. The mixed-identifier and build
metadata cases distinguish specific wrong orderings; arbitrary extra versions do
not add evidence. The finite vectors do not prove correctness for every input.

### Real npm, local packages: test_npm.py

A temporary HTTP registry serves tiny packages. Real npm handles configuration,
cache, engine checks, files, and launcher creation. Its project registry override
is deliberately unusable; only the isolated user registry can serve the packages.
Required npm/Node tools being absent is an unavailable observation, reported as
an INCONCLUSIVE setup error rather than a successful skipped run.

| Probe | Additional observation |
| --- | --- |
| Configuration scope and launcher creation | Real npm uses the user registry despite the conflicting project config; preview leaves the prefix absent, and installation produces an independently executable CLI. |
| Changed next tag | The registry receives a fresh metadata request after changing its tag; the installed newer version remains and the older tarball is not requested. |
| Engine rejection | Real npm refuses an impossible Node requirement despite inherited force=true, without persisting the package. |
| Disabled bin links | npm can persist a package with no launcher; a retry distinguishes incomplete installation from a missing PATH entry. |
| Missing PATH entry | The absolute CLI actually runs after installation even though shell reachability verification fails. |
| Dry-run success | A direct real npm invocation returns 0 without changing the installed version; the updater rejects that same condition. |

These observations concern npm integration with those sample packages. They do
not establish that the published dsh dependency graph or native modules install.

### Process lifecycle: test_process.py

These probes inspect actual Unix process state. One starts a live leader and a
TERM-ignoring child. The other lets the leader exit while the child holds captured
pipes open; checking only the leader's status would miss that case. Both verify
that no process in the observed group remains able to write after the timeout.
An unavailable process-state tool is reported as INCONCLUSIVE, not as success.

The shared helper starts commands in separate sessions and kills their groups
before collecting output and allowing temporary-file cleanup. This covers group
members; descendants that deliberately create another session are not exercised.

## Published-package integration: test_integration.py

```sh
python3 tests/test_integration.py
```

This opt-in network probe adds the observation unavailable from the small local
packages: published dsh and its real dependency graph can be installed and its
CLI can execute in the measured environment. It prepares real 0.1.5-alpha.2,
then exercises both an update and a first installation from latest using temporary
prefixes and cache. It records Node/npm/platform and compares the independently
queried registry version with the persisted manifest and direct CLI output.
Normal npm lifecycle scripts execute; a temporary prefix is not a sandbox.

Repeated --check, message-format, and idempotence assertions were removed from
this probe because they did not add published-package evidence. If latest changes
during a measured operation, the probe reports INCONCLUSIVE and exits 2. A failed
installation or CLI invocation remains a failed integration observation; success
of the local-package suite must not substitute for it. If the probe is not run,
published-package integration remains unverified for that environment.

No retained probe establishes profile startup, model access, web UI behavior,
desktop-app updates, or compatibility with other OS/Node/npm versions. Report
what was observed and what remains unknown, without deriving those capabilities
from a pass count.

The script probes accept DSH_UPDATE_SCRIPT to exercise an isolated script copy.

## Observed run: 2026-11-02

On macOS 27.0.1 arm64, Node v24.22.1, npm 11.19.1, the controlled fault probes,
local real-npm probes, and process-lifecycle probes completed their observations.
This records that execution, not a claim about other machines or future releases.

The published-package probe failed with npm exit 1 and EIDLETIMEOUT for
registry.npmjs.org:443 while preparing baseline @deepseek-ai/dsh@0.1.5-alpha.2.
The updater had not been invoked. Neither the published-package update nor its
fresh latest installation was established by that run. The failure is retained
as an integration limitation, not converted into a skipped or successful result.

A repeat at 2026-11-02T04:14:13+00:00 completed with exit 0 in the same recorded environment.
The baseline CLI reported 0.1.5-alpha.2. Both the update to 0.2.1-rc.5 and the first
installation of 0.2.1-rc.5 in a fresh prefix completed; the independently queried
registry version, installed manifest, and direct CLI output agreed in both cases.
EIDLETIMEOUT did not recur. This confirms these installation/version-command
observations for this run; profile startup, desktop/web behavior, model access,
and other platforms remain unexercised.
