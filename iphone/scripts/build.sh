#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
task_root="$PWD"
if [[ -x "$task_root/.tools/swift/usr/bin/swift" ]]; then
    export PATH="$task_root/.tools/swift/usr/bin:$PATH"
fi
if [[ -x "$task_root/.tools/squashfs-root/AppRun" ]]; then
    xtool_bin="$task_root/.tools/squashfs-root/AppRun"
elif command -v xtool >/dev/null 2>&1; then
    xtool_bin="$(command -v xtool)"
else
    xtool_bin=""
fi
action="${1:-check}"
if ! command -v swift >/dev/null 2>&1; then
    echo "Swift is missing. Follow SETUP-WINDOWS.md to install Swift inside WSL." >&2
    exit 2
fi
if [[ "$action" == "test" ]]; then
    unset ROCKFORM_IOS
    mkdir -p test-output
    swift test 2>&1 | tee test-output/swift-tests.txt
    swift run CaptureCheck test-output 2>&1 | tee test-output/swift-export.txt
    # Parses iOS source syntax; Apple framework type checking requires its SDK.
    swiftc -frontend -parse Sources/RockformCapture/*.swift
    echo "iOS Swift syntax parsed. Apple SDK type checking and device tests remain pending."
    exit 0
fi
if [[ -z "$xtool_bin" ]]; then
    echo "xtool is missing. Follow SETUP-WINDOWS.md." >&2
    exit 2
fi
if [[ "$action" == "setup" ]]; then
    "$xtool_bin" setup
    exit 0
fi
if [[ "$action" == "devices" ]]; then
    "$xtool_bin" devices
    exit 0
fi
echo "Swift: $(swift --version | head -n 1)"
echo "xtool: $("$xtool_bin" --version)"
if ! swift sdk list | grep -q 'darwin'; then
    echo "Apple iOS SDK is missing. Download the matching Xcode .xip from Apple, then run this script with 'setup'." >&2
    echo "No IPA was produced. See SETUP-WINDOWS.md." >&2
    exit 3
fi
export ROCKFORM_IOS=1
case "$action" in
    check) echo "Build tools and Darwin SDK found. Run 'package' or 'install' next." ;;
    package) "$xtool_bin" dev build --configuration release --ipa ;;
    signed-package) "$xtool_bin" dev build --configuration release --ipa --sign ;;
    install) "$xtool_bin" dev run --configuration release ;;
    *) echo "Usage: build.sh check|setup|test|devices|package|signed-package|install" >&2; exit 2 ;;
esac
