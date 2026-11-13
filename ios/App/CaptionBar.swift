import CacheFlowKit
import SwiftUI

/// What the tutor just said, plus the controls that make sense for this phase.
struct CaptionBar: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        let s = client.session
        HStack(spacing: 16) {
            HStack(spacing: 10) {
                if s.speaking { Image(systemName: "waveform").symbolEffect(.variableColor.iterative).foregroundStyle(Color.accentColor) }
                Text(s.caption).font(.title3).lineLimit(2).minimumScaleFactor(0.8)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityLabel("Tutor says: \(s.caption)")

            Controls()
            TalkButton()
        }
        .padding(.horizontal, 24)
        .background(Theme.chrome)
        .overlay(alignment: .top) { Divider().overlay(Theme.hairline) }
    }
}

private struct Controls: View {
    @Environment(TutorClient.self) private var client
    @State private var answer = ""

    var body: some View {
        let s = client.session
        HStack(spacing: 12) {
            switch s.phase {
            case .modeSelect:
                Button("Teach me") { client.choose(.teach) }.buttonStyle(PillButtonStyle(prominent: true))
                Button("Let me try") { client.choose(.guide) }.buttonStyle(PillButtonStyle())
            case .working, .hinting:
                Button("I'm stuck") { client.stuck() }.buttonStyle(PillButtonStyle())
                Button("Check") { client.check() }.buttonStyle(PillButtonStyle(prominent: true))
            case .stepCheck, .followUp:
                if s.ask != nil, s.answerMode != .voice {
                    TextField("Type your answer", text: $answer)
                        .textFieldStyle(.roundedBorder).frame(width: 220)
                        .submitLabel(.send).onSubmit(send)
                    Button("Send", action: send).buttonStyle(PillButtonStyle(prominent: true))
                        .disabled(answer.trimmingCharacters(in: .whitespaces).isEmpty)
                }
            case .recap:
                Button("Spot the mistake") { client.choose(.check) }.buttonStyle(PillButtonStyle())
                Button("Let me try one") { client.choose(.guide) }.buttonStyle(PillButtonStyle(prominent: true))
            case .solved, .done:
                Button("New problem") { client.newProblem() }.buttonStyle(PillButtonStyle(prominent: true))
            case .spotMistake:
                Label("Circle the mistake", systemImage: "scribble").foregroundStyle(.secondary)
            default:
                EmptyView()
            }
            if s.mode != nil || s.stepTotal > 0 {
                Button { client.clearBoard() } label: { Image(systemName: "eraser") }
                    .buttonStyle(PillButtonStyle()).accessibilityLabel("Clear the tutor's writing")
            }
        }
    }

    private func send() {
        client.submitAnswer(answer)
        answer = ""
    }
}

/// Push-to-talk, half-duplex: disabled while the tutor is speaking.
private struct TalkButton: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        let s = client.session
        let disabled = s.speaking || s.connection != .connected
        Label(s.recording ? "Listening…" : "Hold to talk", systemImage: s.recording ? "mic.fill" : "mic")
            .font(.body.weight(.semibold))
            .padding(.horizontal, 20).padding(.vertical, 12)
            .foregroundStyle(s.recording ? .white : Color.accentColor)
            .background(s.recording ? Theme.bad : Color.accentColor.opacity(0.12), in: Capsule())
            .opacity(disabled ? 0.4 : 1)
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { _ in if !disabled { client.talkBegan() } }
                .onEnded { _ in client.talkEnded() })
            .accessibilityLabel("Hold to talk")
            .accessibilityAddTraits(.isButton)
    }
}
