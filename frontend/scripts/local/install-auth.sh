#!/usr/bin/env bash
# Downloads the Supabase Auth (GoTrue) release binary for Linux x86_64.
# On macOS use the Supabase CLI (`supabase start`) instead; see README.
set -euo pipefail
source "$(dirname "$0")/env.sh"
if [ -x "$AUTH_BIN" ]; then echo "Supabase Auth already installed at $AUTH_BIN"; exit 0; fi
mkdir -p "$LOCAL_DIR/bin" "$LOCAL_DIR/auth"
url="https://github.com/supabase/auth/releases/download/$AUTH_VERSION/auth-$AUTH_VERSION-x86.tar.gz"
echo "Downloading $url"
curl -fsSL "$url" -o "$LOCAL_DIR/auth/auth.tgz"
tar -xzf "$LOCAL_DIR/auth/auth.tgz" -C "$LOCAL_DIR/auth"
mv "$LOCAL_DIR/auth/auth" "$AUTH_BIN"
echo "Installed Supabase Auth $AUTH_VERSION"
