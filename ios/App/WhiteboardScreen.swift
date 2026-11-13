import CacheFlowKit
import SwiftUI

/// The single, landscape-locked screen: header, board (student canvas plus the
/// tutor's five writing slots), and a caption bar. All geometry comes from
/// `BoardLayout`, never from ad-hoc spacing.
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
            }
            .task(id: layout.fingerprint) { client.start(layout: layout) }
        }
        .ignoresSafeArea()
        .sheet(isPresented: $showSettings) { SettingsSheet() }
        .sheet(isPresented: Binding(get: { client.session.needsConfirm }, set: { _ in })) { ConfirmSheet() }
    }

    private func place<Content: View>(_ rect: CGRect, @ViewBuilder _ content: () -> Content) -> some View {
        content()
            .frame(width: rect.width, height: rect.height)
            .position(x: rect.midX, y: rect.midY)
    }
}

/// The board in board space: paper, tutor slots, ink, overlays, question box.
private struct BoardView: View {
    @Environment(TutorClient.self) private var client
    let layout: BoardLayout

    var body: some View {
        let session = client.session
        ZStack(alignment: .topLeading) {
            Theme.paper
            Rectangle().fill(Theme.stripTint)
                .frame(width: layout.tutorStrip.width, height: layout.tutorStrip.height)
                .offset(x: layout.tutorStrip.minX, y: layout.tutorStrip.minY)
            Text("Your working")
                .font(.caption.weight(.medium)).foregroundStyle(.tertiary)
                .offset(x: layout.workArea.minX + 16, y: layout.workArea.minY + 4)
            ForEach(Array(layout.slots.enumerated()), id: \.offset) { i, slot in
                SlotGuide(number: i + 1, active: session.stepIndex == i + 1)
                    .frame(width: slot.width, height: slot.height)
                    .offset(x: slot.minX, y: slot.minY)
            }
            // Ink goes above the guides, over the full board.
            PencilCanvas(controller: client.canvas, input: session.penInput, ink: session.inkOwner)
            GhostLayer(ghosts: session.ghosts)
            LineOverlays(layout: layout)
            QuestionBox()
                .frame(width: layout.questionBox.width, height: layout.questionBox.height)
                .offset(x: layout.questionBox.minX, y: layout.questionBox.minY)
            if session.calibration != nil { CalibrationOverlay(layout: layout) }
        }
        .clipped()
        .overlay(alignment: .top) { Divider().overlay(Theme.hairline) }
    }
}

private struct SlotGuide: View {
    let number: Int
    let active: Bool

    var body: some View {
        RoundedRectangle(cornerRadius: 10)
            .strokeBorder(active ? Theme.tutorInk.opacity(0.55) : Theme.guide,
                          style: StrokeStyle(lineWidth: active ? 1.5 : 1, dash: active ? [] : [6, 4]))
            .overlay(alignment: .topLeading) {
                Text("\(number)").font(.caption.monospacedDigit().weight(.semibold))
                    .foregroundStyle(Theme.tutorInk.opacity(active ? 0.8 : 0.45))
                    .padding(8)
            }
            .allowsHitTesting(false)
    }
}

#Preview(traits: .landscapeLeft) {
    WhiteboardScreen().environment(TutorClient())
}
