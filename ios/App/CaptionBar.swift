import CacheFlowKit
import SwiftUI

/// What the tutor just said (always captioned, even when audio fails), the
/// student's actions, a typed reply box and push-to-talk.
struct CaptionBar: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        let m = client.model
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 10) {
                if m.speaking { Image(systemName: "waveform").symbolEffect(.variableColor.iterative).foregroundStyle(Color.accentColor) }
                Text(m.lesson.caption)
                    .font(.title3).lineLimit(2).minimumScaleFactor(0.7)
                    .accessibilityIdentifier("caption.text")
                    .accessibilityLabel(m.lesson.caption)
                Spacer(minLength: 8)
                AudioBadge()
                if let notice = m.lesson.notice {
                    Text(notice).font(.callout).foregroundStyle(.secondary).lineLimit(1)
                        .accessibilityIdentifier("notice.text")
                }
            }
            HStack(spacing: 10) {
                ActionButtons()
                Spacer(minLength: 8)
                ReplyField()
                TalkButton()
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(Theme.chrome)
        .overlay(alignment: .top) { Divider().overlay(Theme.hairline) }
    }
}

/// Shows when a clip was lost and the caption/on-device voice stood in.
private struct AudioBadge: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        if client.model.audioStatus == .fallback {
            Label("Audio unavailable, captions only", systemImage: "speaker.slash")
                .font(.caption.weight(.semibold)).foregroundStyle(Theme.warn)
                .padding(.horizontal, 10).padding(.vertical, 4)
                .background(Theme.warn.opacity(0.12), in: Capsule())
                .accessibilityIdentifier("audio.fallback")
        }
    }
}

private struct ActionButtons: View {
    @Environment(TutorClient.self) private var client

    private static let items: [(IntentAction, String, String, String)] = [
        (.tutor, "Tutor me", "graduationcap", "action.tutor"),
        (.check, "Check", "checkmark.seal", "action.check"),
        (.hint, "Hint", "lightbulb", "action.hint"),
        (.explainStep, "Explain", "text.bubble", "action.explain"),
        (.draw, "Draw it", "chart.xyaxis.line", "action.draw"),
        (.showAnswer, "Show the answer", "eye", "action.showAnswer"),
    ]

    var body: some View {
        let m = client.model
        let available = m.actions
        HStack(spacing: 8) {
            if available.contains(.cancel) {
                Button { client.cancelTurn() } label: { Label("Stop", systemImage: "stop.fill") }
                    .buttonStyle(PillButtonStyle(prominent: true, tint: Theme.bad))
                    .accessibilityIdentifier("action.cancel")
            }
            ForEach(Self.items, id: \.0) { action, title, icon, id in
                if available.contains(action) {
                    Button { client.request(action) } label: { Label(title, systemImage: icon).lineLimit(1) }
                        .buttonStyle(PillButtonStyle(prominent: action == .tutor || action == .check))
                        .accessibilityIdentifier(id)
                }
            }
            if m.lesson.phase == .complete {
                Button { client.loadSummary() } label: { Label("Summary", systemImage: "list.bullet.clipboard") }
                    .buttonStyle(PillButtonStyle(prominent: true)).accessibilityIdentifier("summary.button")
                Button("New problem") { client.newProblem() }.buttonStyle(PillButtonStyle())
            }
            if !m.lesson.ink.isEmpty || !m.lesson.scenes.isEmpty {
                Button { client.clearTutorLayer() } label: { Image(systemName: "eraser") }
                    .buttonStyle(PillButtonStyle()).accessibilityLabel("Clear the tutor's writing")
                    .accessibilityIdentifier("action.clearTutor")
            }
        }
        .environment(\.pillCompact, true)
    }
}

/// Typed reply: a question for the tutor, or the answer to the transfer item.
private struct ReplyField: View {
    @Environment(TutorClient.self) private var client
    @State private var text = ""

    var body: some View {
        let actions = client.model.actions
        let enabled = actions.contains(.ask) || actions.contains(.quizAnswer)
        HStack(spacing: 6) {
            TextField(client.model.lesson.phase == .independentCheck ? "Type your answer" : "Type a reply or question", text: $text)
                .textFieldStyle(.roundedBorder).frame(width: 220)
                .submitLabel(.send).onSubmit(send)
                .accessibilityIdentifier("reply.field")
            Button(action: send) { Image(systemName: "paperplane.fill") }
                .buttonStyle(PillButtonStyle(prominent: true))
                .disabled(text.trimmingCharacters(in: .whitespaces).isEmpty)
                .accessibilityLabel("Send reply")
                .accessibilityIdentifier("reply.send")
        }
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.5)
    }

    private func send() {
        client.sendReply(text)
        text = ""
    }
}

/// Push-to-talk, half-duplex: disabled while the tutor is speaking.
private struct TalkButton: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        let m = client.model
        let disabled = m.speaking || m.connection != .connected || !(m.actions.contains(.ask) || m.actions.contains(.quizAnswer))
        Label(m.recording ? "Listening…" : "Hold to talk", systemImage: m.recording ? "mic.fill" : "mic")
            .font(.body.weight(.semibold))
            .padding(.horizontal, 16).padding(.vertical, 10)
            .foregroundStyle(m.recording ? .white : Color.accentColor)
            .background(m.recording ? Theme.bad : Color.accentColor.opacity(0.12), in: Capsule())
            .opacity(disabled ? 0.4 : 1)
            .gesture(DragGesture(minimumDistance: 0)
                .onChanged { _ in if !disabled { client.talkBegan() } }
                .onEnded { _ in client.talkEnded() })
            .accessibilityLabel("Hold to talk")
            .accessibilityAddTraits(.isButton)
            .accessibilityIdentifier("talk.button")
    }
}
