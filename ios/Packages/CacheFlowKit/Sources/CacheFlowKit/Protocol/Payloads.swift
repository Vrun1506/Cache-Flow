import CoreGraphics
import Foundation

// Payloads of the v2 stream events (Interface.md "v2 stream"). Each payload is
// nested under "payload" in a sequenced envelope; see ServerEnvelope.

extension KeyedDecodingContainer {
    func list<T: Decodable>(_ key: Key) throws -> [T] { try decodeIfPresent([T].self, forKey: key) ?? [] }
    func flag(_ key: Key) throws -> Bool { try decodeIfPresent(Bool.self, forKey: key) ?? false }
}

public struct ProblemInfo: Codable, Equatable, Sendable {
    public var text: String
    public var subject: String
    public var confirmed: Bool
    /// "lesson" or "independent_check".
    public var item: String

    public init(text: String, subject: String = "algebra", confirmed: Bool, item: String = "lesson") {
        self.text = text; self.subject = subject; self.confirmed = confirmed; self.item = item
    }
}

/// `state`: replaces view state on connect, reconnect and phase changes.
public struct LessonSnapshot: Codable, Equatable, Sendable {
    public var phase: LessonPhase
    public var problem: ProblemInfo?
    public var latestTurnID: String?
    public var skills: [SkillLevel]

    public init(phase: LessonPhase, problem: ProblemInfo?, latestTurnID: String?, skills: [SkillLevel]) {
        self.phase = phase; self.problem = problem; self.latestTurnID = latestTurnID; self.skills = skills
    }
    private enum K: String, CodingKey { case phase, problem, latestTurnID = "latest_turn_id", skills }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        phase = try c.decode(LessonPhase.self, forKey: .phase)
        problem = try c.decodeIfPresent(ProblemInfo.self, forKey: .problem)
        latestTurnID = try c.decodeIfPresent(String.self, forKey: .latestTurnID)
        skills = try c.list(.skills)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(phase, forKey: .phase)
        try c.encodeIfPresent(problem, forKey: .problem)
        try c.encodeIfPresent(latestTurnID, forKey: .latestTurnID)
        try c.encode(skills, forKey: .skills)
    }
}

/// `read_back`: a candidate reading the student must accept or correct.
public struct ReadBack: Codable, Equatable, Sendable {
    public var requestID: String
    public var source: ReadBackSource
    public var text: String
    public var alternatives: [String]
    public var ambiguous: Bool
    public var note: String?
    public var regions: [Region]

    public init(requestID: String, source: ReadBackSource, text: String, alternatives: [String] = [],
                ambiguous: Bool, note: String? = nil, regions: [Region] = []) {
        self.requestID = requestID; self.source = source; self.text = text; self.alternatives = alternatives
        self.ambiguous = ambiguous; self.note = note; self.regions = regions
    }
    private enum K: String, CodingKey { case requestID = "request_id", source, text, alternatives, ambiguous, note, regions }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        requestID = try c.decode(String.self, forKey: .requestID)
        source = try c.decode(ReadBackSource.self, forKey: .source)
        text = try c.decode(String.self, forKey: .text)
        alternatives = try c.list(.alternatives)
        ambiguous = try c.flag(.ambiguous)
        note = try c.decodeIfPresent(String.self, forKey: .note)
        regions = try c.list(.regions)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(requestID, forKey: .requestID); try c.encode(source, forKey: .source)
        try c.encode(text, forKey: .text); try c.encode(alternatives, forKey: .alternatives)
        try c.encode(ambiguous, forKey: .ambiguous); try c.encodeIfPresent(note, forKey: .note)
        try c.encode(regions, forKey: .regions)
    }
}

/// `evidence`: what was checked. Only `label == .verified` may be shown as checked.
public struct Evidence: Codable, Equatable, Sendable {
    public var requestID: String?
    public var evidenceID: String
    public var subject: String
    public var stepKind: StepKind
    public var verdict: Verdict
    public var reasonCode: ReasonCode
    public var label: EvidenceLabel
    public var verifierVersion: String
    public var normalizedStep: String?
    public var lineIndex: Int?
    public var highlights: [Region]
    public var sourceIDs: [String]
    /// True only when the work was done without assistance on this item.
    public var independent: Bool

