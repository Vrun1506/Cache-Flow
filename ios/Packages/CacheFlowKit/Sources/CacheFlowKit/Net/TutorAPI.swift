import CoreGraphics
import Foundation

// The HTTP half of the v2 contract (Interface.md "v2 HTTP").

public struct SessionInfo: Codable, Equatable, Sendable {
    public var sessionID: String
    public var protocolVersion: Int
    public var boardRevision: BoardRevision

    public init(sessionID: String, protocolVersion: Int = ProtocolV2.version, boardRevision: BoardRevision) {
        self.sessionID = sessionID; self.protocolVersion = protocolVersion; self.boardRevision = boardRevision
    }
    private enum CodingKeys: String, CodingKey {
        case sessionID = "session_id", protocolVersion = "protocol_version", boardRevision = "board_revision"
    }
}

/// `202 Accepted` body for question, board and intent requests.
public struct Accepted: Codable, Equatable, Sendable {
    public var requestID: String
    public init(requestID: String) { self.requestID = requestID }
    private enum CodingKeys: String, CodingKey { case requestID = "request_id" }
}

/// `/audio` response: a candidate the student must confirm when ambiguous.
public struct TranscriptCandidate: Codable, Equatable, Sendable {
    public var requestID: String
    public var text: String
    public var ambiguous: Bool
    public init(requestID: String, text: String, ambiguous: Bool) { self.requestID = requestID; self.text = text; self.ambiguous = ambiguous }
    private enum CodingKeys: String, CodingKey { case requestID = "request_id", text = "transcript", ambiguous }
}

public enum QuestionInput: Equatable, Sendable {
    case image(png: Data)
    case text(String)
}

/// A bounded snapshot of the student's working area at one board revision.
public struct BoardSnapshot: Equatable, Sendable {
    public static let maxBytes = 4 * 1024 * 1024
    public static let maxLines = 40

    public var boardRevision: BoardRevision
    /// Board-space rectangle the PNG covers; line boxes are in board space.
    public var imageRegion: CGRect
    public var png: Data
    public var lines: [Region]

    public init(boardRevision: BoardRevision, imageRegion: CGRect, png: Data, lines: [Region]) {
        self.boardRevision = boardRevision; self.imageRegion = imageRegion; self.png = png; self.lines = lines
    }

    public var isWithinBounds: Bool { png.count <= Self.maxBytes && lines.count <= Self.maxLines }
}

public struct IntentRequest: Codable, Equatable, Sendable {
    public var action: IntentAction
    public var boardRevision: BoardRevision
    public var requestID: String
    public var text: String?
    public var turnID: String?

    public init(action: IntentAction, boardRevision: BoardRevision, requestID: String, text: String? = nil, turnID: String? = nil) {
        self.action = action; self.boardRevision = boardRevision; self.requestID = requestID; self.text = text; self.turnID = turnID
    }
    private enum CodingKeys: String, CodingKey {
        case action, boardRevision = "board_revision", requestID = "request_id", text, turnID = "turn_id"
    }
}

/// `GET /summary`: assisted vs independent evidence and the transfer result.
public struct SessionSummary: Codable, Equatable, Sendable {
    public struct Item: Codable, Equatable, Sendable {
        public var problem: String
        public var outcome: Verdict
        public var assisted: Bool
        public var independent: Bool
        public var evidenceIDs: [String]
        public init(problem: String, outcome: Verdict, assisted: Bool, independent: Bool, evidenceIDs: [String]) {
            self.problem = problem; self.outcome = outcome; self.assisted = assisted; self.independent = independent
            self.evidenceIDs = evidenceIDs
        }
        private enum CodingKeys: String, CodingKey { case problem, outcome, assisted, independent, evidenceIDs = "evidence_ids" }
    }
    public var items: [Item]
    public var skills: [SkillLevel]
    public var nextStep: String

    public init(items: [Item], skills: [SkillLevel], nextStep: String) { self.items = items; self.skills = skills; self.nextStep = nextStep }
    private enum CodingKeys: String, CodingKey { case items, skills, nextStep = "next_step" }
}

