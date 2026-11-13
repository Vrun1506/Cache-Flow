import Foundation

/// Where the Spark session API lives, plus a built-in mock so the app runs
/// with no server at all. v2 expects authenticated local HTTPS/WSS.
public struct ServerProfile: Codable, Equatable, Identifiable, Sendable {
    public var id: String
    public var name: String
    public var baseURL: String

    public init(id: String, name: String, baseURL: String) {
        self.id = id
        self.name = name
        self.baseURL = baseURL
    }

    public var isMock: Bool { id == Self.mock.id }

    public static let mock = ServerProfile(id: "mock", name: "Mock", baseURL: "")
    public static let magicDNS = ServerProfile(id: "magicdns", name: "MagicDNS", baseURL: "https://tutor-spark:8443")
    public static let lan = ServerProfile(id: "lan", name: "LAN", baseURL: "https://192.168.1.50:8443")
    public static let defaults: [ServerProfile] = [.mock, .magicDNS, .lan]

    public func url(path: String, query: [URLQueryItem] = []) -> URL? {
        guard !isMock, var c = URLComponents(string: baseURL.trimmingCharacters(in: .whitespaces)),
              c.host != nil else { return nil }
        let base = c.path.hasSuffix("/") ? String(c.path.dropLast()) : c.path
        c.path = base + (path.hasPrefix("/") ? path : "/" + path)
        c.queryItems = query.isEmpty ? nil : query
        return c.url
    }

    /// `GET /v2/sessions/{id}/events`, resuming after `afterSeq` when known.
    /// `http` becomes `ws`, `https` becomes `wss`.
    public func eventsURL(sessionID: String, afterSeq: Int?) -> URL? {
        let query = afterSeq.map { [URLQueryItem(name: "after_seq", value: String($0))] } ?? []
        guard let http = url(path: "/v2/sessions/\(sessionID)/events", query: query),
              var c = URLComponents(url: http, resolvingAgainstBaseURL: false) else { return nil }
        c.scheme = (c.scheme == "https") ? "wss" : "ws"
        return c.url
    }

    /// Resolve a server-relative audio URL such as `/v2/audio/t3.wav`.
    public func resolve(_ audioURL: String) -> URL? {
        if let abs = URL(string: audioURL), abs.scheme != nil { return abs }
        return url(path: audioURL)
    }
}

/// Problems the student can pick when handwriting isn't working. IDs are
/// proposed preset IDs for `POST /v2/sessions`.
public struct Preset: Equatable, Identifiable, Sendable {
    public var id: String
    public var display: String
    public var subject: String

    public static let all: [Preset] = [
        Preset(id: "quad_5_6", display: "x² + 5x + 6 = 0", subject: "algebra"),
        Preset(id: "quad_7_12", display: "x² + 7x + 12 = 0", subject: "algebra"),
        Preset(id: "lin_2_3_11", display: "2x + 3 = 11", subject: "algebra"),
        Preset(id: "phys_forces_box", display: "Forces on a box resting on a table", subject: "physics"),
    ]
}
