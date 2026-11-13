import CacheFlowKit
import SwiftUI

/// The single, landscape-locked screen: header, board (student canvas plus the
/// separate tutor layer and writing strip), and a caption/action bar. All
/// geometry comes from `BoardLayout`, never from ad-hoc spacing.
struct WhiteboardScreen: View {
    @Environment(TutorClient.self) private var client
    @State private var showSettings = false

    var body: some View {
        GeometryReader { proxy in
            let layout = BoardLayout(screenSize: proxy.size)
            ZStack(alignment: .topLeading) {
                Theme.chrome
                place(layout.header) { HeaderBar(showSettings: $showSettings) }
                place(layout.board) { BoardView(layout: layout) }
                place(layout.captionBar) { CaptionBar() }
                if client.config.uiTest {
                    DebugPanel()
                        .frame(width: layout.tutorStrip.width - 24, alignment: .trailing)
                        .position(x: layout.board.maxX - layout.tutorStrip.width / 2, y: layout.board.maxY - 34)
                }
            }
            .task(id: layout.fingerprint) { client.start(layout: layout) }
        }
        .ignoresSafeArea()
        .sheet(isPresented: $showSettings) { SettingsSheet() }
        .sheet(item: Binding(get: { client.model.lesson.pendingReadBack.map(PendingReadBack.init) }, set: { _ in })) {
            ReadBackSheet(readBack: $0.readBack)
        }
        .sheet(isPresented: Binding(get: { client.model.showSummary }, set: { client.model.showSummary = $0 })) { SummarySheet() }
    }

    private func place<Content: View>(_ rect: CGRect, @ViewBuilder _ content: () -> Content) -> some View {
        content()
            .frame(width: rect.width, height: rect.height)
            .position(x: rect.midX, y: rect.midY)
    }
}

private struct PendingReadBack: Identifiable {
    let readBack: ReadBack
    var id: String { readBack.requestID }
}

/// The board in board space: paper, the tutor's writing strip, the student's
/// Pencil canvas, then the tutor layer above it (which ignores touches).
private struct BoardView: View {
    @Environment(TutorClient.self) private var client
    let layout: BoardLayout

    var body: some View {
        ZStack(alignment: .topLeading) {
            Theme.paper
            Rectangle().fill(Theme.stripTint)
                .frame(width: layout.tutorStrip.width, height: layout.tutorStrip.height)
                .offset(x: layout.tutorStrip.minX, y: layout.tutorStrip.minY)
            Text("Your working")
                .font(.caption.weight(.medium)).foregroundStyle(.tertiary)
                .offset(x: layout.workArea.minX + 16, y: layout.workArea.minY + 4)
            Text("Tutor")
                .font(.caption.weight(.medium)).foregroundStyle(Theme.tutorInk.opacity(0.5))
                .offset(x: layout.tutorStrip.minX + 16, y: layout.tutorStrip.minY + 2)
            PencilCanvas(controller: client.canvas)
                .accessibilityIdentifier("board.canvas")
            TutorLayer()
            QuestionBox()
                .frame(width: layout.questionBox.width, height: layout.questionBox.height)
                .offset(x: layout.questionBox.minX, y: layout.questionBox.minY)
        }
        .clipped()
        .overlay(alignment: .top) { Divider().overlay(Theme.hairline) }
    }
}

#Preview(traits: .landscapeLeft) {
    WhiteboardScreen().environment(TutorClient())
}
