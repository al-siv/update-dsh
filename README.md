# Update dsh — DeepSeek Harness updater

**Update DeepSeek Harness (`dsh`, `@deepseek-ai/dsh`) to npm `latest` or `next`.**
A small Bash script to check the available version, install or update the global
CLI your shell uses, and verify the result.

## How to update dsh

For a global npm installation, the direct update command is:

```sh
npm install --global @deepseek-ai/dsh@latest
dsh --version
```

Restart running dsh sessions after updating. The `latest` tag is chosen by the
package publisher and can point to a prerelease.

### Update with this script

Use the script to check the active installation before updating, install the exact
version it found, and confirm that `dsh --version` matches afterward.

Download [update.sh](update.sh) using **Download raw file**, then run these commands
in the download directory:

```sh
bash update.sh --check   # show installed and available versions
bash update.sh           # update and verify
```

Requires **Bash, Node.js, and npm**, with `node` and `npm` in `PATH`. Regular updates
require a working global npm installation of dsh and `dsh` in `PATH`. The script
uses the Unix npm directory layout (macOS and Linux); native Windows installations
are outside its scope.

If the installed version already matches `latest`, the script verifies the CLI
and skips reinstallation. `--check` does not install anything.

### First installation

If the global npm package is missing, opt in to installation:

```sh
bash update.sh --install --check  # show the version without installing
bash update.sh --install          # install and verify the global CLI
```

`--install` also works for an existing installation and follows the same channel
rules as an update. A different `dsh` already in `PATH` must be resolved first.
After installation, npm's global `bin` directory must be in `PATH`, for example:

```sh
export PATH="$(npm prefix --global)/bin:$PATH"
```

The script reports success only after the installed CLI returns the expected
version and `dsh` in `PATH` resolves to that installation. Errors go to stderr;
validation errors return exit code 1, while CLI and npm failures preserve their
exit codes.
An installation may have changed files before a later verification error.

### Update to `next`

The publisher's `next` tag can point to a newer version than `latest`. To follow it,
download [next.sh](next.sh) into the same directory as `update.sh`; it runs
`update.sh --channel next`:

```sh
bash next.sh --check   # show installed and next versions
bash next.sh           # update to next if it is newer, and verify
bash next.sh --install # allow a first installation from next
```

`next.sh` installs only a version newer than the installed one, so a `next` tag that
falls behind `latest` does not downgrade dsh. `bash update.sh` installs `latest` even
when it is older than the installed version: that is the way back from `next`.

## Desktop application

The separate DeepSeek Harness `.app` uses its own desktop updater. This script's
`--install` option installs the npm CLI; the desktop application and its bundled
CLI are managed by the application.

## Using npx instead?

If you normally start DeepSeek Harness with `npx`, request the `latest` tag explicitly:

```sh
npx @deepseek-ai/dsh@latest web
```

This launches dsh through npx; it does not update a global installation. The updater
script is for global npm installs. For source checkouts, follow the
[upstream build instructions](https://github.com/deepseek-ai/deepseek-harness#run-from-source).

## Troubleshooting

| Problem | What to check |
| --- | --- |
| No global npm installation is found | Run `bash update.sh --install` to install the CLI, or add `--check` to preview the version. |
| `dsh` is missing from `PATH` | Add `$(npm prefix --global)/bin` to `PATH`, then retry. An npm package may already have been installed before this verification error. |
| The global installation is incomplete | Repair `@deepseek-ai/dsh` through npm using the intended version, with `bin-links=true` and executable permissions, then retry. Adding a PATH entry cannot restore a missing launcher. |
| dsh in `PATH` differs from the current npm installation | Run `command -v dsh`, `command -v npm`, and `npm prefix --global`. Activate the Node environment that owns your dsh installation. |
| npm reports `EBADENGINE` | Check `npm view --global @deepseek-ai/dsh@latest engines` (`@next` for `next.sh`) and use a compatible Node.js version. The script enforces engine requirements. |
| npm cannot reach the registry | Check your network and `npm config get registry`. The script respects your user-level npm registry and authentication. |
| The update lock cannot be acquired | Wait for the other updater to finish and retry. If it was forcibly terminated, confirm no updater or npm install is still running for that prefix, then remove the empty lock directory shown in the error. |
| An update fails | Read the npm or CLI error. npm may already have changed files; there is no automatic rollback. Repair a broken installation through npm before retrying. |

## How it works

The script ignores unrelated project `.npmrc` settings, pins the resolved version,
rejects incompatible Node.js engines even with inherited npm `force=true`, and
returns a nonzero exit code if installation or CLI verification fails.
CLI verification failures include a stderr message and preserve the CLI's exit
code; any captured stdout diagnostic is forwarded to stderr.
Update runs use an exclusive lock to prevent overlapping installations; `--check`
does not require write access to the global installation. Invalid versions and
comparison errors are reported as failures.

[MIT license](LICENSE) · [Implementation and tests](docs/development.md) ·
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) ·
[npm package](https://www.npmjs.com/package/@deepseek-ai/dsh) ·
[npm install reference](https://docs.npmjs.com/cli/v11/commands/npm-install) ·
[npx reference](https://docs.npmjs.com/cli/v11/commands/npx)

An independent community utility; not an official DeepSeek project.
