import Foundation

/// A v2 server event. Unknown types decode to `.unknown` and are ignored.
public enum ServerEvent: Equatable, Sendable {
    case state(LessonSnapshot)
    case readBack(ReadBack)
    case evidence(Evidence)
    case tutorTurn(TutorTurn)
    case tutorInkPreview(TutorInk)
    case tutorInkCommit(CommitRef)
    case scenePreview(TutorScene)
    case sceneCommit(CommitRef)
    case audioChunk(AudioChunk)
    case agentTrace(AgentTrace)
    case skills([SkillLevel])
    case error(ServerError)
    case turnCancelled(TurnCancelled)
    case unknown(type: String)

    public var type: String {
        switch self {
        case .state: "state"
        case .readBack: "read_back"
        case .evidence: "evidence"
        case .tutorTurn: "tutor_turn"
        case .tutorInkPreview: "tutor_ink_preview"
        case .tutorInkCommit: "tutor_ink_commit"
        case .scenePreview: "scene_preview"
        case .sceneCommit: "scene_commit"
        case .audioChunk: "audio_chunk"
        case .agentTrace: "agent_trace"
        case .skills: "skills"
        case .error: "error"
        case .turnCancelled: "turn_cancelled"
        case .unknown(let t): t
        }
    }

    /// Durable events are replayed after reconnect; speech never is.
    public var isDurable: Bool {
        switch self {
        case .audioChunk, .agentTrace, .unknown: false
        default: true
        }
    }
}

private struct SkillsPayload: Codable { var skills: [SkillLevel] }

/// The sequenced JSON envelope every stream event travels in:
/// `{"v":2,"type":…,"session_id":…,"seq":…,"board_revision":…,"turn_id"?,"run_id"?,"request_id"?,"replayed"?,"payload":{…}}`.
public struct ServerEnvelope: Codable, Equatable, Sendable {
    public var version: Int
    public var sessionID: String
    public var seq: Int
    public var boardRevision: BoardRevision
    public var turnID: String?
    public var runID: String?
    public var requestID: String?
    /// Set on events re-sent after a reconnect; their speech must not play.
    public var replayed: Bool
    public var event: ServerEvent

    public init(sessionID: String, seq: Int, boardRevision: BoardRevision, turnID: String? = nil, runID: String? = nil,
                requestID: String? = nil, replayed: Bool = false, event: ServerEvent, version: Int = ProtocolV2.version) {
        self.version = version; self.sessionID = sessionID; self.seq = seq; self.boardRevision = boardRevision
        self.turnID = turnID; self.runID = runID; self.requestID = requestID; self.replayed = replayed; self.event = event
    }

    private enum K: String, CodingKey {
        case version = "v", type, sessionID = "session_id", seq, boardRevision = "board_revision"
        case turnID = "turn_id", runID = "run_id", requestID = "request_id", replayed, payload
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        version = try c.decode(Int.self, forKey: .version)
        sessionID = try c.decode(String.self, forKey: .sessionID)
        seq = try c.decode(Int.self, forKey: .seq)
        boardRevision = try c.decode(Int.self, forKey: .boardRevision)
        turnID = try c.decodeIfPresent(String.self, forKey: .turnID)
        runID = try c.decodeIfPresent(String.self, forKey: .runID)
        requestID = try c.decodeIfPresent(String.self, forKey: .requestID)
        replayed = try c.flag(.replayed)
        let type = try c.decode(String.self, forKey: .type)
        func p<T: Decodable>(_ t: T.Type) throws -> T { try c.decode(T.self, forKey: .payload) }
        switch type {
        case "state": event = .state(try p(LessonSnapshot.self))
        case "read_back": event = .readBack(try p(ReadBack.self))
        case "evidence": event = .evidence(try p(Evidence.self))
        case "tutor_turn": event = .tutorTurn(try p(TutorTurn.self))
        case "tutor_ink_preview": event = .tutorInkPreview(try p(TutorInk.self))
        case "tutor_ink_commit": event = .tutorInkCommit(try p(CommitRef.self))
        case "scene_preview": event = .scenePreview(try p(TutorScene.self))
        case "scene_commit": event = .sceneCommit(try p(CommitRef.self))
        case "audio_chunk": event = .audioChunk(try p(AudioChunk.self))
        case "agent_trace": event = .agentTrace(try p(AgentTrace.self))
        case "skills": event = .skills(try p(SkillsPayload.self).skills)
        case "error": event = .error(try p(ServerError.self))
        case "turn_cancelled": event = .turnCancelled(try p(TurnCancelled.self))
        default: event = .unknown(type: type)
        }
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(version, forKey: .version)
        try c.encode(event.type, forKey: .type)
        try c.encode(sessionID, forKey: .sessionID)
        try c.encode(seq, forKey: .seq)
        try c.encode(boardRevision, forKey: .boardRevision)
        try c.encodeIfPresent(turnID, forKey: .turnID)
        try c.encodeIfPresent(runID, forKey: .runID)
        try c.encodeIfPresent(requestID, forKey: .requestID)
        if replayed { try c.encode(true, forKey: .replayed) }
        switch event {
        case .state(let x): try c.encode(x, forKey: .payload)
        case .readBack(let x): try c.encode(x, forKey: .payload)
        case .evidence(let x): try c.encode(x, forKey: .payload)
        case .tutorTurn(let x): try c.encode(x, forKey: .payload)
        case .tutorInkPreview(let x): try c.encode(x, forKey: .payload)
        case .tutorInkCommit(let x), .sceneCommit(let x): try c.encode(x, forKey: .payload)
        case .scenePreview(let x): try c.encode(x, forKey: .payload)
        case .audioChunk(let x): try c.encode(x, forKey: .payload)
        case .agentTrace(let x): try c.encode(x, forKey: .payload)
        case .skills(let x): try c.encode(SkillsPayload(skills: x), forKey: .payload)
        case .error(let x): try c.encode(x, forKey: .payload)
        case .turnCancelled(let x): try c.encode(x, forKey: .payload)
        case .unknown: try c.encode([String: String](), forKey: .payload)
        }
    }
}

