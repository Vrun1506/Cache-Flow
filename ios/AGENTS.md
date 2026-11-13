# iPad app agent instructions

- Work from ios/. Run make test and make build before declaring iPad source changes complete.
- Put logic testable without UIKit in Packages/CacheFlowKit; keep App/ for SwiftUI/PencilKit glue.
- Change the generated Xcode project only through project.yml. Do not commit CacheFlow.xcodeproj or Config/Signing.local.xcconfig.
- Interface.md proposes v2; the current Events.swift and TutorAPI.swift still encode v1. Coordinate any wire change with the backend owner and update the mock and tests in the same change.
- Preserve student Pencil ink as its own layer. Render tutor writing programmatically in a separate layer with matching board revision; do not reuse physical arm events or any-input tutor phases for the new product.
- Apply the repository-wide guidance in ../AGENTS.md.
