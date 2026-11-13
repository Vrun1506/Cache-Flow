import Foundation

public struct ComponentStatus: Codable, Equatable, Sendable {
    public var ok: Bool
    public var revision: String?
    public var latencyMs: Int?
    public var detail: String?

    public init(ok: Bool, revision: String? = nil, latencyMs: Int? = nil, detail: String? = nil) {
        self.ok = ok; self.revision = revision; self.latencyMs = latencyMs; self.detail = detail
    }
    private enum CodingKeys: String, CodingKey { case ok, revision, latencyMs = "latency_ms", detail }
}

/// `GET /v2/health`: availability and revisions of local components. No student data.
public struct HealthReport: Codable, Equatable, Sendable {
    public static let componentOrder = ["session_api", "vllm", "agent", "math", "guardrails", "tts", "asr"]

    public var protocolVersion: Int
    public var components: [String: ComponentStatus]

    public init(protocolVersion: Int = ProtocolV2.version, components: [String: ComponentStatus]) {
        self.protocolVersion = protocolVersion; self.components = components
    }
    private enum CodingKeys: String, CodingKey { case protocolVersion = "protocol_version", components }

    public var allOK: Bool { !components.isEmpty && components.values.allSatisfy(\.ok) }
}
