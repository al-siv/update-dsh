#!/usr/bin/env bash
# Install or update the global npm CLI of DeepSeek Harness.
set -euo pipefail

usage() {
  cat <<'EOF'
Install or update the DeepSeek Harness CLI (@deepseek-ai/dsh) from an npm channel.

Usage: ./update.sh [--channel latest|next] [--install] [--check]
       ./next.sh [--install] [--check]   (the same as --channel next)
  --channel  latest (default) installs its version even if it is older than
             the installed one; next installs its version only if it is newer.
  --check    Show installed and available versions without installing.
  --install  Allow a first global installation if the package is missing.
  -h, --help Show help.

Uses npm from PATH. Restart running dsh sessions after updating.
The separate desktop application is not installed or updated by this script.
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

# Print SemVer precedence (-1, 0, 1); reserve failure status for errors.
compare_versions() {
  node --input-type=commonjs - "$@" <<'NODE'
const parse = (version, source, allowV = false) => {
  const value = allowV ? version.replace(/^v/, '') : version;
  const match = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$/.exec(value);
  const prerelease = match && match[4] ? match[4].split('.') : [];
  if (!match || prerelease.some((id) => /^0[0-9]+$/.test(id))) {
    throw new Error(`${source}: ${version}`);
  }
  return [match.slice(1, 4).map(BigInt), prerelease];
};
const order = (a, b) => (a > b) - (a < b);
const numeric = (identifier) => /^[0-9]+$/.test(identifier);
const compare = ([releaseA, preA], [releaseB, preB]) => {
  for (let i = 0; i < 3; i++) {
    if (releaseA[i] !== releaseB[i]) return order(releaseA[i], releaseB[i]);
  }
  // A release outranks its prereleases.
  if (!preA.length || !preB.length) return order(preB.length, preA.length);
  for (let i = 0; i < Math.min(preA.length, preB.length); i++) {
    const [a, b] = [preA[i], preB[i]];
    // Numeric identifiers compare as numbers and rank below alphanumeric ones.
    const result = numeric(a) && numeric(b) ? order(BigInt(a), BigInt(b))
      : numeric(a) || numeric(b) ? order(numeric(b), numeric(a))
      : order(a, b);
    if (result) return result;
  }
  return order(preA.length, preB.length);
};
try {
  const available = parse(process.argv[2], 'npm returned an invalid version');
  // npm can preserve a leading v in an installed tarball's manifest.
  const installed = process.argv[3] === undefined ? null
    : parse(process.argv[3], 'Installed package has an invalid version', true);
  console.log(installed ? compare(available, installed) : 1);
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exit(1);
}
NODE
}

verify_active_launcher() {
  local active_launcher
  [[ -f "$launcher" && -x "$launcher" ]] ||
    fail "Global dsh installation is incomplete: no executable launcher at $launcher. Repair $package with npm (check bin-links and executable permissions), then retry."
  active_launcher=$(command -v dsh) || fail "dsh was not found in PATH. Add $prefix/bin to PATH and retry."
  node --input-type=commonjs - "$active_launcher" "$launcher" <<'NODE' || exit 1
const fs = require('node:fs');
try {
  if (fs.realpathSync(process.argv[2]) !== fs.realpathSync(process.argv[3])) {
    throw new Error('dsh in PATH differs from the global installation of the current npm. Check PATH.');
  }
} catch (error) {
  console.error(`Error: ${error.message}`);
  process.exit(1);
}
NODE
}

read_cli_version() {
  local output status
  if output=$("$launcher" --version); then
    printf '%s\n' "$output"
  else
    status=$?
    printf 'Error: dsh --version failed (exit %s) at %s.\n' "$status" "$launcher" >&2
    if [[ -n "$output" ]]; then
      printf '%s\n' "$output" >&2
    fi
    return "$status"
  fi
}

channel=latest
check_only=false
allow_install=false
while (($#)); do
  case "$1" in
    --channel)
      [[ "${2:-}" =~ ^(latest|next)$ ]] || { usage >&2; fail '--channel takes latest or next.'; }
      channel=$2
      shift
      ;;
    --check) check_only=true ;;
    --install) allow_install=true ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; fail "Unknown argument: $1" ;;
  esac
  shift