    public init(requestID: String?, evidenceID: String, subject: String = "algebra", stepKind: StepKind, verdict: Verdict,
                reasonCode: ReasonCode, label: EvidenceLabel, verifierVersion: String, normalizedStep: String? = nil,
                lineIndex: Int? = nil, highlights: [Region] = [], sourceIDs: [String] = [], independent: Bool = false) {
        self.requestID = requestID; self.evidenceID = evidenceID; self.subject = subject; self.stepKind = stepKind
        self.verdict = verdict; self.reasonCode = reasonCode; self.label = label; self.verifierVersion = verifierVersion
        self.normalizedStep = normalizedStep; self.lineIndex = lineIndex; self.highlights = highlights
        self.sourceIDs = sourceIDs; self.independent = independent
    }
    private enum K: String, CodingKey {
        case requestID = "request_id", evidenceID = "evidence_id", subject, stepKind = "step_kind", verdict
        case reasonCode = "reason_code", label, verifierVersion = "verifier_version", normalizedStep = "normalized_step"
        case lineIndex = "line_index", highlights, sourceIDs = "source_ids", independent
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        requestID = try c.decodeIfPresent(String.self, forKey: .requestID)
        evidenceID = try c.decode(String.self, forKey: .evidenceID)
        subject = try c.decodeIfPresent(String.self, forKey: .subject) ?? "algebra"
        stepKind = try c.decode(StepKind.self, forKey: .stepKind)
        verdict = try c.decode(Verdict.self, forKey: .verdict)
        reasonCode = try c.decode(ReasonCode.self, forKey: .reasonCode)
        // A missing label never upgrades a claim to "verified".
        label = try c.decodeIfPresent(EvidenceLabel.self, forKey: .label) ?? .notVerified
        verifierVersion = try c.decode(String.self, forKey: .verifierVersion)
        normalizedStep = try c.decodeIfPresent(String.self, forKey: .normalizedStep)
        lineIndex = try c.decodeIfPresent(Int.self, forKey: .lineIndex)
        highlights = try c.list(.highlights)
        sourceIDs = try c.list(.sourceIDs)
        independent = try c.flag(.independent)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encodeIfPresent(requestID, forKey: .requestID); try c.encode(evidenceID, forKey: .evidenceID)
        try c.encode(subject, forKey: .subject); try c.encode(stepKind, forKey: .stepKind)
        try c.encode(verdict, forKey: .verdict); try c.encode(reasonCode, forKey: .reasonCode)
        try c.encode(label, forKey: .label); try c.encode(verifierVersion, forKey: .verifierVersion)
        try c.encodeIfPresent(normalizedStep, forKey: .normalizedStep); try c.encodeIfPresent(lineIndex, forKey: .lineIndex)
        try c.encode(highlights, forKey: .highlights); try c.encode(sourceIDs, forKey: .sourceIDs)
        try c.encode(independent, forKey: .independent)
    }

    /// Plain-language summary; "unknown" is explicitly ungraded.
    public var summary: String {
        switch verdict {
        case .valid: label == .verified ? "Checked: correct" : "Looks right (not verified)"
        case .invalid: "Not quite" + (reasonCode == .signMismatch ? ": check the signs" : "")
        case .partial: reasonCode == .partialRoot ? "Partly there: one root found" : "Partly there"
        case .unknown: reasonCode == .ambiguousInput ? "Not graded: please confirm what you wrote" : "Not graded"
        }
    }
}

/// `tutor_turn`: one pedagogical move. The caption shows immediately.
public struct TutorTurn: Codable, Equatable, Sendable {
    public var move: TutorMove
    public var caption: String
    public var audioURL: String?
    public var evidenceIDs: [String]
    public var assisted: Bool
    public var highlights: [Region]

