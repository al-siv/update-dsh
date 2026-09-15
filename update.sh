#!/usr/bin/env bash
# Update the existing global npm installation of DeepSeek Harness.
set -euo pipefail

usage() {
  cat <<'EOF'
Update DeepSeek Harness (@deepseek-ai/dsh) from the npm latest channel.

Usage: ./update.sh [--check]
  --check    Show installed and available versions without installing.
  -h, --help Show help.

Uses npm from PATH. Restart running dsh sessions after updating.
EOF
}

fail() {
  printf 'Error: %s\n' "$*" >&2
  exit 1
}

check_only=false
case "${1:-}" in
  --check) check_only=true ;;
  -h|--help) usage; exit 0 ;;
  '') ;;
  *) usage >&2; fail "Unknown argument: $1" ;;
esac
[[ $# -le 1 ]] || fail 'Too many arguments.'

command -v node >/dev/null 2>&1 || fail 'node was not found in PATH.'
command -v npm >/dev/null 2>&1 || fail 'npm was not found in PATH.'

package='@deepseek-ai/dsh'
prefix=$(npm prefix --global)
package_root=$(npm root --global)
manifest="$package_root/$package/package.json"
launcher="$prefix/bin/dsh"
[[ -f "$manifest" ]] || fail "Global installation of $package was not found in $package_root."
active_launcher=$(command -v dsh) || fail 'dsh was not found in PATH.'

# Refuse to update a different installation from the one the shell uses.
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

installed=$(node --input-type=commonjs -e 'console.log(require(process.argv[1]).version)' "$manifest")
printf 'Installed: %s\nChecking npm latest...\n' "$installed"
# Use the same npm configuration scope as the global installation, and keep
# user/project JSON preferences from changing the version's representation.
latest=$(npm view --global "$package@latest" version --json=false --fetch-retries=1 --fetch-timeout=15000)
[[ "$latest" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?(\+[0-9A-Za-z.-]+)?$ ]] ||
  fail "npm returned an invalid version: $latest"
printf 'Available: %s\n' "$latest"

if [[ "$installed" == "$latest" ]]; then
  actual=$("$launcher" --version)
  [[ "$actual" == "$latest" ]] || fail "dsh --version returned $actual instead of $latest."
  printf 'Already on the latest channel version.\n'
  exit 0
fi

if "$check_only"; then
  printf 'Run the script without --check to install.\n'
  exit 0
fi

printf 'Installing %s in %s...\n' "$latest" "$prefix"
# Pin the version checked above; reject incompatible Node.js engines.
npm install --global --prefix "$prefix" --engine-strict --force=false --no-fund --no-audit "$package@$latest"
actual=$("$launcher" --version)
[[ "$actual" == "$latest" ]] || fail "After installation, dsh --version returned $actual instead of $latest."
printf 'Done: dsh %s. Restart running dsh sessions.\n' "$actual"
