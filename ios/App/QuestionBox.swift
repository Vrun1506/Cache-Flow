import CacheFlowKit
import SwiftUI

/// Where the student writes the problem, with a preset picker that always works.
struct QuestionBox: View {
    @Environment(TutorClient.self) private var client
    @State private var hasInk = false

    var body: some View {
        let enabled = client.session.acceptsQuestion
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 12).fill(Theme.paper)
                RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.hairline, lineWidth: 1.5)
                if !hasInk {
                    Text("Write your problem here")
                        .font(.title3).foregroundStyle(.tertiary).allowsHitTesting(false)
                }
                PencilCanvas(controller: client.questionCanvas, input: .pencilOnly, ink: .student)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .allowsHitTesting(enabled)
            }
            VStack(spacing: 10) {
                Button("Read it") { client.readQuestion() }
                    .buttonStyle(PillButtonStyle(prominent: true))
                    .disabled(!hasInk || !enabled)
                HStack(spacing: 8) {
                    Menu {
                        ForEach(Preset.all) { p in Button(p.display) { client.pickPreset(p) } }
                    } label: { Label("Presets", systemImage: "list.bullet").lineLimit(1).fixedSize() }
                    .buttonStyle(PillButtonStyle())
                    Button { client.questionCanvas.clearAll() } label: { Image(systemName: "trash") }
                        .buttonStyle(PillButtonStyle()).disabled(!hasInk)
                        .accessibilityLabel("Clear the question")
                }
                .disabled(!enabled)
            }
            .frame(width: 210)
        }
        .opacity(enabled ? 1 : 0.55)
        .onAppear { client.questionCanvas.onDrawingChanged = { hasInk = !client.questionCanvas.isEmpty } }
    }
}