done

command -v node >/dev/null 2>&1 || fail 'node was not found in PATH.'
command -v npm >/dev/null 2>&1 || fail 'npm was not found in PATH.'

package='@deepseek-ai/dsh'
prefix=$(npm prefix --global)
package_root=$(npm root --global)
manifest="$package_root/$package/package.json"
launcher="$prefix/bin/dsh"
if ! "$allow_install" && [[ ! -f "$manifest" ]]; then
  fail "Global installation of $package was not found in $package_root. Run this script with --install to install the CLI."
fi

# Serialize updates before reading versions, including registry lookup and final
# verification. A read-only check does not require write access to this directory.
if ! "$check_only"; then
  if "$allow_install"; then
    mkdir -p -- "$package_root" || fail "Could not create the global npm root at $package_root. Check its permissions."
  fi
  lock_dir="$package_root/.dsh-update.lock"
  mkdir -- "$lock_dir" ||
    fail "Could not acquire update lock at $lock_dir. Another dsh update may be running; retry when it finishes."
  trap 'rmdir -- "$lock_dir"' EXIT
  trap 'exit 129' HUP
  trap 'exit 130' INT
  trap 'exit 143' TERM
fi

# Read the installation state only after acquiring the lock: another installer
# may have created it while npm was discovering the prefix.
has_installation=false
installed=
if [[ -f "$manifest" ]]; then
  has_installation=true
  verify_active_launcher
  installed=$(node --input-type=commonjs -e 'console.log(require(process.argv[1]).version)' "$manifest")
  printf 'Installed: %s\n' "$installed"
else
  "$allow_install" || fail "Global installation of $package was not found in $package_root. Run this script with --install to install the CLI."
  active_launcher=$(command -v dsh || true)
  [[ -z "$active_launcher" ]] ||
    fail "dsh in PATH ($active_launcher) is not owned by the current npm installation. Check PATH before installing."
  printf 'Installed: not installed\n'
fi
printf 'Checking npm %s...\n' "$channel"
# Use the same npm configuration scope as the global installation, and keep
# user/project JSON preferences from changing the version's representation.
available=$(npm view --global "$package@$channel" version --json=false --fetch-retries=1 --fetch-timeout=15000)
# Keep this assignment outside a condition so a comparator failure aborts.
if "$has_installation"; then
  precedence=$(compare_versions "$available" "$installed")
else
  precedence=$(compare_versions "$available")
fi
printf 'Available: %s\n' "$available"

# On next, a version without higher precedence is kept, so a next tag that
# lags behind does not downgrade dsh.
keep=
if "$has_installation" && [[ "$installed" == "$available" ]]; then
  keep="Already on the $channel channel version."
elif "$has_installation" && [[ "$channel" == next && "$precedence" -le 0 ]]; then
  keep='The next channel version is not newer than the installed one; nothing to install.'
fi
if [[ -n "$keep" ]]; then
  actual=$(read_cli_version)
  [[ "$actual" == "$installed" ]] || fail "dsh --version returned $actual instead of $installed."
  printf '%s\n' "$keep"
  exit 0
fi

if "$check_only"; then
  printf 'Run the script without --check to install.\n'
  exit 0
fi

printf 'Installing %s in %s...\n' "$available" "$prefix"
# Pin the version checked above; reject incompatible Node.js engines.
if npm install --global --prefix "$prefix" --engine-strict --force=false --no-fund --no-audit "$package@$available"; then
  :
else
  status=$?
  printf 'Error: npm installation failed (exit %s). See the npm output above.\n' "$status" >&2
  exit "$status"
fi
[[ -f "$launcher" && -x "$launcher" ]] || fail "npm did not create an executable dsh launcher at $launcher. Check npm's bin-links setting."
actual=$(read_cli_version)
[[ "$actual" == "$available" ]] || fail "After installation, dsh --version returned $actual instead of $available."
verify_active_launcher
printf 'Done: dsh %s. Restart running dsh sessions.\n' "$actual"
