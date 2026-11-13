# iPad app agent instructions

- Work from `ios/`. `make test` runs the CacheFlowKit unit tests; `make build` builds for the simulator. Run both before declaring work done.
- Put anything testable without UIKit (geometry, protocol, algorithms) in `Packages/CacheFlowKit` with tests. `App/` holds only SwiftUI and PencilKit glue.
- Change the Xcode project only through `project.yml`. Never commit `*.xcodeproj` or `Config/Signing.local.xcconfig`.
- The WebSocket types in `Events.swift` mirror `01-plan.md` section 6. Change both together and tell the backend owner on the agent board.
- Don't move or resize the board canvas; see "Rules that keep the arm calibrated" in `README.md`.
- Apply the repository-wide guidance in `../AGENTS.md`.
