import SwiftUI

@main
struct CacheFlowApp: App {
    @State private var client = TutorClient()

    var body: some Scene {
        WindowGroup {
            WhiteboardScreen()
                .environment(client)
                .statusBarHidden()
                .persistentSystemOverlays(.hidden)
                .preferredColorScheme(.light) // paper-white board, even in Dark Mode
        }
    }
}
