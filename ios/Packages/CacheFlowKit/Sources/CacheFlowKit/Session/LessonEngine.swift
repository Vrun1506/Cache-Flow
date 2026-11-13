import CoreGraphics
import Foundation

/// A tutor-layer annotation. Previews render as ghost ink; only committed items are durable.
public struct TutorInkItem: Equatable, Sendable {
    public var ink: TutorInk
    public var turnID: String
    public var committed: Bool
}

public struct SceneItem: Equatable, Sendable {
    public var scene: TutorScene
    public var turnID: String
    public var committed: Bool
}

/// One clip to play for a turn. `url == nil` never happens for server speech;
/// the app falls back to on-device speech of `caption` when a URL fails.
public struct SpeechRequest: Equatable, Sendable {
    public var turnID: String
    public var chunkIndex: Int?
    public var url: String
    public var caption: String

    public init(turnID: String, chunkIndex: Int? = nil, url: String, caption: String) {
        self.turnID = turnID; self.chunkIndex = chunkIndex; self.url = url; self.caption = caption
    }
}

public enum LessonEffect: Equatable, Sendable {
    case send(ClientMessage)
    case speak(SpeechRequest)
    /// Stop and drop queued speech for a turn (nil: all turns).
    case stopSpeech(turnID: String?)
}

/// Observable evidence that the client's ordering rules held.
public struct LessonCounters: Equatable, Sendable {
    public var duplicatesDropped = 0
    public var staleRejected = 0
    public var cancelledDropped = 0
    public var replayedSpeechSuppressed = 0
    public var previewsInvalidated = 0
    public var invalidRejected = 0
    public init() {}
}

public struct LessonState: Equatable, Sendable {
    public var sessionID: String
    /// The student's board revision as this iPad knows it.
    public var boardRevision: BoardRevision
    public var lastSeq: Int?
    public var phase: LessonPhase = .idle
    public var problem: ProblemInfo?
    public var caption: String
    public var captionTurnID: String?
    public var lastMove: TutorMove?
    public var activeTurnID: String?
    public var cancelledTurns: Set<String> = []
    public var supersededTurns: Set<String> = []
    public var pendingReadBack: ReadBack?
    /// Latest reading per student line (board read-backs).
    public var lineReadings: [Int: String] = [:]
    public var evidence: [Evidence] = []
    public var ink: [TutorInkItem] = []
    public var scenes: [SceneItem] = []
    public var highlights: [Region] = []
    public var skills: [SkillLevel] = []
    public var traces: [AgentTrace] = []
    public var notice: String?
    public var lastError: ServerError?
    public var assistedThisItem = false
    public var counters = LessonCounters()

    public init(sessionID: String, boardRevision: BoardRevision, caption: String) {
        self.sessionID = sessionID; self.boardRevision = boardRevision; self.caption = caption
    }

    public var committedInk: [TutorInkItem] { ink.filter(\.committed) }
    public var committedScenes: [SceneItem] { scenes.filter(\.committed) }
    public var latestEvidence: Evidence? { evidence.last }
    public var isTutorTurnActive: Bool { activeTurnID != nil && phase == .tutoring }
}

/// Folds sequenced server envelopes and student actions into `LessonState`,
/// returning side effects for the app to perform. Pure and synchronous, so
/// ordering, staleness, cancellation and replay rules are unit-testable.
public struct LessonEngine: Sendable {
    public static let maxTraces = 20

    public private(set) var state: LessonState
    /// Board-space bounds tutor annotations must stay inside (nil: unchecked).
    public var boardBounds: CGRect?

    public init(sessionID: String, boardRevision: BoardRevision, caption: String = "", boardBounds: CGRect? = nil) {
        state = LessonState(sessionID: sessionID, boardRevision: boardRevision, caption: caption)
        self.boardBounds = boardBounds
    }

    // MARK: Server events

