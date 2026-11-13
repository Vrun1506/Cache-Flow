import Foundation

public struct ServiceStatus: Decodable, Equatable, Sendable {
    public var ok: Bool
    public var latencyMs: Int?
    public var detail: String?

    public init(ok: Bool, latencyMs: Int? = nil, detail: String? = nil) {
        self.ok = ok; self.latencyMs = latencyMs; self.detail = detail
    }
    private enum CodingKeys: String, CodingKey { case ok, latencyMs = "latency_ms", detail }
}

/// `GET /health`. Drives the coloured dots in the settings sheet.
public struct HealthReport: Decodable, Equatable, Sendable {
    public static let serviceOrder = ["llm", "vlm", "asr", "tts", "arm"]

    public var services: [String: ServiceStatus]
    public var tailscaleIP: String?
    public var lanIP: String?

    public init(services: [String: ServiceStatus], tailscaleIP: String? = nil, lanIP: String? = nil) {
        self.services = services; self.tailscaleIP = tailscaleIP; self.lanIP = lanIP
    }
    private enum CodingKeys: String, CodingKey { case services, tailscaleIP = "tailscale_ip", lanIP = "lan_ip" }

    public var allOK: Bool { !services.isEmpty && services.values.allSatisfy(\.ok) }
}
