import CoreGraphics
import Foundation

public struct LineUpload: Sendable, Equatable {
    public var index: Int
    public var bbox: CGRect
    public var png: Data
    public init(index: Int, bbox: CGRect, png: Data) { self.index = index; self.bbox = bbox; self.png = png }
}

public enum TutorAPIError: Error, Equatable {
    case notConfigured
    case http(Int)
    case malformedResponse
}

/// The HTTP half of the iPad <-> orchestrator contract. See Interface.md.
public protocol TutorAPI: Sendable {
    func createSession(problemID: String?) async throws -> String
    func submitQuestion(sessionID: String, png: Data) async throws
    func submitLines(sessionID: String, lines: [LineUpload]) async throws
    /// Returns the transcript; the server also feeds it to the state machine.
    func submitAudio(sessionID: String, wav: Data) async throws -> String
    func health() async throws -> HealthReport
    func admin(_ action: String, value: String?) async throws
}

/// Pure request builders, so the wire format is unit-testable.
public enum TutorRequests {
    public static func createSession(_ p: ServerProfile, problemID: String?) throws -> URLRequest {
        var r = try make(p, "/session", method: "POST")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        let body: [String: String] = problemID.map { ["problem_id": $0] } ?? [:]
        r.httpBody = try JSONEncoder().encode(body)
        return r
    }

    public static func question(_ p: ServerProfile, sessionID: String, png: Data) throws -> URLRequest {
        var r = try make(p, "/session/\(sessionID)/question", method: "POST")
        r.setValue("image/png", forHTTPHeaderField: "Content-Type")
        r.httpBody = png
        return r
    }

    private struct LineMeta: Encodable { var index: Int; var bbox: [Double] }

    public static func lines(_ p: ServerProfile, sessionID: String, lines: [LineUpload]) throws -> URLRequest {
        var r = try make(p, "/session/\(sessionID)/lines", method: "POST")
        var form = MultipartForm()
        let meta = lines.map { LineMeta(index: $0.index, bbox: [$0.bbox.minX, $0.bbox.minY, $0.bbox.width, $0.bbox.height]) }
        form.addField(name: "meta", value: String(decoding: try JSONEncoder().encode(meta), as: UTF8.self))
        for l in lines { form.addFile(name: "line_\(l.index)", filename: "line_\(l.index).png", mimeType: "image/png", data: l.png) }
        r.setValue(form.contentType, forHTTPHeaderField: "Content-Type")
        r.httpBody = form.finalized()
        return r
    }

    public static func audio(_ p: ServerProfile, sessionID: String, wav: Data) throws -> URLRequest {
        var r = try make(p, "/session/\(sessionID)/audio", method: "POST")
        r.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        r.httpBody = wav
        return r
    }

    public static func health(_ p: ServerProfile) throws -> URLRequest { try make(p, "/health", method: "GET") }

    public static func admin(_ p: ServerProfile, action: String, value: String?) throws -> URLRequest {
        var r = try make(p, "/admin/\(action)", method: "POST")
        r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        r.httpBody = try JSONEncoder().encode(value.map { ["value": $0] } ?? [:])
        return r
    }

    private static func make(_ p: ServerProfile, _ path: String, method: String) throws -> URLRequest {
        guard let url = p.url(path: path) else { throw TutorAPIError.notConfigured }
        var r = URLRequest(url: url, timeoutInterval: 15)
        r.httpMethod = method
        return r
    }
}

public struct HTTPTutorAPI: TutorAPI {
    public let profile: ServerProfile
    private let session: URLSession

    public init(profile: ServerProfile, session: URLSession = .shared) {
        self.profile = profile
        self.session = session
    }

    public func createSession(problemID: String?) async throws -> String {
        struct R: Decodable { var sessionID: String; enum CodingKeys: String, CodingKey { case sessionID = "session_id" } }
        let data = try await send(TutorRequests.createSession(profile, problemID: problemID))
        return try decode(R.self, data).sessionID
    }

    public func submitQuestion(sessionID: String, png: Data) async throws {
        _ = try await send(TutorRequests.question(profile, sessionID: sessionID, png: png))
    }

    public func submitLines(sessionID: String, lines: [LineUpload]) async throws {
        _ = try await send(TutorRequests.lines(profile, sessionID: sessionID, lines: lines))
    }

    public func submitAudio(sessionID: String, wav: Data) async throws -> String {
        struct R: Decodable { var transcript: String }
        let data = try await send(TutorRequests.audio(profile, sessionID: sessionID, wav: wav))
        return try decode(R.self, data).transcript
    }

    public func health() async throws -> HealthReport {
        try decode(HealthReport.self, try await send(TutorRequests.health(profile)))
    }

    public func admin(_ action: String, value: String?) async throws {
        _ = try await send(TutorRequests.admin(profile, action: action, value: value))
    }

    private func send(_ request: URLRequest) async throws -> Data {
        let (data, response) = try await session.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw TutorAPIError.malformedResponse }
        guard (200..<300).contains(http.statusCode) else { throw TutorAPIError.http(http.statusCode) }
        return data
    }

    private func decode<T: Decodable>(_ type: T.Type, _ data: Data) throws -> T {
        do { return try JSONDecoder().decode(type, from: data) } catch { throw TutorAPIError.malformedResponse }
    }
}
