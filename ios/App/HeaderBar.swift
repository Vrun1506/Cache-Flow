import CacheFlowKit
import SwiftUI

struct HeaderBar: View {
    @Environment(TutorClient.self) private var client
    @Binding var showSettings: Bool

    var body: some View {
        let session = client.session
        HStack(spacing: 20) {
            HStack(spacing: 8) {
                Text("Solve:").foregroundStyle(.secondary)
                Text(session.problem ?? "—").fontWeight(.semibold)
            }
            .font(.title3)
            .lineLimit(1)
            // Hidden entry to calibration mode (01-plan.md, section 7).
            .onLongPressGesture(minimumDuration: 1.5) { client.toggleCalibration() }

            PhaseChip()
            if session.stepTotal > 0 { StepDots(index: session.stepIndex, total: session.stepTotal) }

            Spacer(minLength: 12)
            SkillBars(skills: session.skills)

            Button { showSettings = true } label: {
                HStack(spacing: 8) {
                    Circle().fill(session.connection.color).frame(width: 10, height: 10)
                    Image(systemName: "gearshape")
                }
                .padding(10)
            }
            .accessibilityLabel("Settings, \(session.connection.label)")
        }
        .padding(.horizontal, 24)
        .background(Theme.chrome)
    }
}

private struct PhaseChip: View {
    @Environment(TutorClient.self) private var client

    var body: some View {
        let s = client.session
        HStack(spacing: 6) {
            if let mode = s.mode { Text(mode.title).fontWeight(.semibold) ; Text("·").foregroundStyle(.tertiary) }
            Text(s.phase.title)
        }
        .font(.subheadline)
        .padding(.horizontal, 12).padding(.vertical, 6)
        .background(Color.accentColor.opacity(0.12), in: Capsule())
        .foregroundStyle(Color.accentColor)
    }
}

private struct StepDots: View {
    let index: Int
    let total: Int

    var body: some View {
        HStack(spacing: 8) {
            Text("Step \(index) of \(total)").font(.subheadline).foregroundStyle(.secondary)
            HStack(spacing: 4) {
                ForEach(1...max(total, 1), id: \.self) { i in
                    Capsule().fill(i <= index ? Theme.tutorInk : Theme.guide).frame(width: 14, height: 5)
                }
            }
        }
    }
}

/// Skill levels are integers 0...5 (see Interface.md).
private struct SkillBars: View {
    let skills: [String: Int]
    private static let names = ["factor_pair": "Factor pairs", "signs": "Signs"]

    var body: some View {
        HStack(spacing: 16) {
            ForEach(skills.keys.sorted(), id: \.self) { key in
                VStack(alignment: .leading, spacing: 3) {
                    Text(Self.names[key] ?? key.replacingOccurrences(of: "_", with: " ").capitalized)
                        .font(.caption2).foregroundStyle(.secondary)
                    HStack(spacing: 2) {
                        ForEach(0..<5, id: \.self) { i in
                            RoundedRectangle(cornerRadius: 1.5)
                                .fill(i < (skills[key] ?? 0) ? Theme.ok : Theme.hairline)
                                .frame(width: 12, height: 6)
                        }
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityValue("\(skills[key] ?? 0) of 5")
            }
        }
    }
}
