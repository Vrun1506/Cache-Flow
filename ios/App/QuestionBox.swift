import CacheFlowKit
import SwiftUI

/// Where the student writes (or types) the problem, with presets that always work.
struct QuestionBox: View {
    @Environment(TutorClient.self) private var client
    @State private var hasInk = false
    @State private var typed = ""

    var body: some View {
        let enabled = client.model.acceptsQuestion && client.model.connection == .connected
        HStack(spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 12).fill(Theme.paper)
                RoundedRectangle(cornerRadius: 12).strokeBorder(Theme.hairline, lineWidth: 1.5)
                if !hasInk {
                    Text("Write your problem here")
                        .font(.title3).foregroundStyle(.tertiary).allowsHitTesting(false)
                }
                PencilCanvas(controller: client.questionCanvas)
                    .clipShape(RoundedRectangle(cornerRadius: 12))
                    .allowsHitTesting(enabled)
                    .accessibilityIdentifier("question.canvas")
            }
            VStack(spacing: 8) {
                HStack(spacing: 8) {
                    Button("Read it") { client.readQuestion() }
                        .buttonStyle(PillButtonStyle(prominent: true))
                        .disabled(!hasInk)
                        .accessibilityIdentifier("question.read")
                    Menu {
                        ForEach(Preset.all) { p in Button(p.display) { client.pickPreset(p) } }
                    } label: { Label("Presets", systemImage: "list.bullet").lineLimit(1).fixedSize() }
                    .buttonStyle(PillButtonStyle())
                    .accessibilityIdentifier("question.presets")
                    Button { client.questionCanvas.clearAll() } label: { Image(systemName: "trash") }
                        .buttonStyle(PillButtonStyle()).disabled(!hasInk)
                        .accessibilityLabel("Clear the question")
                }
                HStack(spacing: 6) {
                    TextField("…or type it", text: $typed)
                        .textFieldStyle(.roundedBorder)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .onSubmit(sendTyped)
                        .accessibilityIdentifier("question.typeField")
                    Button(action: sendTyped) { Image(systemName: "arrow.right.circle.fill") }
                        .disabled(typed.trimmingCharacters(in: .whitespaces).isEmpty)
                        .accessibilityLabel("Use typed problem")
                        .accessibilityIdentifier("question.typeSend")
                }
            }
            .disabled(!enabled)
            .frame(width: 280)
        }
        .opacity(enabled ? 1 : 0.55)
        .onAppear {
            let previous = client.questionCanvas.onDrawingChanged
            client.questionCanvas.onDrawingChanged = {
                previous?()
                hasInk = !client.questionCanvas.isEmpty
            }
        }
    }

    private func sendTyped() {
        client.typeQuestion(typed)
        typed = ""
    }
}
