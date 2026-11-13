import CacheFlowKit
import Foundation
import Observation

struct Highlight: Identifiable, Equatable {
    let id = UUID()
    var lineIndex: Int
    var rect: CGRect
}

struct ArmStatus: Equatable {
    var job: String
    var state: ArmState
}

/// Ghost ink: the arm's planned strokes, either as a faint dotted preview while
/// the arm draws, or animated in blue when the arm didn't ink (the fallback).
struct Ghost: Equatable {
    enum Kind { case preview, playback }
    var job: String
    var strokes: [[CGPoint]]
    var kind: Kind
    var started = Date()
    var duration: Double
}

/// Everything the views render. Server events fold into it through `apply`;
/// side effects (audio, ghost playback, replies) live in `TutorClient`.
@MainActor @Observable
final class SessionModel {
    var mode: Mode?
    var phase: Phase = .idle
    var problem: String?
    var needsConfirm = false
    var stepIndex = 0
    var stepTotal = 0
    var caption = "Hi! I'm Socrates. Write a problem in the box, or pick one."
    var speaking = false
    var ask: Clip?
    var answerMode: AnswerMode = .either
    var readBack: [ReadBackLine] = []
    var lineRects: [Int: CGRect] = [:]
    var highlight: Highlight?
    var skills: [String: Int] = [:]
    var arm: ArmStatus?
    /// Ghost ink in flight, at most one per arm job.
    var ghosts: [Ghost] = []
    var connection: ConnectionState = .offline
    var health: HealthReport?
    var calibration: CalibrationLog?
    var recording = false
    var busy = false

    var penInput: PenInput { CanvasPolicy.input(for: phase) }
    var inkOwner: InkOwner { CanvasPolicy.ink(for: phase) }
    var acceptsQuestion: Bool { [.idle, .pose, .confirm, .modeSelect].contains(phase) }

    func apply(_ event: ServerEvent, slotRects: (Int) -> CGRect?) {
        switch event {
        case .problem(let display, _, let confirm):
            problem = display
            needsConfirm = confirm
        case .phase(let m, let p):
            mode = m
            phase = p
            if p == .idle { resetLesson() }
            if p != .stepCheck && p != .followUp { ask = nil }
        case .step(let index, let of, _, _, _, let job):
            stepIndex = index
            stepTotal = of
            if let job {
                arm = ArmStatus(job: job.job, state: .drawing)
                setGhost(Ghost(job: job.job, strokes: job.strokes, kind: .preview, duration: job.estimatedSeconds))
            }
        case .ask(let clip, let answer):
            ask = clip
            answerMode = answer
        case .tutorSay(let text, _):
            caption = text
        case .readBack(let lines):
            readBack = lines
        case .highlight(let line, let bbox):
            highlight = Highlight(lineIndex: line, rect: bbox)
        case .arm(let job, let state):
            if arm?.job == job { arm?.state = state }
            if state != .drawing { ghosts.removeAll { $0.job == job && $0.kind == .preview } }
        case .skills(let s):
            skills = s
        case .unknown:
            break
        }
    }

    func resetLesson() {
        mode = nil
        stepIndex = 0
        stepTotal = 0
        ask = nil
        readBack = []
        lineRects = [:]
        highlight = nil
        arm = nil
        ghosts = []
    }

    func setGhost(_ g: Ghost) {
        ghosts.removeAll { $0.job == g.job }
        ghosts.append(g)
    }
}