    public mutating func receive(_ env: ServerEnvelope) -> [LessonEffect] {
        guard env.sessionID == state.sessionID, env.version == ProtocolV2.version else {
            state.counters.staleRejected += 1
            return []
        }
        if let last = state.lastSeq, env.seq <= last {
            state.counters.duplicatesDropped += 1
            return []
        }
        state.lastSeq = env.seq
        if Self.isTurnScoped(env.event), let t = env.turnID,
           state.cancelledTurns.contains(t) || state.supersededTurns.contains(t) {
            state.counters.cancelledDropped += 1
            return []
        }

        switch env.event {
        case .state(let s):
            return applySnapshot(s, serverRevision: env.boardRevision)
        case .readBack(let rb):
            if rb.source == .board {
                for r in rb.regions { if let i = r.lineIndex { state.lineReadings[i] = rb.text } }
            }
            if rb.ambiguous || rb.source == .question { state.pendingReadBack = rb }
            if rb.source == .transcript, !rb.ambiguous { state.notice = "You said: “\(rb.text)”" }
            return []
        case .evidence(let e):
            state.evidence.removeAll { $0.evidenceID == e.evidenceID }
            state.evidence.append(e)
            if !e.highlights.isEmpty { state.highlights = e.highlights }
            return []
        case .tutorTurn(let turn):
            return applyTurn(turn, env: env)
        case .tutorInkPreview(let ink):
            return applyInkPreview(ink, env: env)
        case .tutorInkCommit(let ref):
            commitInk(ref.id, env: env)
            return []
        case .scenePreview(let scene):
            return applyScenePreview(scene, env: env)
        case .sceneCommit(let ref):
            commitScene(ref.id, env: env)
            return []
        case .audioChunk(let chunk):
            guard let t = env.turnID else { return [] }
            if env.replayed { state.counters.replayedSpeechSuppressed += 1; return [] }
            return [.speak(SpeechRequest(turnID: t, chunkIndex: chunk.chunkIndex, url: chunk.audioURL, caption: chunk.caption))]
        case .agentTrace(let trace):
            state.traces.append(trace)
            if state.traces.count > Self.maxTraces { state.traces.removeFirst(state.traces.count - Self.maxTraces) }
            return []
        case .skills(let skills):
            state.skills = skills
            return []
        case .error(let err):
            state.lastError = err
            state.notice = err.message
            if let latest = err.latestBoardRevision { state.boardRevision = max(state.boardRevision, latest) }
            return []
        case .turnCancelled:
            guard let t = env.turnID else { return [] }
            return endTurn(t, cancelled: true)
        case .unknown:
            return []
        }
    }

    private static func isTurnScoped(_ e: ServerEvent) -> Bool {
        switch e {
        case .tutorTurn, .tutorInkPreview, .tutorInkCommit, .scenePreview, .sceneCommit, .audioChunk: true
        default: false
        }
    }

    private mutating func applySnapshot(_ s: LessonSnapshot, serverRevision: BoardRevision) -> [LessonEffect] {
        let newItem = s.problem?.text != state.problem?.text || s.problem?.item != state.problem?.item
        state.phase = s.phase
        state.problem = s.problem
        state.skills = s.skills
        // Unsent local strokes may put the iPad ahead; never move backwards.
        state.boardRevision = max(state.boardRevision, serverRevision)
        if newItem { state.assistedThisItem = false }
        if s.phase == .idle, s.problem == nil {
            state.ink = []; state.scenes = []; state.highlights = []; state.evidence = []; state.lineReadings = [:]
        }
        if s.phase != .tutoring, s.phase != .confirm { state.activeTurnID = nil }
        if s.phase != .confirm, state.pendingReadBack?.source != .transcript { state.pendingReadBack = nil }
        return []
    }

