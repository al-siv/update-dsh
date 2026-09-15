# Update dsh — DeepSeek Harness updater

**Update DeepSeek Harness (`dsh`, `@deepseek-ai/dsh`) to npm `latest`.**
A small Bash script for an existing global npm installation: check the available
version, update the installation your shell uses, and verify the result.

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

Requires **Bash, Node.js, npm, and a working global npm installation of dsh**, with
`node`, `npm`, and `dsh` in `PATH`. The script uses the Unix npm directory layout
(macOS and Linux); native Windows installations are outside its scope.

If the installed version already matches `latest`, the script verifies the CLI
and skips reinstallation. `--check` does not install anything.

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
| `dsh` is missing, or no global installation is found | Install it with `npm install --global @deepseek-ai/dsh@latest`, then check `dsh --version`. |
| dsh in `PATH` differs from the current npm installation | Run `command -v dsh`, `command -v npm`, and `npm prefix --global`. Activate the Node environment that owns your dsh installation. |
| npm reports `EBADENGINE` | Check `npm view --global @deepseek-ai/dsh@latest engines` and use a compatible Node.js version. The script enforces engine requirements. |
| npm cannot reach the registry | Check your network and `npm config get registry`. The script respects your user-level npm registry and authentication. |
| An update fails | Read the npm or CLI error. npm may already have changed files; there is no automatic rollback. Repair a broken installation through npm before retrying. |

## How it works

The script ignores unrelated project `.npmrc` settings, pins the resolved version,
rejects incompatible Node.js engines even with inherited npm `force=true`, and
returns a nonzero exit code if installation or CLI verification fails.

[MIT license](LICENSE) · [Implementation and tests](docs/development.md) ·
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) ·
[npm package](https://www.npmjs.com/package/@deepseek-ai/dsh) ·
[npm install reference](https://docs.npmjs.com/cli/v11/commands/npm-install) ·
[npx reference](https://docs.npmjs.com/cli/v11/commands/npx)

An independent community utility; not an official DeepSeek project.
