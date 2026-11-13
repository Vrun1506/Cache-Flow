import CoreGraphics
import Foundation

/// Deterministic fixture content for the in-process mock. This is NOT a math
/// verifier: it maps a handful of known strings from the first fixture in
/// Interface.md to the evidence the real `check_math_step` tool should return.
public enum MockLessonScript {
    public struct Reading: Equatable, Sendable {
        public var text: String
        public var alternatives: [String]
        public var ambiguous: Bool { !alternatives.isEmpty }
    }

    /// What the mock "reads" on each student line, by line index. The second
    /// line is deliberately ambiguous (plus or minus), exercising the
    /// ungraded/confirm path.
    public static let boardReadings: [Reading] = [
        Reading(text: "(x − 2)(x − 3) = 0", alternatives: []),
        Reading(text: "(x + 2)(x + 3) = 0", alternatives: ["(x + 2)(x + 3) = 0", "(x + 2)(x − 3) = 0"]),
        Reading(text: "x = −2", alternatives: []),
        Reading(text: "x = −3", alternatives: []),
    ]

    public static let questionReading = Reading(text: "x² + 5x + 6 = 0", alternatives: ["x² + 5x + 6 = 0", "x² + 5x − 6 = 0"])
    public static let transcript = Reading(text: "Why does the other root matter?",
                                           alternatives: ["Why does the other root matter?", "Why does the other route matter?"])
    public static let transferProblem = "x² + 7x + 12 = 0"
    public static let transferReading = "x = −3 or x = −4"
    public static let verifierVersion = "mock-fixture-1"

    public struct Result: Equatable, Sendable {
        public var stepKind: StepKind
        public var verdict: Verdict
        public var reason: ReasonCode
        public var normalized: String?
        public var caption: String
    }

    static func normalize(_ s: String) -> String {
        s.lowercased()
            .replacingOccurrences(of: "−", with: "-").replacingOccurrences(of: "–", with: "-")
            .replacingOccurrences(of: "×", with: "*").replacingOccurrences(of: "²", with: "^2")
            .filter { !$0.isWhitespace }
    }

    /// Evidence for the first fixture (x² + 5x + 6 = 0).
    public static func checkQuadratic(_ text: String) -> Result {
        let n = normalize(text)
        let roots = Set(["-2", "-3"].filter { n.contains("x=\($0)") || n.contains("{\($0)") || n.contains(",\($0)") })
        switch n {
        case "(x-2)(x-3)=0", "(x-2)(x-3)":
            return Result(stepKind: .factorization, verdict: .invalid, reason: .signMismatch, normalized: "(x-2)*(x-3)=0",
                          caption: "Multiply your brackets back out. Which sign does the 5x term get, and which sign does the 6 get?")
        case "(x+2)(x+3)=0", "(x+3)(x+2)=0":
            return Result(stepKind: .factorization, verdict: .valid, reason: .ok, normalized: "(x+2)*(x+3)=0",
                          caption: "Those factors check out. Which values of x make the product zero?")
        case "(x+2)(x-3)=0":
            return Result(stepKind: .factorization, verdict: .invalid, reason: .signMismatch, normalized: "(x+2)*(x-3)=0",
                          caption: "Expand that and compare the middle term with 5x. What do you notice?")
        default:
            break
        }
        if roots.count == 2 {
            return Result(stepKind: .solutionSet, verdict: .valid, reason: .ok, normalized: "{-3,-2}",
                          caption: "That's the complete solution set: x = −2 or x = −3.")
        }
        if roots.count == 1, let r = roots.first {
            let other = r == "-2" ? "−3" : "−2"
            _ = other
            return Result(stepKind: .candidateRoot, verdict: .partial, reason: .partialRoot, normalized: "x=\(r)",
                          caption: "x = \(r.replacingOccurrences(of: "-", with: "−")) works, so that's one root. A product of two brackets has two ways to be zero. What's the other root?")
        }
        return Result(stepKind: .equivalentEquation, verdict: .unknown, reason: .unsupported, normalized: nil,
                      caption: "I can't check that step yet, so I won't mark it right or wrong. Can you write it another way?")
    }

