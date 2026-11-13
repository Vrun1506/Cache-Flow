/// Orders tutor speech: one clip at a time, never while the Pencil is down,
/// and nothing from a cancelled or superseded turn.
public struct SpeechQueue: Equatable, Sendable {
    public private(set) var pending: [SpeechRequest] = []
    public private(set) var current: SpeechRequest?
    public private(set) var penDown = false

    public init() {}

    public mutating func enqueue(_ r: SpeechRequest) {
        guard !pending.contains(r), current != r else { return }
        pending.append(r)
    }

    /// Drop queued clips for a turn (nil: all). Returns true if the clip now
    /// playing belongs to it and must be stopped.
    @discardableResult
    public mutating func cancel(turnID: String?) -> Bool {
        pending.removeAll { turnID == nil || $0.turnID == turnID }
        guard let c = current, turnID == nil || c.turnID == turnID else { return false }
        current = nil
        return true
    }

    public mutating func setPen(down: Bool) { penDown = down }

    /// The next clip to start, or nil while busy, empty or the Pencil is down.
    public mutating func next() -> SpeechRequest? {
        guard current == nil, !penDown, !pending.isEmpty else { return nil }
        current = pending.removeFirst()
        return current
    }

    /// The current clip ended. Returns it so the caller can send audio_done.
    @discardableResult
    public mutating func finished() -> SpeechRequest? {
        defer { current = nil }
        return current
    }
}

/// How the iPad treats an audio URL. `mock://clip/…` stands in for local
/// Magpie audio; `mock://lost/…` simulates a clip that never arrives.
public enum AudioURLKind: Equatable, Sendable {
    case remote, mockClip, mockLost

    public static func classify(_ url: String) -> AudioURLKind {
        if url.hasPrefix("mock://lost") { return .mockLost }
        if url.hasPrefix("mock://") { return .mockClip }
        return .remote
    }
}

/// Which intents make sense now. The server still authorizes every request.
public enum ActionPolicy {
    public static func available(_ s: LessonState) -> Set<IntentAction> {
        if s.pendingReadBack != nil { return [] }
        switch s.phase {
        case .ready: return [.tutor, .ask]
        case .tutoring: return [.cancel]
        case .studentTurn: return [.check, .hint, .explainStep, .draw, .showAnswer, .ask]
        case .independentCheck: return [.check, .quizAnswer, .hint, .showAnswer]
        default: return []
        }
    }

    /// The student's typed reply goes to the transfer item during an
    /// independent check, otherwise it is an `ask`.
    public static func replyAction(_ s: LessonState) -> IntentAction {
        s.phase == .independentCheck ? .quizAnswer : .ask
    }
}

/// Request IDs for idempotent commands.
public struct RequestIDGenerator: Sendable {
    private let prefix: String
    private var counter = 0
    public init(prefix: String) { self.prefix = prefix }
    public mutating func next() -> String { counter += 1; return "\(prefix)-\(counter)" }
}
