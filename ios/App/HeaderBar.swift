import CacheFlowKit
import SwiftUI

struct HeaderBar: View {
    @Environment(TutorClient.self) private var client
    @Binding var showSettings: Bool

    var body: some View {
        let m = client.model
        HStack(spacing: 18) {
            HStack(spacing: 8) {
                Text(m.lesson.problem?.item == "independent_check" ? "On your own:" : "Solve:").foregroundStyle(.secondary)
                Text(m.lesson.problem?.text ?? "—").fontWeight(.semibold)
                    .accessibilityIdentifier("problem.text")
            }
            .font(.title3)
            .lineLimit(1)

            PhaseChip()
            if m.lesson.assistedThisItem {
                Label("Helped", systemImage: "hand.raised")
                    .font(.caption.weight(.semibold)).foregroundStyle(Theme.warn)
                    .accessibilityIdentifier("assisted.badge")
            }
            if let e = m.lesson.latestEvidence {
                Label(e.summary, systemImage: e.icon)
                    .font(.subheadline.weight(.semibold)).foregroundStyle(e.tint)
                    .accessibilityIdentifier("evidence.latest")
                    .accessibilityLabel(e.summary)
            }

            Spacer(minLength: 12)
            SkillBars(skills: m.lesson.skills)

            Button { showSettings = true } label: {
                HStack(spacing: 8) {
                    Circle().fill(m.connection.color).frame(width: 10, height: 10)
                    Image(systemName: "gearshape")
                }
                .padding(10)
            }
            .accessibilityLabel("Settings, \(m.connection.label)")
            .accessibilityIdentifier("settings.button")
        }
        .padding(.horizontal, 24)
        .background(Theme.chrome)
    }
}

private struct PhaseChip: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        Text(client.model.lesson.phase.title)
            .font(.subheadline)
            .padding(.horizontal, 12).padding(.vertical, 6)
            .background(Color.accentColor.opacity(0.12), in: Capsule())
            .foregroundStyle(Color.accentColor)
            .accessibilityIdentifier("phase.chip")
            .accessibilityLabel(client.model.lesson.phase.title)
    }
}

/// Evidence-backed levels 0...5; a hand marks skills that had help.
private struct SkillBars: View {
    let skills: [SkillLevel]

    var body: some View {
        HStack(spacing: 16) {
            ForEach(skills, id: \.skill) { s in
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 3) {
                        Text(s.label).font(.caption2).foregroundStyle(.secondary)
                        if s.assisted { Image(systemName: "hand.raised.fill").font(.caption2).foregroundStyle(Theme.warn) }
                    }
                    HStack(spacing: 2) {
                        ForEach(0..<5, id: \.self) { i in
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(i < s.level ? Theme.ok : Theme.hairline)
                                .frame(width: 12, height: 6)
                        }
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityLabel("\(s.label): \(s.level) of 5, \(s.independentEvidence) independent\(s.assisted ? ", helped" : "")")
            }
        }
    }
}