public enum TutorAPIError: Error, Equatable {
    case notConfigured
    case http(Int)
    case malformedResponse
    /// 409: the request's board revision is behind the server's.
    case staleRevision(latest: BoardRevision)
    /// 426: this client speaks an incompatible protocol version.
    case upgradeRequired
    case tooLarge
}

public protocol TutorAPI: Sendable {
    func createSession(presetID: String?, locale: String) async throws -> SessionInfo
    func submitQuestion(sessionID: String, _ input: QuestionInput, boardRevision: BoardRevision, requestID: String) async throws -> Accepted
    func submitBoard(sessionID: String, _ snapshot: BoardSnapshot, requestID: String) async throws -> Accepted
    func submitAudio(sessionID: String, wav: Data, boardRevision: BoardRevision, requestID: String) async throws -> TranscriptCandidate
    func sendIntent(sessionID: String, _ intent: IntentRequest) async throws -> Accepted
    func summary(sessionID: String) async throws -> SessionSummary
    func deleteSession(sessionID: String) async throws
    func health() async throws -> HealthReport
}

/// Pure request builders, so the wire format is unit-testable.
public enum TutorRequests {
    public static func createSession(_ p: ServerProfile, presetID: String?, locale: String) throws -> URLRequest {
        struct Body: Encodable { var preset_id: String?; var locale: String }
        return try json(p, "/v2/sessions", method: "POST", Body(preset_id: presetID, locale: locale))
    }

    public static func question(_ p: ServerProfile, sessionID: String, _ input: QuestionInput,
                                boardRevision: BoardRevision, requestID: String) throws -> URLRequest {
        var r = try make(p, "/v2/sessions/\(sessionID)/question", method: "POST")
        var form = MultipartForm()
        form.addField(name: "board_revision", value: String(boardRevision))
        form.addField(name: "request_id", value: requestID)
        switch input {
        case .image(let png): form.addFile(name: "image", filename: "question.png", mimeType: "image/png", data: png)
        case .text(let t): form.addField(name: "text", value: t)
        }
        r.setValue(form.contentType, forHTTPHeaderField: "Content-Type")
        r.httpBody = form.finalized()
        return r
    }

    private struct BoardMeta: Encodable {
        var board_revision: Int
        var request_id: String
        var image_region: [Double]
        var lines: [Region]
    }

    public static func board(_ p: ServerProfile, sessionID: String, _ s: BoardSnapshot, requestID: String) throws -> URLRequest {
        guard s.isWithinBounds else { throw TutorAPIError.tooLarge }
        var r = try make(p, "/v2/sessions/\(sessionID)/board", method: "POST")
        var form = MultipartForm()
        let meta = BoardMeta(board_revision: s.boardRevision, request_id: requestID,
                             image_region: WireGeometry.array(s.imageRegion), lines: s.lines)
        form.addField(name: "meta", value: String(decoding: try WireCoding.encoder().encode(meta), as: UTF8.self))
        form.addFile(name: "image", filename: "board.png", mimeType: "image/png", data: s.png)
        r.setValue(form.contentType, forHTTPHeaderField: "Content-Type")
        r.httpBody = form.finalized()
        return r
    }

    public static func audio(_ p: ServerProfile, sessionID: String, wav: Data, boardRevision: BoardRevision,
                             requestID: String) throws -> URLRequest {
        var r = try make(p, "/v2/sessions/\(sessionID)/audio", method: "POST")
        var form = MultipartForm()
        form.addField(name: "board_revision", value: String(boardRevision))
        form.addField(name: "request_id", value: requestID)
        form.addFile(name: "audio", filename: "utterance.wav", mimeType: "audio/wav", data: wav)
        r.setValue(form.contentType, forHTTPHeaderField: "Content-Type")
        r.httpBody = form.finalized()
        return r
    }

    public static func intent(_ p: ServerProfile, sessionID: String, _ intent: IntentRequest) throws -> URLRequest {
        try json(p, "/v2/sessions/\(sessionID)/intent", method: "POST", intent)
    }