/// iPad -> server stream messages (Interface.md: read_back_confirm, audio_done,
/// tutor_ink_ack, scene_ack, cancel_turn). Encoded as
/// `{"v":2,"type":…,"session_id":…,"board_revision":…,"payload":{…}}`.
public enum ClientMessage: Equatable, Sendable {
    case readBackConfirm(requestID: String, accepted: Bool, text: String?)
    case audioDone(turnID: String, chunkIndex: Int?)
    case tutorInkAck(turnID: String, inkID: String, placed: Bool, reason: String?)
    case sceneAck(turnID: String, sceneID: String, placed: Bool, reason: String?)
    case cancelTurn(turnID: String, requestID: String)

    public var type: String {
        switch self {
        case .readBackConfirm: "read_back_confirm"
        case .audioDone: "audio_done"
        case .tutorInkAck: "tutor_ink_ack"
        case .sceneAck: "scene_ack"
        case .cancelTurn: "cancel_turn"
        }
    }
}

public struct ClientEnvelope: Encodable, Equatable, Sendable {
    public var sessionID: String
    public var boardRevision: BoardRevision
    public var message: ClientMessage

    public init(sessionID: String, boardRevision: BoardRevision, message: ClientMessage) {
        self.sessionID = sessionID; self.boardRevision = boardRevision; self.message = message
    }

    private enum K: String, CodingKey { case version = "v", type, sessionID = "session_id", boardRevision = "board_revision", payload }
    private enum P: String, CodingKey {
        case requestID = "request_id", accepted, text, turnID = "turn_id", chunkIndex = "chunk_index"
        case inkID = "ink_id", sceneID = "scene_id", placed, reason
    }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(ProtocolV2.version, forKey: .version)
        try c.encode(message.type, forKey: .type)
        try c.encode(sessionID, forKey: .sessionID)
        try c.encode(boardRevision, forKey: .boardRevision)
        var p = c.nestedContainer(keyedBy: P.self, forKey: .payload)
        switch message {
        case let .readBackConfirm(r, accepted, text):
            try p.encode(r, forKey: .requestID); try p.encode(accepted, forKey: .accepted); try p.encodeIfPresent(text, forKey: .text)
        case let .audioDone(t, chunk):
            try p.encode(t, forKey: .turnID); try p.encodeIfPresent(chunk, forKey: .chunkIndex)
        case let .tutorInkAck(t, ink, placed, reason):
            try p.encode(t, forKey: .turnID); try p.encode(ink, forKey: .inkID)
            try p.encode(placed, forKey: .placed); try p.encodeIfPresent(reason, forKey: .reason)
        case let .sceneAck(t, scene, placed, reason):
            try p.encode(t, forKey: .turnID); try p.encode(scene, forKey: .sceneID)
            try p.encode(placed, forKey: .placed); try p.encodeIfPresent(reason, forKey: .reason)
        case let .cancelTurn(t, r):
            try p.encode(t, forKey: .turnID); try p.encode(r, forKey: .requestID)
        }
    }
}

/// Encoders/decoders with deterministic output for fixtures and the mock.
public enum WireCoding {
    public static func encoder() -> JSONEncoder {
        let e = JSONEncoder()
        e.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        return e
    }
    public static func decoder() -> JSONDecoder { JSONDecoder() }
}
