# Run from ios/. `make help` lists targets.
SIM ?= generic/platform=iOS Simulator

# With only the Command Line Tools selected (no Xcode.app), SwiftPM cannot
# find the Swift Testing framework by itself; point it at the CLT copy.
CLT_DEV := /Library/Developer/CommandLineTools/Library/Developer
ifneq ($(findstring CommandLineTools,$(shell xcode-select -p 2>/dev/null)),)
SWIFT_TEST_FLAGS := -Xswiftc -F$(CLT_DEV)/Frameworks -Xlinker -F$(CLT_DEV)/Frameworks \
  -Xlinker -rpath -Xlinker $(CLT_DEV)/Frameworks -Xlinker -rpath -Xlinker $(CLT_DEV)/usr/lib
endif

.PHONY: help bootstrap generate open test build e2e typecheck clean
help:
	@echo "bootstrap  install XcodeGen (Homebrew) and generate CacheFlow.xcodeproj"
	@echo "generate   regenerate the Xcode project from project.yml"
	@echo "open       generate and open in Xcode"
	@echo "test       run CacheFlowKit unit, contract-fixture and headless mock-lesson tests (no simulator)"
	@echo "build      build the app for the iOS Simulator"
	@echo "e2e        run the simulator UI end-to-end lesson test (CacheFlowUITests) against the mock"
	@echo "typecheck  typecheck App/ against the Mac Catalyst SDK (no Xcode needed; not a real iOS build)"

bootstrap:
	@command -v xcodegen >/dev/null || brew install xcodegen
	@[ -f Config/Signing.local.xcconfig ] || { cp Config/Signing.example.xcconfig Config/Signing.local.xcconfig; \
	  echo "Created Config/Signing.local.xcconfig - set your DEVELOPMENT_TEAM and bundle ID before running on the iPad."; }
	xcodegen generate

generate:
	xcodegen generate

open: generate
	open CacheFlow.xcodeproj

test:
	cd Packages/CacheFlowKit && swift test $(SWIFT_TEST_FLAGS)

build: generate
	xcodebuild -project CacheFlow.xcodeproj -scheme CacheFlow -destination '$(SIM)' \
	  CODE_SIGNING_ALLOWED=NO build

e2e: generate
	scripts/e2e.sh

typecheck:
	scripts/typecheck-catalyst.sh

clean:
	rm -rf CacheFlow.xcodeproj build .build Packages/CacheFlowKit/.build