    private mutating func applyTurn(_ turn: TutorTurn, env: ServerEnvelope) -> [LessonEffect] {
        guard let t = env.turnID else { return [] }
        var effects: [LessonEffect] = []
        if let old = state.activeTurnID, old != t {
            state.supersededTurns.insert(old)
            dropUncommitted(turn: old)
            effects.append(.stopSpeech(turnID: old))
        }
        state.activeTurnID = t
        state.caption = turn.caption
        state.captionTurnID = t
        state.lastMove = turn.move
        state.notice = nil
        if turn.assisted || turn.move == .showAnswer { state.assistedThisItem = true }
        if !turn.highlights.isEmpty { state.highlights = turn.highlights }
        if let url = turn.audioURL, !url.isEmpty {
            if env.replayed { state.counters.replayedSpeechSuppressed += 1 }
            else { effects.append(.speak(SpeechRequest(turnID: t, url: url, caption: turn.caption))) }
        }
        return effects
    }

    private mutating func applyInkPreview(_ ink: TutorInk, env: ServerEnvelope) -> [LessonEffect] {
        let t = env.turnID ?? ""
        if let existing = state.ink.first(where: { $0.ink.inkID == ink.inkID }) {
            // Idempotent: a duplicate preview of known ink is re-acknowledged, never re-drawn.
            return [.send(.tutorInkAck(turnID: t, inkID: ink.inkID, placed: existing.turnID == t, reason: nil))]
        }
        guard Bounds.isValid(ink, within: boardBounds) else {
            state.counters.invalidRejected += 1
            return [.send(.tutorInkAck(turnID: t, inkID: ink.inkID, placed: false, reason: "invalid"))]
        }
        guard env.boardRevision == state.boardRevision else {
            state.counters.staleRejected += 1
            return [.send(.tutorInkAck(turnID: t, inkID: ink.inkID, placed: false, reason: "stale_revision"))]
        }
        state.ink.append(TutorInkItem(ink: ink, turnID: t, committed: false))
        return [.send(.tutorInkAck(turnID: t, inkID: ink.inkID, placed: true, reason: nil))]
    }

    private mutating func commitInk(_ id: String, env: ServerEnvelope) {
        guard let i = state.ink.firstIndex(where: { $0.ink.inkID == id }) else {
            state.counters.staleRejected += 1   // no placed preview: nothing may commit
            return
        }
        if state.ink[i].committed { return }
        guard env.boardRevision == state.boardRevision, state.ink[i].turnID == (env.turnID ?? "") else {
            state.counters.staleRejected += 1
            state.ink.remove(at: i)
            return
        }
        state.ink[i].committed = true
    }

    private mutating func applyScenePreview(_ scene: TutorScene, env: ServerEnvelope) -> [LessonEffect] {
        let t = env.turnID ?? ""
        if let existing = state.scenes.first(where: { $0.scene.sceneID == scene.sceneID }) {
            return [.send(.sceneAck(turnID: t, sceneID: scene.sceneID, placed: existing.turnID == t, reason: nil))]
        }
        guard Bounds.isValid(scene, within: boardBounds) else {
            state.counters.invalidRejected += 1
            return [.send(.sceneAck(turnID: t, sceneID: scene.sceneID, placed: false, reason: "invalid"))]
        }
        guard env.boardRevision == state.boardRevision else {
            state.counters.staleRejected += 1
            return [.send(.sceneAck(turnID: t, sceneID: scene.sceneID, placed: false, reason: "stale_revision"))]
        }
        state.scenes.append(SceneItem(scene: scene, turnID: t, committed: false))
        return [.send(.sceneAck(turnID: t, sceneID: scene.sceneID, placed: true, reason: nil))]
    }

    private mutating func commitScene(_ id: String, env: ServerEnvelope) {
        guard let i = state.scenes.firstIndex(where: { $0.scene.sceneID == id }) else {
            state.counters.staleRejected += 1
            return
        }
        if state.scenes[i].committed { return }
        guard env.boardRevision == state.boardRevision, state.scenes[i].turnID == (env.turnID ?? "") else {
            state.counters.staleRejected += 1
            state.scenes.remove(at: i)
            return
        }
        state.scenes[i].committed = true
    }

    private mutating func dropUncommitted(turn t: String) {
        state.ink.removeAll { $0.turnID == t && !$0.committed }
        state.scenes.removeAll { $0.turnID == t && !$0.committed }
    }

