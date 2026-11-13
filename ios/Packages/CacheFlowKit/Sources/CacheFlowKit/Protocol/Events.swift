import CoreGraphics
import Foundation

// The iPad <-> orchestrator WebSocket contract (01-plan.md, section 6).
// Keep this file and that section in step; both sides sign off changes.

public enum Mode: String, Codable, Sendable { case teach, guide, check }

public enum Phase: String, Codable, Sendable {
    case idle = "IDLE", pose = "POSE", confirm = "CONFIRM", modeSelect = "MODE_SELECT"
    case stepIntro = "STEP_INTRO", stepWrite = "STEP_WRITE", stepCheck = "STEP_CHECK"
    case followUp = "FOLLOW_UP", recap = "RECAP"
    case working = "WORKING", hinting = "HINTING", armStep = "ARM_STEP", solved = "SOLVED"
    case spotMistake = "SPOT_MISTAKE", final = "FINAL", done = "DONE"
}

public enum ArmState: String, Codable, Sendable { case drawing, done, error }
public enum AnswerMode: String, Codable, Sendable { case voice, write, either }

public struct Clip: Codable, Equatable, Sendable {
    public var id: String
    public var url: String
    public var caption: String

    public init(id: String, url: String, caption: String) { self.id = id; self.url = url; self.caption = caption }
}

public struct ArmJob: Decodable, Equatable, Sendable {
    public var job: String
    /// Polylines in board-space points.
    public var strokes: [[CGPoint]]
    public var estimatedSeconds: Double

    private enum CodingKeys: String, CodingKey { case job, strokes, estS = "est_s" }

    public init(job: String, strokes: [[CGPoint]], estimatedSeconds: Double) {
        self.job = job; self.strokes = strokes; self.estimatedSeconds = estimatedSeconds
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        job = try c.decode(String.self, forKey: .job)
        estimatedSeconds = try c.decode(Double.self, forKey: .estS)
        strokes = try c.decode([[[Double]]].self, forKey: .strokes).map { line in
            try line.map { pair in
                guard pair.count == 2 else {
                    throw DecodingError.dataCorruptedError(
                        forKey: .strokes, in: c, debugDescription: "Point must be [x, y]")
                }
                return CGPoint(x: pair[0], y: pair[1])
            }
        }
    }
}

public struct ReadBackLine: Decodable, Equatable, Sendable {
    public var index: Int
    public var expr: String
    public var ok: Bool

    public init(index: Int, expr: String, ok: Bool) { self.index = index; self.expr = expr; self.ok = ok }
}

public enum ServerEvent: Decodable, Equatable, Sendable {
    case problem(display: String, kind: String, needsConfirm: Bool)
    case phase(mode: Mode, phase: Phase)
    case step(index: Int, of: Int, slot: Int, display: String, clips: [Clip], arm: ArmJob?)
    case ask(clip: Clip, answer: AnswerMode)
    case tutorSay(caption: String, url: String?)
    case readBack(lines: [ReadBackLine])
    case highlight(lineIndex: Int, bbox: CGRect)
    case arm(job: String, state: ArmState)
    case skills([String: Int])
    /// A type this build doesn't know. Ignored rather than fatal, so a newer
    /// server never takes the demo down.
    case unknown(type: String)

    private enum K: String, CodingKey {
        case type, display, kind, needsConfirm = "needs_confirm", mode, phase
        case index, of, slot, clips, arm, clip, answer, caption, url
        case lines, lineIndex = "line_index", bbox, job, state, skills
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: K.self)
        let type = try c.decode(String.self, forKey: .type)
        switch type {
        case "problem":
            self = .problem(
                display: try c.decode(String.self, forKey: .display),
                kind: try c.decode(String.self, forKey: .kind),
                needsConfirm: try c.decodeIfPresent(Bool.self, forKey: .needsConfirm) ?? false)
        case "phase":
            self = .phase(mode: try c.decode(Mode.self, forKey: .mode), phase: try c.decode(Phase.self, forKey: .phase))
        case "step":
            self = .step(
                index: try c.decode(Int.self, forKey: .index),
                of: try c.decode(Int.self, forKey: .of),
                slot: try c.decode(Int.self, forKey: .slot),
                display: try c.decode(String.self, forKey: .display),
                clips: try c.decode([Clip].self, forKey: .clips),
                arm: try c.decodeIfPresent(ArmJob.self, forKey: .arm))
        case "ask":
            self = .ask(clip: try c.decode(Clip.self, forKey: .clip), answer: try c.decode(AnswerMode.self, forKey: .answer))
        case "tutor_say":
            self = .tutorSay(
                caption: try c.decode(String.self, forKey: .caption),
                url: try c.decodeIfPresent(String.self, forKey: .url))
        case "read_back":
            self = .readBack(lines: try c.decode([ReadBackLine].self, forKey: .lines))
        case "highlight":
            let box = try c.decode([Double].self, forKey: .bbox)
            guard box.count == 4 else {
                throw DecodingError.dataCorruptedError(forKey: .bbox, in: c, debugDescription: "bbox must be [x, y, w, h]")
            }
            self = .highlight(
                lineIndex: try c.decode(Int.self, forKey: .lineIndex),
                bbox: CGRect(x: box[0], y: box[1], width: box[2], height: box[3]))
        case "arm":
            self = .arm(job: try c.decode(String.self, forKey: .job), state: try c.decode(ArmState.self, forKey: .state))
        case "skills":
            self = .skills(try c.decode([String: Int].self, forKey: .skills))
        default:
            self = .unknown(type: type)
        }
    }
}

/// iPad -> server messages.
public enum ClientEvent: Encodable, Equatable, Sendable {
    case confirmProblem(ok: Bool)
    case chooseMode(Mode)
    case audioDone(clip: String)
    case answerText(String)
    case check
    case stuck
    case armTouches(job: String, count: Int)
    case mistakeCircled(lineIndex: Int)
    case clearBoard

    private enum K: String, CodingKey { case type, ok, mode, clip, text, job, count, lineIndex = "line_index" }

    public func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: K.self)
        switch self {
        case .confirmProblem(let ok): try c.encode("confirm_problem", forKey: .type); try c.encode(ok, forKey: .ok)
        case .chooseMode(let m): try c.encode("choose_mode", forKey: .type); try c.encode(m, forKey: .mode)
        case .audioDone(let clip): try c.encode("audio_done", forKey: .type); try c.encode(clip, forKey: .clip)
        case .answerText(let t): try c.encode("answer_text", forKey: .type); try c.encode(t, forKey: .text)
        case .check: try c.encode("check", forKey: .type)
        case .stuck: try c.encode("stuck", forKey: .type)
        case .armTouches(let job, let count):
            try c.encode("arm_touches", forKey: .type); try c.encode(job, forKey: .job); try c.encode(count, forKey: .count)
        case .mistakeCircled(let i): try c.encode("mistake_circled", forKey: .type); try c.encode(i, forKey: .lineIndex)
        case .clearBoard: try c.encode("clear_board", forKey: .type)
        }
    }
}
