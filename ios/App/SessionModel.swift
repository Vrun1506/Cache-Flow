import CacheFlowKit
import Foundation
import Observation

/// How the latest tutor clip reached the student.
enum AudioStatus: Equatable {
    case idle
    /// Local server audio played.
    case played
    /// The clip was lost or failed; the caption stayed up and on-device speech read it.
    case fallback
}

/// Everything the views render. `lesson` mirrors `LessonEngine.state`; the
/// rest is device state owned by `TutorClient`.
@MainActor @Observable
final class SessionModel {
    var lesson = LessonState(sessionID: "", boardRevision: 0,
                             caption: "Hi! I'm Socrates. Write a problem in the box, or pick one.")
    var connection: ConnectionState = .offline
    var speaking = false
    var recording = false
    var audioStatus: AudioStatus = .idle
    var health: HealthReport?
    var summary: SessionSummary?
    var showSummary = false
    var clipsPlayed = 0
    var fallbacks = 0
    var reconnects = 0

    /// Intents travel over HTTP, so they stay available while the stream
    /// reconnects; their results arrive with the cursor replay.
    var actions: Set<IntentAction> {
        [.connected, .reconnecting].contains(connection) ? ActionPolicy.available(lesson) : []
    }
    var acceptsQuestion: Bool { [.idle, .reading, .confirm, .ready].contains(lesson.phase) && lesson.problem?.item != "independent_check" }

    /// A compact, deterministic line for the UI tests and the operator.
    var debugStatus: String {
        let l = lesson
        return [
            "phase=\(l.phase.rawValue)", "seq=\(l.lastSeq ?? 0)", "rev=\(l.boardRevision)",
            "ink=\(l.committedInk.count)", "previews=\(l.ink.count - l.committedInk.count)",
            "scenes=\(l.committedScenes.count)", "speech=\(clipsPlayed)", "fallback=\(fallbacks)",
            "stale=\(l.counters.staleRejected)", "dup=\(l.counters.duplicatesDropped)",
            "cancelled=\(l.counters.cancelledDropped)", "replaySuppressed=\(l.counters.replayedSpeechSuppressed)",
            "reconnects=\(reconnects)", "speaking=\(speaking ? 1 : 0)", "conn=\(connection.label.lowercased())",
        ].joined(separator: " ")
    }
}