    private mutating func endTurn(_ t: String, cancelled: Bool) -> [LessonEffect] {
        if cancelled { state.cancelledTurns.insert(t) }
        dropUncommitted(turn: t)
        if state.activeTurnID == t { state.activeTurnID = nil }
        return [.stopSpeech(turnID: t)]
    }

    // MARK: Student actions

    /// The student's Pencil changed the board: bump the revision and discard
    /// tutor previews composed against the old board.
    public mutating func studentInkChanged() {
        state.boardRevision += 1
        let before = state.ink.count + state.scenes.count
        state.ink.removeAll { !$0.committed }
        state.scenes.removeAll { !$0.committed }
        state.counters.previewsInvalidated += before - state.ink.count - state.scenes.count
    }

    /// Cancel the active tutor turn locally (audio, ink and scenes stop now)
    /// and tell the server where the cancellation boundary is.
    public mutating func cancelActiveTurn(requestID: String) -> [LessonEffect] {
        guard let t = state.activeTurnID else { return [] }
        var effects = endTurn(t, cancelled: true)
        effects.append(.send(.cancelTurn(turnID: t, requestID: requestID)))
        if state.phase == .tutoring { state.phase = .studentTurn }
        return effects
    }

    /// Accept the candidate (`text == nil`), correct it, or reject it.
    public mutating func confirmReadBack(accepted: Bool, text: String? = nil) -> [LessonEffect] {
        guard let rb = state.pendingReadBack else { return [] }
        state.pendingReadBack = nil
        let corrected = text?.trimmingCharacters(in: .whitespacesAndNewlines)
        let final = (corrected?.isEmpty == false) ? corrected : nil
        if accepted, rb.source == .board {
            for r in rb.regions { if let i = r.lineIndex { state.lineReadings[i] = final ?? rb.text } }
        }
        return [.send(.readBackConfirm(requestID: rb.requestID, accepted: accepted, text: final))]
    }

    /// Clear the tutor layer only. The student's ink is never touched.
    public mutating func clearTutorLayer() {
        state.ink = []
        state.scenes = []
        state.highlights = []
    }

    /// The server reported a newer board revision (409 conflict).
    public mutating func adopt(serverRevision: BoardRevision) {
        state.boardRevision = max(state.boardRevision, serverRevision)
    }

    public mutating func setNotice(_ text: String?) { state.notice = text }
    public mutating func setCaption(_ text: String) { state.caption = text }
}

/// Bounds and complexity checks for tutor annotations.
public enum Bounds {
    public static func isValid(_ ink: TutorInk, within board: CGRect?) -> Bool {
        guard ink.strokes.count <= TutorInk.maxStrokes,
              ink.strokes.allSatisfy({ $0.count <= TutorInk.maxPointsPerStroke }),
              (ink.formula?.count ?? 0) <= TutorInk.maxFormulaLength,
              !ink.strokes.isEmpty || !(ink.formula ?? "").isEmpty || ink.style != .write,
              inside(ink.region, board) else { return false }
        let area = ink.region.insetBy(dx: -8, dy: -8)
        return ink.strokes.allSatisfy { $0.allSatisfy(area.contains) }
    }

    public static func isValid(_ scene: TutorScene, within board: CGRect?) -> Bool {
        guard scene.primitives.count <= TutorScene.maxPrimitives,
              scene.primitives.reduce(0, { $0 + $1.points.count }) <= TutorScene.maxPoints,
              scene.primitives.allSatisfy({ SceneKind.approved.contains($0.kind) && ($0.text?.count ?? 0) <= 80 }),
              inside(scene.region, board) else { return false }
        let area = scene.region.insetBy(dx: -8, dy: -8)
        return scene.primitives.allSatisfy { $0.points.allSatisfy(area.contains) }
    }

    private static func inside(_ r: CGRect, _ board: CGRect?) -> Bool {
        guard r.minX.isFinite, r.minY.isFinite, r.width.isFinite, r.height.isFinite, r.width > 0, r.height > 0 else { return false }
        return board.map { $0.contains(r) } ?? true
    }
}