    /// Evidence for the transfer item (x² + 7x + 12 = 0).
    public static func checkTransfer(_ text: String) -> Result {
        let n = normalize(text)
        let found = ["-3", "-4"].filter { n.contains($0) }
        let wrong = ["3", "4"].contains { n.contains("x=\($0)") }
        if found.count == 2 && !wrong {
            return Result(stepKind: .solutionSet, verdict: .valid, reason: .ok, normalized: "{-4,-3}",
                          caption: "Correct, and you did that one without any help.")
        }
        if found.count == 1 && !wrong {
            return Result(stepKind: .solutionSet, verdict: .partial, reason: .partialRoot, normalized: nil,
                          caption: "That's one root. What's the other?")
        }
        return Result(stepKind: .solutionSet, verdict: .invalid, reason: .signMismatch, normalized: nil,
                      caption: "Not quite. Substitute your answer back in and check the signs.")
    }

    // MARK: Geometry

    static func ellipse(around r: CGRect, points n: Int = 32) -> [CGPoint] {
        let e = r.insetBy(dx: -10, dy: -8)
        return (0...n).map { i in
            let t = Double(i) / Double(n) * 2 * .pi
            return CGPoint(x: e.midX + e.width / 2 * cos(t), y: e.midY + e.height / 2 * sin(t))
        }
    }

    /// y = x² + 5x + 6 on x ∈ [−5, 0], with both roots marked.
    static func parabolaScene(id: String, in region: CGRect) -> TutorScene {
        let inner = region.insetBy(dx: 24, dy: 28)
        let xr = (-5.0, 0.5), yr = (-1.0, 6.0)
        func map(_ x: Double, _ y: Double) -> CGPoint {
            CGPoint(x: inner.minX + (x - xr.0) / (xr.1 - xr.0) * inner.width,
                    y: inner.maxY - (y - yr.0) / (yr.1 - yr.0) * inner.height)
        }
        let curve = stride(from: -5.0, through: 0.0, by: 0.125).map { map($0, $0 * $0 + 5 * $0 + 6) }
        return TutorScene(sceneID: id, title: "y = x² + 5x + 6", region: region, primitives: [
            ScenePrimitive(kind: .axes, points: [map(xr.0, 0), map(xr.1, 0), map(0, yr.0), map(0, yr.1)]),
            ScenePrimitive(kind: .plot, points: curve),
            ScenePrimitive(kind: .point, points: [map(-3, 0)], text: "x = −3"),
            ScenePrimitive(kind: .point, points: [map(-2, 0)], text: "x = −2"),
            ScenePrimitive(kind: .label, points: [map(-4.6, 5.4)], text: "y = x² + 5x + 6"),
        ])
    }

    /// A labelled free-body diagram for the second-subject fixture.
    static func freeBodyScene(id: String, in region: CGRect) -> TutorScene {
        let c = CGPoint(x: region.midX, y: region.midY + 10)
        let box = CGRect(x: c.x - 50, y: c.y - 35, width: 100, height: 70)
        return TutorScene(sceneID: id, title: "Forces on a resting box", region: region, primitives: [
            ScenePrimitive(kind: .segment, points: [CGPoint(x: region.minX + 20, y: box.maxY), CGPoint(x: region.maxX - 20, y: box.maxY)]),
            ScenePrimitive(kind: .box, points: [CGPoint(x: box.minX, y: box.minY), CGPoint(x: box.maxX, y: box.maxY)]),
            ScenePrimitive(kind: .arrow, points: [c, CGPoint(x: c.x, y: c.y - 90)], text: "normal force N"),
            ScenePrimitive(kind: .arrow, points: [c, CGPoint(x: c.x, y: c.y + 90)], text: "weight mg"),
        ])
    }
}
