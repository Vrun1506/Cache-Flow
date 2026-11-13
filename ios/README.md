# Socrates iPad app

SwiftUI + PencilKit whiteboard for the local software tutor. It speaks the **v2** iPad↔Spark contract ([Interface.md](../Interface.md)). The decisions the iOS side made where Interface.md was silent are listed in [CONTRACT-QUESTIONS.md](CONTRACT-QUESTIONS.md) for the backend owner to confirm. The built-in **Mock** profile runs the whole demo with no Spark. The mock proves contract handling only; it is not evidence of any real model, agent, verifier or speech service.

## Setup

Requires Xcode 16+ and Homebrew. From ios/ run `make bootstrap`, then edit Config/Signing.local.xcconfig with a DEVELOPMENT_TEAM and a unique bundle ID. That file and the generated CacheFlow.xcodeproj are ignored by Git.

| Command | What it does | Needs |
| --- | --- | --- |
| `make test` | CacheFlowKit unit tests, v2 contract-fixture tests and a headless full-lesson run (MockServer + LessonEngine, including lost audio, stale ink, cancellation and reconnect) | Swift toolchain (the Command Line Tools are enough) |
| `make build` | Builds the app for the iPad Simulator | Xcode, XcodeGen |
| `make e2e` | Simulator UI test `UITests/LessonE2ETests` running the full lesson through the real UI with `-uitest` (mock, scripted audio/mic, fault panel). Set `E2E_DEVICE` to choose a simulator. Results go to `build/e2e.xcresult` | Xcode, XcodeGen, an iOS simulator runtime |
| `make typecheck` | Typechecks App/ against the Mac Catalyst SDK | Command Line Tools only; a proxy, not an iOS build |

On a device, enable Developer Mode and select the device in Xcode. Debug builds default to the Mock profile.

## Layout and code

| Path | Responsibility |
| --- | --- |
| App/TutorClient.swift | Composition root: picks Mock or HTTP/WSS, turns taps into revisioned, idempotent requests, performs `LessonEngine` effects, owns the speech queue. |
| App/TutorLayer.swift | The tutor's own layer: committed and ghost-preview ink, approved scene primitives, highlights, read-back/evidence labels. It ignores touches. |
| App/BoardCanvasController.swift, PencilCanvas.swift | Student-only, Pencil-only `PKCanvasView`s (question box and working area). Tutor ink is never inserted here. |
| App/CaptionBar.swift, HeaderBar.swift, Sheets.swift, QuestionBox.swift | Captions and audio-fallback badge; Tutor me, Check, Hint, Explain, Draw it, Show the answer and Stop; typed reply; push-to-talk; read-back confirmation; summary; settings with the agent trace; the `-uitest` debug panel. |
| App/AppEnvironment.swift, TutorAudio.swift | Launch configuration, audio/recorder protocols, real AVFoundation implementations and deterministic UI-test fakes. |
| Packages/CacheFlowKit/Protocol | v2 envelope, payloads and client messages. |
| Packages/CacheFlowKit/Net | v2 HTTP request builders/client and the cursor-resuming WSS stream. |
| Packages/CacheFlowKit/Session | `LessonEngine`, a pure reducer handling sequence de-duplication, stale-revision rejection, preview→commit, cancellation and supersession, replay without speech, and bounds. Also `SpeechQueue` (pen-down hold) and `ActionPolicy`. |
| Packages/CacheFlowKit/Mock | `MockServer`: the scripted v2 lesson, a JSON wire round-trip, and fault injection (`loseNextAudio`, `staleNextInk`, `dropConnection()`). |
| Packages/CacheFlowKit/Tests/…/Fixtures/v2 | Deterministic contract fixtures: one per event type, client messages, HTTP bodies, malformed cases and Interface.md scenarios. |
| UITests/ | The simulator end-to-end lesson test. |
| project.yml | XcodeGen project (app + CacheFlowUITests). Edit this, never the generated project. |

## Rules the client enforces

Student and tutor ink are separate layers. Clearing the tutor layer never touches student ink or undo history. Every request carries `request_id` and `board_revision`. Tutor ink and scenes commit only after a placed preview and an exact revision match. Pen movement invalidates pending previews, and speech never starts while the Pencil is down. Cancelling or superseding a turn stops its audio and drops its ink and scenes. Envelopes are applied once, in `seq` order. Replayed events never play speech. Every tutor turn is captioned, and a lost clip falls back to on-device speech of the caption with a visible badge. Ambiguous handwriting and transcripts are never graded or used until the student confirms them.
