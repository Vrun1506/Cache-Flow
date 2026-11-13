# Socrates iPad app

SwiftUI + PencilKit whiteboard for the local software tutor. The committed app is a robot-era scaffold that can run a mock lesson; it is the implementation starting point, not the completed v2 product. Read [the proposed interface](../Interface.md) and [status](../status.md) before changing its wire types.

## Setup

Requires Xcode 16+ and Homebrew. From ios/ run make bootstrap, then edit Config/Signing.local.xcconfig with a DEVELOPMENT_TEAM and a unique bundle ID. The file and generated CacheFlow.xcodeproj are ignored by Git. Run make open to launch Xcode, make test for the pure Swift package and make build for the iPad simulator. On device, enable Developer Mode and select the device in Xcode. The debug Mock profile runs without a Spark.

## Layout and code

| Path | Current responsibility |
| --- | --- |
| App/ | SwiftUI screen, PencilKit canvas, session model, TutorClient and local audio playback/recording. |
| Packages/CacheFlowKit/ | Board geometry, ink ownership, line segmentation, v1 event/request types and an in-process MockServer. |
| project.yml | XcodeGen project definition; edit this instead of a generated project. |

The current app has a fixed student area and five-slot tutor strip, line PNG upload, push-to-talk WAV, WebSocket events, captions, audio fallback and a mock algebra flow. Its Step/ArmJob events, calibration UI, any-input pen phases, force-arm operator controls and ghost fallback reflect the retired hardware plan. The mock's scribble strokes are not verified tutor writing or a real NVIDIA model result.

## v2 migration

Keep PencilKit student ink and the useful question/audio/network plumbing. Make tutor annotations a separate digital layer driven by revisioned preview/commit events. Preserve student undo, Pencil-only capture and local captions. Remove arm/calibration semantics and update Events.swift, TutorAPI.swift, MockServer.swift, tests and [Interface.md](../Interface.md) together. Do not claim that a v1 mock replay proves the v2 tutor or Spark services.
