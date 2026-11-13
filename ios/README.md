# Socrates iPad app (`ios/`)

SwiftUI + PencilKit whiteboard that the SO-101 arm writes onto. It replaces the earlier Next.js frontend. Spec: [01-plan.md](../01-plan.md), section 7.

## Setup

Requires Xcode 16+ (built and tested with 27.2) and Homebrew.

```sh
cd ios
make bootstrap   # installs XcodeGen, creates Config/Signing.local.xcconfig, generates CacheFlow.xcodeproj
```

1. Edit `Config/Signing.local.xcconfig`: set your `DEVELOPMENT_TEAM` (a free Apple ID personal team is fine) and a bundle ID unique to you. This file is git-ignored.
2. iPad: turn on Developer Mode, connect by cable, then `make open` and Run. Free profiles expire after 7 days.
3. No iPad? Run the scheme on an iPad simulator (the mouse draws there), or `make build`.

`CacheFlow.xcodeproj` is generated from `project.yml` and is **not committed**. Never edit it; change `project.yml` and run `make generate`. This avoids `.pbxproj` merge conflicts between people working in parallel.

## Layout

| Path | What |
| --- | --- |
| `project.yml` | The Xcode project definition (iPad only, landscape only, iOS 17+) |
| `Config/` | Shared and per-laptop build settings |
| `App/` | SwiftUI app: screen, canvas host, `SessionModel` |
| `Packages/CacheFlowKit/` | Pure-Swift logic, tested with `make test` (no simulator) |

### CacheFlowKit: the shared source of truth

| Type | Purpose |
| --- | --- |
| `BoardLayout` | All screen geometry, as a pure function of screen size. Board-space regions and the five tutor slots. Calibration stores its `fingerprint`. |
| `InkColor` / `InkOwner` | Student ink is black, tutor ink is blue. One definition shared by the pen, the segmenter and the UI. |
| `CanvasPolicy` | Per-phase rule: `.pencilOnly` + student ink, or `.anyInput` + tutor ink while the arm writes. |
| `LineSegmenter` | Splits student strokes into lines for Guide-mode checking. |
| `ServerEvent` / `ClientEvent` | The WebSocket contract from 01-plan.md section 6. Unknown event types decode to `.unknown`, not an error. |

## Rules that keep the arm calibrated

- One `PKCanvasView` spans the whole board (student area plus tutor strip), so student ink, arm ink and calibration touches share one coordinate space. Don't add scrolling, zoom, rotation or padding to it.
- Lay things out only through `BoardLayout`. Don't add ad-hoc offsets.
- Landscape only, full screen. Changing the device or its screen size invalidates calibration.

## Not built yet

Networking and the mock server, audio queue and push-to-talk, question box, ghost ink, highlight and read-back overlays, calibration mode, settings/health sheet. Build against the contract in `Events.swift`.
