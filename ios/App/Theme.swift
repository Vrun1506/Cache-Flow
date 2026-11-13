import CacheFlowKit
import SwiftUI

/// Shared look. Ink colours come from `InkColor` in CacheFlowKit so the pen,
/// the segmenter and the UI can never disagree about who owns a stroke.
enum Theme {
    static let paper = Color.white
    static let chrome = Color(white: 0.965)
    static let hairline = Color.black.opacity(0.08)
    static let stripTint = Color(red: 0.94, green: 0.96, blue: 1.0)
    static let guide = Color(red: 0.05, green: 0.35, blue: 0.9).opacity(0.28)
    static let tutorInk = Color(ink: .tutor)
    static let studentInk = Color(ink: .student)
    static let ok = Color(red: 0.13, green: 0.62, blue: 0.35)
    static let warn = Color(red: 0.86, green: 0.55, blue: 0.1)
    static let bad = Color(red: 0.84, green: 0.2, blue: 0.2)
}

extension Color {
    init(ink: InkColor) { self.init(red: ink.red, green: ink.green, blue: ink.blue) }
}

extension Phase {
    var title: String {
        switch self {
        case .idle: "Ready"
        case .pose: "Posing a problem"
        case .confirm: "Confirm problem"
        case .modeSelect: "Choose how to work"
        case .stepIntro: "Introducing step"
        case .stepWrite: "Writing"
        case .stepCheck: "Check question"
        case .followUp: "Follow-up"
        case .recap: "Recap"
        case .working: "Your turn"
        case .hinting: "Hint"
        case .armStep: "Showing a step"
        case .solved: "Solved"
        case .spotMistake: "Spot the mistake"
        case .final: "Final"
        case .done: "Done"
        }
    }
}

extension Mode {
    var title: String {
        switch self {
        case .teach: "Teach"
        case .guide: "Guide"
        case .check: "Check"
        }
    }
}

extension ConnectionState {
    var color: Color {
        switch self {
        case .connected: Theme.ok
        case .connecting, .reconnecting: Theme.warn
        case .offline: Theme.bad
        }
    }
    var label: String {
        switch self {
        case .connected: "Connected"
        case .connecting: "Connecting"
        case .reconnecting: "Reconnecting"
        case .offline: "Offline"
        }
    }
}

/// Pill buttons used across the caption bar and sheets.
struct PillButtonStyle: ButtonStyle {
    var prominent = false
    @Environment(\.isEnabled) private var enabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.semibold))
            .padding(.horizontal, 20).padding(.vertical, 12)
            .foregroundStyle(prominent ? Color.white : Color.accentColor)
            .background(prominent ? Color.accentColor : Color.accentColor.opacity(0.12), in: Capsule())
            .opacity(enabled ? (configuration.isPressed ? 0.7 : 1) : 0.4)
    }
}
