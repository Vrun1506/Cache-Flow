# Run from ios/. `make help` lists targets.
SIM ?= generic/platform=iOS Simulator

.PHONY: help bootstrap generate open test build clean
help:
	@echo "bootstrap  install XcodeGen (Homebrew) and generate CacheFlow.xcodeproj"
	@echo "generate   regenerate the Xcode project from project.yml"
	@echo "open       generate and open in Xcode"
	@echo "test       run CacheFlowKit unit tests (no simulator needed)"
	@echo "build      build the app for the iOS Simulator"

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
	cd Packages/CacheFlowKit && swift test

build: generate
	xcodebuild -project CacheFlow.xcodeproj -scheme CacheFlow -destination '$(SIM)' \
	  CODE_SIGNING_ALLOWED=NO build

clean:
	rm -rf CacheFlow.xcodeproj build .build Packages/CacheFlowKit/.build
