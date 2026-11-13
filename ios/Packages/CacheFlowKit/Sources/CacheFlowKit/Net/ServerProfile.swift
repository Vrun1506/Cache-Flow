import Foundation

/// Where the orchestrator lives. Three real profiles (MagicDNS, Tailscale IP,
/// plain LAN) plus a built-in mock so the app runs with no server at all.
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
    public static let magicDNS = ServerProfile(id: "magicdns", name: "MagicDNS", baseURL: "http://tutor-spark:8000")
    public static let tailscaleIP = ServerProfile(id: "tailscale-ip", name: "Tailscale IP", baseURL: "http://100.64.0.1:8000")
    public static let lan = ServerProfile(id: "lan", name: "LAN", baseURL: "http://192.168.1.50:8000")
    public static let defaults: [ServerProfile] = [.mock, .magicDNS, .tailscaleIP, .lan]

    public func url(path: String) -> URL? {
        guard !isMock, var c = URLComponents(string: baseURL.trimmingCharacters(in: .whitespaces)),
              c.host != nil else { return nil }
        let base = c.path.hasSuffix("/") ? String(c.path.dropLast()) : c.path
        c.path = base + (path.hasPrefix("/") ? path : "/" + path)
        return c.url
    }

    /// `http` becomes `ws`, `https` becomes `wss`.
    public func webSocketURL(sessionID: String) -> URL? {
        guard let http = url(path: "/ws/\(sessionID)"),
              var c = URLComponents(url: http, resolvingAgainstBaseURL: false) else { return nil }
        c.scheme = (c.scheme == "https") ? "wss" : "ws"
        return c.url
    }

    /// Resolve a server-relative clip URL such as `/audio/s2i.wav`.
    public func resolve(_ clipURL: String) -> URL? {
        if let abs = URL(string: clipURL), abs.scheme != nil { return abs }
        return url(path: clipURL)
    }
}

/// Problems the student can pick when handwriting isn't working. Bundled so the
/// picker always works, even with the server down. IDs are shared with the
/// backend's `data/problems.json`.
public struct Preset: Equatable, Identifiable, Sendable {
    public var id: String
    public var display: String
    public var kind: String

    public static let all: [Preset] = [
        Preset(id: "quad_5_6", display: "x² + 5x + 6 = 0", kind: "quadratic"),
        Preset(id: "quad_7_12", display: "x² + 7x + 12 = 0", kind: "quadratic"),
        Preset(id: "quad_m5_6", display: "x² − 5x + 6 = 0", kind: "quadratic"),
        Preset(id: "lin_2_3_11", display: "2x + 3 = 11", kind: "linear"),
    ]
}
