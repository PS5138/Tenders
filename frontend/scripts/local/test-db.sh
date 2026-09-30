#!/usr/bin/env bash
# The joined tests create and drop their own frontend database.
set -euo pipefail
cd "$(dirname "$0")/../.."
exec pnpm test:integration
