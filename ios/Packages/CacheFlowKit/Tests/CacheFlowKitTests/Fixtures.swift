import Foundation
@testable import CacheFlowKit

/// Shared v2 contract fixtures (Fixtures/v2). The backend can load the same
/// files to prove both sides agree on the wire format.
enum Fixture {
    static var root: URL { Bundle.module.resourceURL!.appendingPathComponent("Fixtures/v2") }

    static func data(_ path: String) throws -> Data { try Data(contentsOf: root.appendingPathComponent(path)) }

    static func files(in dir: String) throws -> [String] {
        try FileManager.default.contentsOfDirectory(atPath: root.appendingPathComponent(dir).path)
            .filter { $0.hasSuffix(".json") }.sorted().map { "\(dir)/\($0)" }
    }

    static func envelope(_ path: String) throws -> ServerEnvelope {
        try WireCoding.decoder().decode(ServerEnvelope.self, from: try data(path))
    }

    /// Canonical JSON object for order-insensitive comparison.
    static func object(_ data: Data) throws -> NSObject {
        try JSONSerialization.jsonObject(with: data) as! NSObject
    }

    struct Scenario: Decodable {
        struct Step: Decodable {
            var server: ServerEnvelope?
            var student: String?
            var accepted: Bool?
            var text: String?
        }
        var description: String
        var session_id: String
        var board_revision: Int
        var steps: [Step]
    }

    /// Feed a scenario through a fresh engine; returns the engine and every effect.
    static func run(_ name: String, boardBounds: CGRect? = CGRect(x: 0, y: 0, width: 1210, height: 638)) throws
        -> (engine: LessonEngine, effects: [LessonEffect]) {
        let s = try JSONDecoder().decode(Scenario.self, from: try data("scenarios/\(name).json"))
        var engine = LessonEngine(sessionID: s.session_id, boardRevision: s.board_revision, boardBounds: boardBounds)
        var effects: [LessonEffect] = []
        var requests = RequestIDGenerator(prefix: "test")
        for step in s.steps {
            if let env = step.server { effects += engine.receive(env) }
            switch step.student {
            case "ink": engine.studentInkChanged()
            case "cancel": effects += engine.cancelActiveTurn(requestID: requests.next())
            case "confirm": effects += engine.confirmReadBack(accepted: step.accepted ?? true, text: step.text)
            default: break
            }
        }
        return (engine, effects)
    }
}

extension LessonEffect {
    var sent: ClientMessage? { if case .send(let m) = self { m } else { nil } }
    var spoken: SpeechRequest? { if case .speak(let r) = self { r } else { nil } }
}
