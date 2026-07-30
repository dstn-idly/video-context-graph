#!/usr/bin/env bash
# Fetch the TwitchDownloaderCLI binary for this machine into ./bin.
# Self-contained build — no .NET runtime needed.
set -euo pipefail

cd "$(dirname "$0")/.."
VERSION="${TD_VERSION:-1.56.5}"

case "$(uname -s)-$(uname -m)" in
  Darwin-arm64) ASSET="MacOSArm64" ;;
  Darwin-x86_64) ASSET="MacOS-x64" ;;
  Linux-x86_64) ASSET="Linux-x64" ;;
  Linux-aarch64) ASSET="LinuxArm64" ;;
  *) echo "Unsupported platform: $(uname -s)-$(uname -m)"; exit 1 ;;
esac

URL="https://github.com/lay295/TwitchDownloader/releases/download/${VERSION}/TwitchDownloaderCLI-${VERSION}-${ASSET}.zip"

mkdir -p bin
echo "==> Downloading TwitchDownloaderCLI ${VERSION} (${ASSET})"
curl -fsSL -o bin/td.zip "$URL"
unzip -oq bin/td.zip -d bin
rm bin/td.zip
chmod +x bin/TwitchDownloaderCLI
# macOS quarantines downloaded binaries; clear it so it can run.
xattr -d com.apple.quarantine bin/TwitchDownloaderCLI 2>/dev/null || true

echo "==> $(./bin/TwitchDownloaderCLI --version 2>&1 | head -1)"