    public init(move: TutorMove, caption: String, audioURL: String? = nil, evidenceIDs: [String] = [],
                assisted: Bool = false, highlights: [Region] = []) {
        self.move = move; self.caption = caption; self.audioURL = audioURL; self.evidenceIDs = evidenceIDs
        self.assisted = assisted; self.highlights = highlights
    }
    private enum K: String, CodingKey {
        case move, caption, audioURL = "audio_url", evidenceIDs = "evidence_ids", assisted, highlights
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        move = try c.decode(TutorMove.self, forKey: .move)
        caption = try c.decode(String.self, forKey: .caption)
        audioURL = try c.decodeIfPresent(String.self, forKey: .audioURL)
        evidenceIDs = try c.list(.evidenceIDs)
        assisted = try c.flag(.assisted)
        highlights = try c.list(.highlights)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(move, forKey: .move); try c.encode(caption, forKey: .caption)
        try c.encodeIfPresent(audioURL, forKey: .audioURL); try c.encode(evidenceIDs, forKey: .evidenceIDs)
        try c.encode(assisted, forKey: .assisted); try c.encode(highlights, forKey: .highlights)
    }
}

/// `tutor_ink_preview`: bounded strokes and/or a typeset formula for the tutor layer.
public struct TutorInk: Codable, Equatable, Sendable {
    public static let maxStrokes = 256
    public static let maxPointsPerStroke = 512
    public static let maxFormulaLength = 200

    public var inkID: String
    public var style: InkStyle
    public var region: CGRect
    public var strokes: [[CGPoint]]
    public var formula: String?

    public init(inkID: String, style: InkStyle = .write, region: CGRect, strokes: [[CGPoint]] = [], formula: String? = nil) {
        self.inkID = inkID; self.style = style; self.region = region; self.strokes = strokes; self.formula = formula
    }
    private enum K: String, CodingKey { case inkID = "ink_id", style, region, strokes, formula }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        inkID = try c.decode(String.self, forKey: .inkID)
        style = try c.decodeIfPresent(InkStyle.self, forKey: .style) ?? .write
        region = try WireGeometry.rect(try c.decode([Double].self, forKey: .region), codingPath: c.codingPath)
        strokes = try WireGeometry.polylines(try c.list(.strokes), codingPath: c.codingPath)
        formula = try c.decodeIfPresent(String.self, forKey: .formula)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(inkID, forKey: .inkID); try c.encode(style, forKey: .style)
        try c.encode(WireGeometry.array(region), forKey: .region)
        try c.encode(WireGeometry.arrays(strokes), forKey: .strokes)
        try c.encodeIfPresent(formula, forKey: .formula)
    }
}

/// `tutor_ink_commit` / `scene_commit` payload.
public struct CommitRef: Codable, Equatable, Sendable {
    public var id: String
    public init(id: String) { self.id = id }
}

public struct SceneKind: OpenVocabulary {
    public let rawValue: String
    public init(rawValue: String) { self.rawValue = rawValue }
    public static let axes: Self = "axes", plot: Self = "plot", point: Self = "point", label: Self = "label"
    public static let arrow: Self = "arrow", segment: Self = "segment", box: Self = "box", numberLine: Self = "number_line"
    /// The only primitives the iPad will render. Anything else rejects the scene.
    public static let approved: Set<SceneKind> = [.axes, .plot, .point, .label, .arrow, .segment, .box, .numberLine]
}

/// One semantic drawing primitive in board space. Never executable code.
public struct ScenePrimitive: Codable, Equatable, Sendable {
    public var kind: SceneKind
    public var points: [CGPoint]
    public var text: String?

    public init(kind: SceneKind, points: [CGPoint], text: String? = nil) { self.kind = kind; self.points = points; self.text = text }
    private enum K: String, CodingKey { case kind, points, text }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        kind = try c.decode(SceneKind.self, forKey: .kind)
        points = try (try c.list(.points) as [[Double]]).map { try WireGeometry.point($0, codingPath: c.codingPath) }
        text = try c.decodeIfPresent(String.self, forKey: .text)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(kind, forKey: .kind)
        try c.encode(points.map { [$0.x, $0.y] }, forKey: .points)
        try c.encodeIfPresent(text, forKey: .text)
    }
}

/// `scene_preview`: a bounded diagram or plot for the tutor layer.
public struct TutorScene: Codable, Equatable, Sendable {
    public static let maxPrimitives = 64
    public static let maxPoints = 1024

