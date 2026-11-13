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

extension LessonPhase {
    var title: String {
        switch self {
        case .idle: "Ready for a problem"
        case .reading: "Reading"
        case .confirm: "Confirm reading"
        case .ready: "Ready"
        case .tutoring: "Tutor's turn"
        case .studentTurn: "Your turn"
        case .independentCheck: "On your own"
        case .complete: "Complete"
        default: rawValue.replacingOccurrences(of: "_", with: " ").capitalized
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

private struct PillCompactKey: EnvironmentKey { static let defaultValue = false }

extension EnvironmentValues {
    /// Tighter pills for the action bar.
    var pillCompact: Bool {
        get { self[PillCompactKey.self] }
        set { self[PillCompactKey.self] = newValue }
    }
}

/// Pill buttons used across the caption bar and sheets.
struct PillButtonStyle: ButtonStyle {
    var prominent = false
    var tint: Color = .accentColor
    @Environment(\.isEnabled) private var enabled
    @Environment(\.pillCompact) private var compact

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(compact ? .callout.weight(.semibold) : .body.weight(.semibold))
            .padding(.horizontal, compact ? 12 : 20).padding(.vertical, compact ? 8 : 12)
            .foregroundStyle(prominent ? Color.white : tint)
            .background(prominent ? tint : tint.opacity(0.12), in: Capsule())
            .opacity(enabled ? (configuration.isPressed ? 0.7 : 1) : 0.4)
    }
}
