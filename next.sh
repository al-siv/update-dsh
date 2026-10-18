#!/usr/bin/env bash
# Update DeepSeek Harness from the npm next channel; update.sh does the work.
exec "$BASH" "$(dirname -- "${BASH_SOURCE[0]}")/update.sh" --channel next "$@"
