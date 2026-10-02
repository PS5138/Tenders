#!/usr/bin/env bash
# Downloads the Supabase Auth (GoTrue) release binary for this platform: Linux x86_64 or
# macOS on Apple silicon. Other platforms run the stack with Docker Compose from the
# repository root instead (there is no release asset for them).
set -euo pipefail
source "$(dirname "$0")/env.sh"
if [ -x "$AUTH_BIN" ]; then echo "Supabase Auth already installed at $AUTH_BIN"; exit 0; fi
os="$(uname -s)" arch="$(uname -m)"
case "$os/$arch" in
  Linux/x86_64) asset="x86" ;;
  Darwin/arm64) asset="darwin-arm64" ;;
  *) echo "unsupported platform: use Docker Compose (no Supabase Auth release asset for $os/$arch)" >&2; exit 1 ;;
esac
mkdir -p "$LOCAL_DIR/bin" "$LOCAL_DIR/auth"
url="https://github.com/supabase/auth/releases/download/$AUTH_VERSION/auth-$AUTH_VERSION-$asset.tar.gz"
echo "Downloading $url"
curl -fsSL "$url" -o "$LOCAL_DIR/auth/auth.tgz"
tar -xzf "$LOCAL_DIR/auth/auth.tgz" -C "$LOCAL_DIR/auth"
mv "$LOCAL_DIR/auth/auth" "$AUTH_BIN"
chmod +x "$AUTH_BIN"
# macOS quarantines downloaded files and Gatekeeper then refuses to start the binary.
if [ "$os" = Darwin ] && xattr -p com.apple.quarantine "$AUTH_BIN" >/dev/null 2>&1; then
  xattr -d com.apple.quarantine "$AUTH_BIN"
fi
echo "Installed Supabase Auth $AUTH_VERSION ($asset)"
