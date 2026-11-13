#!/bin/bash
# Run the simulator UI end-to-end test (UITests/LessonE2ETests) against the
# in-process mock. Picks an available iPad simulator unless E2E_DEVICE (a
# simulator UDID or name) is set. Requires Xcode and an iOS simulator runtime.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! xcodebuild -version >/dev/null 2>&1; then
  echo "make e2e needs Xcode (xcodebuild). Install Xcode, then: sudo xcode-select -s /Applications/Xcode.app" >&2
  exit 2
fi

DEVICE="${E2E_DEVICE:-}"
if [ -z "$DEVICE" ]; then
  DEVICE=$(xcrun simctl list devices available -j | python3 -c '
import json, sys
devices = [d for runtime, ds in json.load(sys.stdin)["devices"].items() if "iOS" in runtime for d in ds]
ipads = [d for d in devices if d["name"].startswith("iPad")]
booted = [d for d in ipads if d["state"] == "Booted"]
pick = (booted or ipads or [None])[0]
print(pick["udid"] if pick else "")')
fi
if [ -z "$DEVICE" ]; then
  echo "No iPad simulator found. Install an iOS runtime: xcodebuild -downloadPlatform iOS" >&2
  exit 2
fi
case "$DEVICE" in
  *-*-*-*-*) DEST="id=$DEVICE" ;;
  *) DEST="platform=iOS Simulator,name=$DEVICE" ;;
esac

rm -rf build/e2e.xcresult
xcodebuild test -project CacheFlow.xcodeproj -scheme CacheFlow -destination "$DEST" \
  -only-testing:CacheFlowUITests -resultBundlePath build/e2e.xcresult \
  CODE_SIGNING_ALLOWED=NO
