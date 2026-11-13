import CoreGraphics
import Foundation

/// An in-process stand-in for the orchestrator: same `TutorAPI` and
/// `EventStream` contracts, scripted lesson for x² + 5x + 6 = 0. Lets the iPad
/// be built and demoed with no Spark. Strokes are placeholder scribbles; the
/// real backend sends Hershey-font polylines.
public actor MockServer: TutorAPI, EventStream {
    private struct Step { var display: String; var intent: String; var explain: String; var question: String }

    private static let lesson: [Step] = [
        Step(display: "x² + 5x + 6 = 0", intent: "Let's start by writing the equation.",
             explain: "This is a quadratic, so we will try to factor it.", question: "What kind of equation is this?"),
        Step(display: "2 × 3 = 6, 2 + 3 = 5", intent: "Now we look for two numbers.",
             explain: "They multiply to six and add to five. Two and three work.", question: "What do two and three multiply to?"),
        Step(display: "(x + 2)(x + 3) = 0", intent: "Next, we write the factors.",
             explain: "Each number goes in its own bracket with x.", question: "Why plus two and plus three, and not minus?"),
        Step(display: "x + 2 = 0 or x + 3 = 0", intent: "If a product is zero, one factor is zero.",
             explain: "So we set each bracket equal to zero.", question: "Why can we split it into two equations?"),
        Step(display: "x = −2 or x = −3", intent: "Finally, we solve each one.",
             explain: "Subtract from both sides to isolate x.", question: "What is x in the first equation?"),
    ]

    private enum Stage { case idle, writing, asking }

    private let layout: BoardLayout
    private let latency: Duration
    private var continuation: AsyncStream<StreamItem>.Continuation?
    private var mode: Mode = .teach
    private var stage: Stage = .idle
    private var step = 0
    private var waitingClips: Set<String> = []
    private var armFinished = false
    private var lines: [LineUpload] = []
    private var skills: [String: Int] = ["factor_pair": 1, "signs": 0]
    private var problem: (display: String, kind: String, needsConfirm: Bool)?
    private var phase: Phase = .idle

    public init(layout: BoardLayout, latency: Duration = .milliseconds(600)) {
        self.layout = layout
        self.latency = latency
    }

    // MARK: EventStream

    public func events(sessionID: String) -> AsyncStream<StreamItem> {
        let (stream, c) = AsyncStream<StreamItem>.makeStream()
        continuation = c
        c.yield(.state(.connected))
        // Like the real server: replay the current state on every (re)connect.
        if let problem {
            emit(.problem(display: problem.display, kind: problem.kind, needsConfirm: problem.needsConfirm))
            emit(.phase(mode: mode, phase: phase))
            emit(.skills(skills))
            if phase == .modeSelect { emit(.tutorSay(caption: "Want me to teach it, or will you try it yourself?", url: "mock://say")) }
        }
        return stream
    }

    public func close() { continuation?.finish(); continuation = nil }

    public func send(_ event: ClientEvent) async {
        switch event {
        case .confirmProblem(let ok):
            if ok {
                emit(.phase(mode: mode, phase: .modeSelect))
                emit(.tutorSay(caption: "Want me to teach it, or will you try it yourself?", url: "mock://say"))
            } else {
                emit(.phase(mode: mode, phase: .pose))
            }
        case .chooseMode(let m):
            mode = m
            switch m {
            case .teach: begin(at: 0)
            case .guide:
                emit(.phase(mode: .guide, phase: .working))
                emit(.tutorSay(caption: "Go ahead and work it out. Tap Check when you want me to look.", url: "mock://say"))
            case .check: beginSpotMistake()
            }
        case .audioDone(let clip):
            waitingClips.remove(clip)
            advanceIfReady()
        case .answerText(let text): judge(text)
        case .check: checkLines()
        case .stuck: begin(at: 0, mode: .teach)
        case .mistakeCircled(let line):
            let right = line == 3
            emit(.phase(mode: .check, phase: .final))
            emit(.tutorSay(caption: right ? "Yes, that bracket has the wrong sign. Nicely spotted!"
                                          : "Not quite. Look at the factors again.", url: "mock://say"))
            if right { bump("signs"); emit(.phase(mode: .check, phase: .done)) }
        case .armTouches, .clearBoard: break
        }
    }

    // MARK: TutorAPI

    public func createSession(problemID: String?) async throws -> String {
        step = 0; stage = .idle; lines = []; problem = nil; phase = .idle
        if let preset = Preset.all.first(where: { $0.id == problemID }) {
            problem = (preset.display, preset.kind, false)
            phase = .modeSelect
        }
        return "mock-session"
    }

    public func submitQuestion(sessionID: String, png: Data) async throws {
        Task {
            try? await Task.sleep(for: latency)
            self.pose("x² + 5x + 6 = 0", kind: "quadratic", needsConfirm: true)
        }
    }

    public func submitLines(sessionID: String, lines: [LineUpload]) async throws { self.lines = lines }

    public func submitAudio(sessionID: String, wav: Data) async throws -> String {
        let transcript = "plus two and plus three"
        Task { await self.send(.answerText(transcript)) }
        return transcript
    }

    public func health() async throws -> HealthReport {
        HealthReport(services: [
            "llm": ServiceStatus(ok: true, latencyMs: 180), "vlm": ServiceStatus(ok: true, latencyMs: 420),
            "asr": ServiceStatus(ok: true, latencyMs: 90), "tts": ServiceStatus(ok: true, latencyMs: 140),
            "arm": ServiceStatus(ok: false, detail: "mock: no arm attached"),
        ])
    }

    public func admin(_ action: String, value: String?) async throws {
        switch action {
        case "skip_step": step += 1; begin(at: step)
        case "reset": stage = .idle; step = 0; emit(.phase(mode: mode, phase: .idle))
        default: break
        }
    }

    // MARK: Script

    private func pose(_ display: String, kind: String, needsConfirm: Bool) {
        problem = (display, kind, needsConfirm)
        emit(.problem(display: display, kind: kind, needsConfirm: needsConfirm))
        emit(.phase(mode: mode, phase: needsConfirm ? .confirm : .modeSelect))
        if !needsConfirm { emit(.tutorSay(caption: "Want me to teach it, or will you try it yourself?", url: "mock://say")) }
    }

    private func begin(at index: Int, mode m: Mode? = nil) {
        if let m { mode = m }
        step = index
        runStep()
    }

    private func runStep() {
        guard step < Self.lesson.count else { return recap() }
        let s = Self.lesson[step]
        let n = step + 1
        stage = .writing
        armFinished = false
        waitingClips = ["s\(n)i", "s\(n)e"]
        let slot = layout.slots[min(step, layout.slots.count - 1)]
        let strokes = Self.scribble(s.display, in: slot)
        let est = max(2.0, Double(s.display.count) * 0.2)
        let job = ArmJob(job: "j\(n)", strokes: strokes, estimatedSeconds: est)
        emit(.phase(mode: .teach, phase: .stepWrite))
        emit(.step(index: n, of: Self.lesson.count, slot: n, display: s.display,
                   clips: [Clip(id: "s\(n)i", url: "mock://s\(n)i", caption: s.intent),
                           Clip(id: "s\(n)e", url: "mock://s\(n)e", caption: s.explain)], arm: job))
        emit(.arm(job: job.job, state: .drawing))
        Task {
            try? await Task.sleep(for: .seconds(est))
            self.finishArm(job: job.job)
        }
    }

    private func finishArm(job: String) {
        emit(.arm(job: job, state: .done))
        armFinished = true
        advanceIfReady()
    }

    private func advanceIfReady() {
        guard stage == .writing, armFinished, waitingClips.isEmpty else { return }
        if mode == .check { return }
        stage = .asking
        let n = step + 1
        emit(.phase(mode: .teach, phase: .stepCheck))
        emit(.ask(clip: Clip(id: "s\(n)c", url: "mock://s\(n)c", caption: Self.lesson[step].question), answer: .either))
    }

    private func judge(_ text: String) {
        guard stage == .asking else { return }
        let ok = !text.trimmingCharacters(in: .whitespaces).isEmpty
        emit(.phase(mode: .teach, phase: ok ? .stepIntro : .followUp))
        emit(.tutorSay(caption: ok ? "Exactly." : "Not quite, but that's fine. Let me explain and we'll carry on.", url: "mock://say"))
        if ok { bump("factor_pair") }
        stage = .idle
        step += 1
        Task {
            try? await Task.sleep(for: .seconds(1.5))
            self.runStep()
        }
    }

    private func recap() {
        stage = .idle
        emit(.phase(mode: .teach, phase: .recap))
        emit(.tutorSay(caption: "That's the whole solution. Want to try a similar one yourself?", url: "mock://say"))
    }

    private func beginSpotMistake() {
        // The arm writes a solution with one deliberate error, then the student circles it.
        let lines = ["x² + 5x + 6 = 0", "2 × 3 = 6, 2 + 3 = 5", "(x − 2)(x − 3) = 0", "x = 2 or x = 3"]
        emit(.phase(mode: .check, phase: .stepWrite))
        emit(.tutorSay(caption: "I'll write out a solution. Watch closely.", url: "mock://say"))
        Task {
            // The arm writes the lines one after another, as the real one does.
            for (i, text) in lines.enumerated() {
                let job = ArmJob(job: "c\(i + 1)", strokes: Self.scribble(text, in: layout.slots[i]), estimatedSeconds: 1.5)
                emit(.step(index: i + 1, of: lines.count, slot: i + 1, display: text, clips: [], arm: job))
                emit(.arm(job: job.job, state: .drawing))
                try? await Task.sleep(for: .seconds(1.5))
                emit(.arm(job: job.job, state: .done))
            }
            emit(.phase(mode: .check, phase: .spotMistake))
            emit(.tutorSay(caption: "One of these lines has a mistake. Circle it.", url: "mock://say"))
        }
    }

    private func checkLines() {
        guard !lines.isEmpty else {
            emit(.tutorSay(caption: "I don't see any working yet. Write a line and tap Check.", url: "mock://say"))
            return
        }
        let read = lines.map { ReadBackLine(index: $0.index, expr: "line \($0.index + 1)", ok: $0.index != 1) }
        emit(.readBack(lines: read))
        if let bad = lines.first(where: { $0.index == 1 }) {
            emit(.highlight(lineIndex: bad.index, bbox: bad.bbox))
            emit(.tutorSay(caption: "Look at this line. What do two numbers need to multiply to?", url: "mock://say"))
        } else {
            emit(.tutorSay(caption: "That looks right so far. Keep going!", url: "mock://say"))
        }
    }

    private func bump(_ skill: String) {
        skills[skill] = min((skills[skill] ?? 0) + 1, 5)
        emit(.skills(skills))
    }

    private func emit(_ event: ServerEvent) {
        if case .phase(let m, let p) = event { mode = m; phase = p }
        continuation?.yield(.event(event))
    }

    /// Placeholder handwriting: a wave per character, scaled to fit the slot.
    static func scribble(_ text: String, in slot: CGRect) -> [[CGPoint]] {
        let glyphs = Array(text.filter { !$0.isWhitespace })
        guard !glyphs.isEmpty else { return [] }
        let height = slot.height * 0.6
        let width = min(height * 0.6, (slot.width - 24) / CGFloat(glyphs.count))
        let top = slot.midY - height / 2
        return glyphs.enumerated().map { i, _ in
            let x0 = slot.minX + 12 + CGFloat(i) * width
            return (0...8).map { k in
                let t = CGFloat(k) / 8
                return CGPoint(x: x0 + width * 0.8 * (k % 2 == 0 ? 0.1 : 0.9), y: top + height * t)
            }
        }
    }
}
