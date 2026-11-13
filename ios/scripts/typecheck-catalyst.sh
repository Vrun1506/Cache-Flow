#!/bin/bash
# Typecheck App/ against the Mac Catalyst SDK that ships with the Command Line
# Tools. A proxy for `make build` on machines without Xcode: it catches Swift,
# SwiftUI, UIKit and PencilKit API errors, but it is not an iOS build.
set -euo pipefail
cd "$(dirname "$0")/.."
SDK=$(xcrun --show-sdk-path)
OUT="${1:-.build/catalyst-typecheck}"; mkdir -p "$OUT"
TGT=arm64-apple-ios17.0-macabi
FW=(-Fsystem "$SDK/System/iOSSupport/System/Library/Frameworks" -I "$SDK/System/iOSSupport/usr/lib/swift" -L "$SDK/System/iOSSupport/usr/lib/swift")
swiftc -emit-module -module-name CacheFlowKit -target $TGT -sdk "$SDK" "${FW[@]}" -swift-version 6 -parse-as-library \
  $(find Packages/CacheFlowKit/Sources -name '*.swift') -emit-module-path "$OUT/CacheFlowKit.swiftmodule"
rm -rf "$OUT/App" && mkdir -p "$OUT/App"
for f in App/*.swift; do python3 -c "import sys,re; s=open(sys.argv[1]).read(); print(re.sub(r'#Preview.*', '', s, flags=re.S))" "$f" > "$OUT/App/$(basename $f)"; done
swiftc -typecheck -target $TGT -sdk "$SDK" "${FW[@]}" -swift-version 6 -I "$OUT" "$OUT"/App/*.swift
echo TYPECHECK-OK