    public static func summary(_ p: ServerProfile, sessionID: String) throws -> URLRequest {
        try make(p, "/v2/sessions/\(sessionID)/summary", method: "GET")
    }

    public static func delete(_ p: ServerProfile, sessionID: String) throws -> URLRequest {
        try make(p, "/v2/sessions/\(sessionID)", method: "DELETE")
    }

    public static func health(_ p: ServerProfile) throws -> URLRequest { try make(p, "/v2/health", method: "GET") }

    private static func json<T: Encodable>(_ p: ServerProfile, _ path: String, method: String, _ body: T) throws -> URLRequest {
        var r = try make(p, path, method: method)
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try WireCoding.encoder().encode(body)
        return r
    }

    private static func make(_ p: ServerProfile, _ path: String, method: String) throws -> URLRequest {
        guard let url = p.url(path: path) else { throw TutorAPIError.notConfigured }
        var r = URLRequest(url: url, timeoutInterval: 15)
        r.httpMethod = method
        r.setValue(String(ProtocolV2.version), forHTTPHeaderField: "X-Socrates-Protocol")
        return r
    }

    /// Map a non-2xx response to a typed error.
    public static func error(status: Int, body: Data) -> TutorAPIError {
        switch status {
        case 409:
            struct Conflict: Decodable { var latest_board_revision: Int }
            if let c = try? JSONDecoder().decode(Conflict.self, from: body) { return .staleRevision(latest: c.latest_board_revision) }
            return .http(409)
        case 413: return .tooLarge
        case 426: return .upgradeRequired
        default: return .http(status)
        }
    }
}

public struct HTTPTutorAPI: TutorAPI {
    public let profile: ServerProfile
    private let session: URLSession

    public init(profile: ServerProfile, session: URLSession = .shared) {
        self.profile = profile
        self.session = session
    }

    public func createSession(presetID: String?, locale: String) async throws -> SessionInfo {
        let info = try decode(SessionInfo.self, try await send(TutorRequests.createSession(profile, presetID: presetID, locale: locale)))
        guard info.protocolVersion == ProtocolV2.version else { throw TutorAPIError.upgradeRequired }
        return info
    }

    public func submitQuestion(sessionID: String, _ input: QuestionInput, boardRevision: BoardRevision, requestID: String) async throws -> Accepted {
        try decode(Accepted.self, try await send(TutorRequests.question(profile, sessionID: sessionID, input,
                                                                        boardRevision: boardRevision, requestID: requestID)))
    }

    public func submitBoard(sessionID: String, _ snapshot: BoardSnapshot, requestID: String) async throws -> Accepted {
        try decode(Accepted.self, try await send(TutorRequests.board(profile, sessionID: sessionID, snapshot, requestID: requestID)))
    }

    public func submitAudio(sessionID: String, wav: Data, boardRevision: BoardRevision, requestID: String) async throws -> TranscriptCandidate {
        try decode(TranscriptCandidate.self, try await send(TutorRequests.audio(profile, sessionID: sessionID, wav: wav,
                                                                                boardRevision: boardRevision, requestID: requestID)))
    }

    public func sendIntent(sessionID: String, _ intent: IntentRequest) async throws -> Accepted {
        try decode(Accepted.self, try await send(TutorRequests.intent(profile, sessionID: sessionID, intent)))
    }

    public func summary(sessionID: String) async throws -> SessionSummary {
        try decode(SessionSummary.self, try await send(TutorRequests.summary(profile, sessionID: sessionID)))
    }

    public func deleteSession(sessionID: String) async throws {
        _ = try await send(TutorRequests.delete(profile, sessionID: sessionID))
    }

    public func health() async throws -> HealthReport {
        try decode(HealthReport.self, try await send(TutorRequests.health(profile)))
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw TutorAPIError.malformedResponse }
        guard (200..<300).contains(http.statusCode) else { throw TutorRequests.error(status: http.statusCode, body: data) }
        return data
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do { return try JSONDecoder().decode(type, from: data) } catch { throw TutorAPIError.malformedResponse }
    }
}