    public var sceneID: String
    public var title: String
    public var region: CGRect
    public var primitives: [ScenePrimitive]

    public init(sceneID: String, title: String, region: CGRect, primitives: [ScenePrimitive]) {
        self.sceneID = sceneID; self.title = title; self.region = region; self.primitives = primitives
    }
    private enum K: String, CodingKey { case sceneID = "scene_id", title, region, primitives }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        sceneID = try c.decode(String.self, forKey: .sceneID)
        title = try c.decodeIfPresent(String.self, forKey: .title) ?? ""
        region = try WireGeometry.rect(try c.decode([Double].self, forKey: .region), codingPath: c.codingPath)
        primitives = try c.list(.primitives)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(sceneID, forKey: .sceneID); try c.encode(title, forKey: .title)
        try c.encode(WireGeometry.array(region), forKey: .region); try c.encode(primitives, forKey: .primitives)
    }
}

/// `audio_chunk`: optional streamed speech for a turn.
public struct AudioChunk: Codable, Equatable, Sendable {
    public var chunkIndex: Int
    public var audioURL: String
    public var caption: String
    public var isFinal: Bool

    public init(chunkIndex: Int, audioURL: String, caption: String, isFinal: Bool) {
        self.chunkIndex = chunkIndex; self.audioURL = audioURL; self.caption = caption; self.isFinal = isFinal
    }
    private enum K: String, CodingKey { case chunkIndex = "chunk_index", audioURL = "audio_url", caption = "caption_span", isFinal = "final" }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        chunkIndex = try c.decode(Int.self, forKey: .chunkIndex)
        audioURL = try c.decode(String.self, forKey: .audioURL)
        caption = try c.decodeIfPresent(String.self, forKey: .caption) ?? ""
        isFinal = try c.flag(.isFinal)
    }
    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        try c.encode(chunkIndex, forKey: .chunkIndex); try c.encode(audioURL, forKey: .audioURL)
        try c.encode(caption, forKey: .caption); try c.encode(isFinal, forKey: .isFinal)
    }
}

/// `agent_trace`: one actual local component call (no raw student ink/audio).
public struct AgentTrace: Codable, Equatable, Sendable {
    public var component: String
    public var action: String
    public var outcome: String
    public var latencyMs: Int
    public var evidenceID: String?

    public init(component: String, action: String, outcome: String, latencyMs: Int, evidenceID: String? = nil) {
        self.component = component; self.action = action; self.outcome = outcome; self.latencyMs = latencyMs; self.evidenceID = evidenceID
    }
    private enum CodingKeys: String, CodingKey { case component, action, outcome, latencyMs = "latency_ms", evidenceID = "evidence_id" }
}

/// Evidence-backed skill level. Helped work never raises `level`.
public struct SkillLevel: Codable, Equatable, Sendable {
    public var skill: String
    public var label: String
    public var level: Int
    public var independentEvidence: Int
    public var assisted: Bool

    public init(skill: String, label: String, level: Int, independentEvidence: Int, assisted: Bool) {
        self.skill = skill; self.label = label; self.level = level; self.independentEvidence = independentEvidence; self.assisted = assisted
    }
    private enum CodingKeys: String, CodingKey {
        case skill, label, level, independentEvidence = "independent_evidence", assisted
    }
}

public struct ServerError: Codable, Equatable, Sendable {
    public var requestID: String?
    public var code: String
    public var message: String
    public var retryable: Bool
    public var latestBoardRevision: BoardRevision?

    public init(requestID: String?, code: String, message: String, retryable: Bool, latestBoardRevision: BoardRevision? = nil) {
        self.requestID = requestID; self.code = code; self.message = message; self.retryable = retryable
        self.latestBoardRevision = latestBoardRevision
    }
    private enum CodingKeys: String, CodingKey {
        case requestID = "request_id", code, message, retryable, latestBoardRevision = "latest_board_revision"
    }
}

/// `turn_cancelled` (proposed additive event): the cancellation boundary.
public struct TurnCancelled: Codable, Equatable, Sendable {
    public var reason: String
    public init(reason: String) { self.reason = reason }
}
