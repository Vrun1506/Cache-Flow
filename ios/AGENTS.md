# iPad app agent instructions

- Work from ios/. Run make test and make build before declaring iPad source changes complete.
- Put logic testable without UIKit in Packages/CacheFlowKit; keep App/ for SwiftUI/PencilKit glue.
- Change the generated Xcode project only through project.yml. Do not commit CacheFlow.xcodeproj or Config/Signing.local.xcconfig.
- The client implements v2 (Protocol/, Net/, Session/LessonEngine). Coordinate any wire change with the backend owner, and record open points in CONTRACT-QUESTIONS.md. Update the mock, the fixtures under Tests/CacheFlowKitTests/Fixtures/v2 and the tests in the same change.
- Run make test before declaring package changes complete. If Xcode is installed, also run make build and make e2e; without Xcode, run make typecheck and say that build/e2e were not run.
- Preserve student Pencil ink as its own layer. Render tutor writing programmatically in a separate layer with matching board revision; do not reuse physical arm events or any-input tutor phases for the new product.
- Apply the repository-wide guidance in ../AGENTS.md.
