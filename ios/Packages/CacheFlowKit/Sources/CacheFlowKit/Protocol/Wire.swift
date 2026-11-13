import CoreGraphics
import Foundation

// Shared value types for the v2 iPad <-> Spark contract (Interface.md).
// Open vocabularies (moves, reason codes, phases...) are string-backed structs
// so a newer server can add values without breaking this build; the closed
// verdict set is a strict enum because correctness claims depend on it.

public enum ProtocolV2 {
    public static let version = 2
}

/// Monotonic revision of the student's board. Every upload, intent, event and
/// annotation carries one; tutor ink and scenes commit only on an exact match.
public typealias BoardRevision = Int

/// A string-backed open enumeration.
public protocol OpenVocabulary: RawRepresentable, Codable, Hashable, Sendable, ExpressibleByStringLiteral
where RawValue == String {
    init(rawValue: String)
}

public extension OpenVocabulary {
    init(stringLiteral value: String) { self.init(rawValue: value) }
    init(from decoder: Decoder) throws { self.init(rawValue: try decoder.singleValueContainer().decode(String.self)) }
    func encode(to encoder: Encoder) throws {
        var c = encoder.singleValueContainer()
        try c.encode(rawValue)
    }
}

public struct LessonPhase: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    /// No problem yet.
    public static let idle: Self = "idle"
    /// A question or board image is being read.
    public static let reading: Self = "reading"
    /// Waiting for the student to accept or correct a read-back.
    public static let confirm: Self = "confirm"
    /// Problem confirmed; waiting for Tutor me.
    public static let ready: Self = "ready"
    /// A tutor turn is being composed or delivered (cancellable).
    public static let tutoring: Self = "tutoring"
    /// The tutor is waiting for the student's work or reply.
    public static let studentTurn: Self = "student_turn"
    /// A fresh, unassisted transfer item.
    public static let independentCheck: Self = "independent_check"
    public static let complete: Self = "complete"
}

public struct TutorMove: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let ask: Self = "ask", hint: Self = "hint", explain: Self = "explain"
    public static let writeStep: Self = "write_step", draw: Self = "draw", showAnswer: Self = "show_answer"
    public static let clarify: Self = "clarify", recap: Self = "recap"
}

public struct IntentAction: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let tutor: Self = "tutor", check: Self = "check", hint: Self = "hint"
    public static let explainStep: Self = "explain_step", draw: Self = "draw", showAnswer: Self = "show_answer"
    public static let ask: Self = "ask", cancel: Self = "cancel", quizAnswer: Self = "quiz_answer"

    /// Actions whose answer depends on the student's current board.
    public var needsBoardSnapshot: Bool { [.check, .hint, .explainStep, .draw, .showAnswer].contains(self) }
}

public struct StepKind: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let equivalentEquation: Self = "equivalent_equation", factorization: Self = "factorization"
    public static let expressionIdentity: Self = "expression_identity", candidateRoot: Self = "candidate_root"
    public static let solutionSet: Self = "solution_set", numericResult: Self = "numeric_result"
    public static let explanation: Self = "explanation"
}

public enum Verdict: String, Codable, Sendable, Equatable {
    case valid, invalid, partial, unknown
}

public struct ReasonCode: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let ok: Self = "ok", signMismatch: Self = "sign_mismatch", partialRoot: Self = "partial_root"
    public static let extraneousRoot: Self = "extraneous_root", domainChanged: Self = "domain_changed"
    public static let ambiguousInput: Self = "ambiguous_input", unsupported: Self = "unsupported", timeout: Self = "timeout"
}

/// How much a claim is backed. Only `verified` may be presented as checked.
public struct EvidenceLabel: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let verified: Self = "verified", supportedBySource: Self = "supported_by_source"
    public static let notVerified: Self = "not_verified"
}

public struct ReadBackSource: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let question: Self = "question", board: Self = "board", transcript: Self = "transcript"
}

public struct InkStyle: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let write: Self = "write", highlight: Self = "highlight", circle: Self = "circle"
}

// MARK: Geometry on the wire

enum WireGeometry {
    static func rect(_ a: [Double], codingPath: [CodingKey]) throws -> CGRect {
        guard a.count == 4, a.allSatisfy(\.isFinite), a[2] >= 0, a[3] >= 0 else {
            throw DecodingError.dataCorrupted(.init(codingPath: codingPath, debugDescription: "rect must be [x, y, w, h]"))
        }
        return CGRect(x: a[0], y: a[1], width: a[2], height: a[3])
    }

    static func array(_ r: CGRect) -> [Double] { [r.minX, r.minY, r.width, r.height] }

    static func polylines(_ a: [[[Double]]], codingPath: [CodingKey]) throws -> [[CGPoint]] {
        try a.map { line in try line.map { try point($0, codingPath: codingPath) } }
    }

    static func point(_ p: [Double], codingPath: [CodingKey]) throws -> CGPoint {
        guard p.count == 2, p.allSatisfy(\.isFinite) else {
            throw DecodingError.dataCorrupted(.init(codingPath: codingPath, debugDescription: "point must be [x, y]"))
        }
        return CGPoint(x: p[0], y: p[1])
    }

    static func arrays(_ lines: [[CGPoint]]) -> [[[Double]]] { lines.map { $0.map { [$0.x, $0.y] } } }
}

/// A board-space region, optionally tied to a student line.
public struct Region: Codable, Equatable, Sendable {
    public var lineIndex: Int?
    public var bbox: CGRect

    public init(lineIndex: Int? = nil, bbox: CGRect) { self.lineIndex = lineIndex; self.bbox = bbox }

    private enum K: String, CodingKey { case lineIndex = "line_index", bbox }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        lineIndex = try c.decodeIfPresent(Int.self, forKey: .lineIndex)
        bbox = try WireGeometry.rect(try c.decode([Double].self, forKey: .bbox), codingPath: c.codingPath)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encodeIfPresent(lineIndex, forKey: .lineIndex)
        try c.encode(WireGeometry.array(bbox), forKey: .bbox)
    }
}
