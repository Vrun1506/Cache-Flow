import CoreGraphics
import Foundation

/// An in-process stand-in for the Spark session API that runs the whole v2
/// demo without a Spark: same `TutorAPI` and `EventStream` contracts, every
/// envelope round-tripped through the JSON wire format, deterministic script
/// (`MockLessonScript`) and fault injection for lost audio, a dropped
/// connection and stale ink. It proves the iPad's contract handling only;
/// it is not evidence of any real model, agent, verifier or speech service.
public actor MockServer: TutorAPI, EventStream {
    public struct Timing: Sendable {
        /// Pause between ordinary events in a turn.
        public var step: Duration
        /// Pause in the long `explain_step` turn, so it can be cancelled.
        public var slow: Duration
        /// How long a dropped connection stays down.
        public var reconnect: Duration

        public init(step: Duration, slow: Duration, reconnect: Duration) { self.step = step; self.slow = slow; self.reconnect = reconnect }
        public static let demo = Timing(step: .milliseconds(350), slow: .seconds(3), reconnect: .milliseconds(1500))
        public static let fast = Timing(step: .milliseconds(1), slow: .milliseconds(200), reconnect: .milliseconds(20))
    }

    public enum Fault: String, Sendable, CaseIterable {
        /// The next tutor clip's audio never arrives.
        case loseNextAudio
        /// The next tutor ink preview is computed against an old board revision.
        case staleNextInk
    }

    private enum Pending { case question(String), board(lineIndex: Int, text: String), transcript(String) }
    private enum Step { case emit(ServerEvent), pause(Duration) }

    private let layout: BoardLayout
    private let timing: Timing
    private var sessionCounter = 0
    private var sessionID = ""
    private var seq = 0
    /// Every envelope ever emitted this session, in order (the replay log).
    public private(set) var log: [ServerEnvelope] = []
    /// Every message the iPad sent on the stream.
    public private(set) var received: [ClientEnvelope] = []
    private var continuation: AsyncStream<StreamItem>.Continuation?
    private var connected = false
    private var deliveredSeq: Int?

    private var boardRevision = 0
    private var phase: LessonPhase = .idle
    private var problem: ProblemInfo?
    private var presetID: String?
    private var skills: [SkillLevel] = MockServer.initialSkills
    private var latestTurnID: String?
    private var counters = (turn: 0, ink: 0, scene: 0, evidence: 0)
    private var runner: Task<Void, Never>?
    private var runningTurn: String?
    private var cancelledTurns: Set<String> = []
    private var handledRequests: [String: Accepted] = [:]
    private var pending: [String: Pending] = [:]
    private var lines: [Region] = []
    private var confirmedLines: [Int: String] = [:]
    private var faults: Set<Fault> = []
    private var staleInk: [String: (turn: String, ink: TutorInk)] = [:]
    private var summaryItems: [SessionSummary.Item] = []
    private var assisted = false

    private static let initialSkills = [
        SkillLevel(skill: "factor_quadratic", label: "Factoring", level: 1, independentEvidence: 0, assisted: false),
        SkillLevel(skill: "signs", label: "Signs", level: 0, independentEvidence: 0, assisted: false),
    ]

    public init(layout: BoardLayout, timing: Timing = .demo) {
        self.layout = layout
        self.timing = timing
    }

    // MARK: Test and debug controls

    public func inject(_ fault: Fault) { faults.insert(fault) }

    /// Simulate a network drop: the stream reports reconnecting, events keep
    /// being produced into the log, then the stream resumes after the
    /// cursor (with a small overlap to prove client-side de-duplication).
    public func dropConnection() {
        guard connected else { return }
        connected = false
        continuation?.yield(.state(.reconnecting))
        let delay = timing.reconnect
        Task {
            try? await Task.sleep(for: delay)
            self.resume()
        }
    }

    // MARK: EventStream

    public func events(sessionID: String, afterSeq: Int?) -> AsyncStream<StreamItem> {
        continuation?.finish()
        let (stream, c) = AsyncStream<StreamItem>.makeStream()
        continuation = c
        deliveredSeq = afterSeq
        connected = true
        c.yield(.state(.connected))
        replayAndSnapshot(after: afterSeq)
        return stream
    }

    public func close() {
        connected = false
        continuation?.finish()
        continuation = nil
    }

    public func send(_ message: ClientEnvelope) async {
        guard message.sessionID == sessionID else { return }
        received.append(message)
        switch message.message {
        case let .readBackConfirm(requestID, accepted, text):
            confirm(requestID: requestID, accepted: accepted, text: text, revision: message.boardRevision)
        case let .cancelTurn(turnID, _):
            cancel(turnID: turnID)
        case let .tutorInkAck(_, inkID, placed, reason):
            if !placed, reason == "stale_revision", let s = staleInk.removeValue(forKey: inkID) {
                // Re-evaluate against the current board and re-issue once.
                counters.ink += 1
                var ink = s.ink
                ink.inkID = "ink-\(counters.ink)"
                emit(.tutorInkPreview(ink), turn: s.turn)
                emit(.tutorInkCommit(CommitRef(id: ink.inkID)), turn: s.turn)
            }
        case .audioDone, .sceneAck:
            break
        }
    }

    // MARK: TutorAPI

    public func createSession(presetID: String?, locale: String) async throws -> SessionInfo {
        runner?.cancel()
        sessionCounter += 1
        sessionID = "mock-\(sessionCounter)"
        seq = 0; log = []; received = []; boardRevision = 0; latestTurnID = nil; runningTurn = nil
        counters = (0, 0, 0, 0); cancelledTurns = []; handledRequests = [:]; pending = [:]
        lines = []; confirmedLines = [:]; staleInk = [:]; summaryItems = []; assisted = false
        skills = Self.initialSkills
        self.presetID = presetID
        if let preset = Preset.all.first(where: { $0.id == presetID }) {
            problem = ProblemInfo(text: preset.display, subject: preset.subject, confirmed: true)
            phase = .ready
        } else {
            problem = nil
            phase = .idle
        }
        return SessionInfo(sessionID: sessionID, boardRevision: boardRevision)
    }

    public func submitQuestion(sessionID: String, _ input: QuestionInput, boardRevision rev: BoardRevision,
                               requestID: String) async throws -> Accepted {
        if let done = try admit(sessionID, rev, requestID) { return done }
        switch input {
        case .text(let t):
            problem = ProblemInfo(text: t, subject: "algebra", confirmed: true)
            emit(.state(snapshot(.ready)), request: requestID)
        case .image:
            emit(.state(snapshot(.reading)), request: requestID)
            let r = MockLessonScript.questionReading
            pending[requestID] = .question(r.text)
            let rb = ReadBack(requestID: requestID, source: .question, text: r.text, alternatives: r.alternatives,
                              ambiguous: r.ambiguous, note: "The sign before 6 is unclear.",
                              regions: [Region(bbox: layout.questionBox)])
            schedule(turn: nil, request: requestID, [.pause(timing.step), .emit(.readBack(rb)), .emit(.state(snapshot(.confirm)))])
        }
        return accept(requestID)
    }

    public func submitBoard(sessionID: String, _ snapshot: BoardSnapshot, requestID: String) async throws -> Accepted {
        if let done = try admit(sessionID, snapshot.boardRevision, requestID) { return done }
        guard snapshot.isWithinBounds else { throw TutorAPIError.tooLarge }
        lines = snapshot.lines
        return accept(requestID)
    }

    public func submitAudio(sessionID: String, wav: Data, boardRevision rev: BoardRevision,
                            requestID: String) async throws -> TranscriptCandidate {
        _ = try admit(sessionID, rev, requestID)
        let r = MockLessonScript.transcript
        pending[requestID] = .transcript(r.text)
        _ = accept(requestID)
        emit(.readBack(ReadBack(requestID: requestID, source: .transcript, text: r.text, alternatives: r.alternatives,
                                ambiguous: r.ambiguous, note: "“root” or “route”?")), request: requestID)
        return TranscriptCandidate(requestID: requestID, text: r.text, ambiguous: r.ambiguous)
    }

    public func sendIntent(sessionID: String, _ intent: IntentRequest) async throws -> Accepted {
        if let done = try admit(sessionID, intent.boardRevision, intent.requestID) { return done }
        let r = intent.requestID
        switch intent.action {
        case .tutor: tutorMe(r)
        case .check: check(r)
        case .hint: hint(r)
        case .explainStep: explainStep(r)
        case .draw: draw(r)
        case .showAnswer: showAnswer(r)
        case .ask: ask(intent.text ?? "", request: r)
        case .quizAnswer:
            if let text = intent.text?.trimmingCharacters(in: .whitespacesAndNewlines), !text.isEmpty {
                quiz(text, request: r)
            } else {
                requestTransferAnswer(r)
            }
        case .cancel: if let t = intent.turnID ?? runningTurn { cancel(turnID: t) }
        default:
            emit(.error(ServerError(requestID: r, code: "unsupported_action", message: "That action isn't available yet.",
                                    retryable: false)), request: r)
        }
        return accept(r)
    }

    public func summary(sessionID: String) async throws -> SessionSummary {
        SessionSummary(items: summaryItems, skills: skills,
                       nextStep: summaryItems.contains { $0.independent && $0.outcome == .valid }
                           ? "Practise a quadratic with a negative constant term."
                           : "Try a fresh quadratic without hints.")
    }

    public func deleteSession(sessionID: String) async throws {
        _ = try await createSession(presetID: nil, locale: "en")
    }

    public func health() async throws -> HealthReport {
        let mock = ComponentStatus(ok: true, revision: "mock", latencyMs: 0, detail: "in-process mock, no Spark")
        return HealthReport(components: Dictionary(uniqueKeysWithValues: HealthReport.componentOrder.map { ($0, mock) }))
    }

    // MARK: Request admission (idempotency + revision conflicts)

    private func admit(_ sid: String, _ rev: BoardRevision, _ requestID: String) throws -> Accepted? {
        guard sid == sessionID else { throw TutorAPIError.http(404) }
        if let done = handledRequests[requestID] { return done }
        guard rev >= boardRevision else { throw TutorAPIError.staleRevision(latest: boardRevision) }
        boardRevision = rev
        return nil
    }

    private func accept(_ requestID: String) -> Accepted {
        let a = Accepted(requestID: requestID)
        handledRequests[requestID] = a
        return a
    }

    // MARK: Lesson script

    private var isPhysics: Bool { problem?.subject == "physics" }
    private var itemPhase: LessonPhase { problem?.item == "independent_check" ? .independentCheck : .studentTurn }

    private func tutorMe(_ r: String) {
        guard problem?.confirmed == true else {
            return emit(.error(ServerError(requestID: r, code: "no_problem", message: "Write or pick a problem first.",
                                           retryable: false)), request: r)
        }
        let caption: String
        if isPhysics {
            caption = "Let's think about the box resting on the table. What forces act on it? I can't grade physics automatically, so I'll say when something isn't verified."
        } else if problem?.text == MockLessonScript.questionReading.text {
            caption = "Let's work on x² + 5x + 6 = 0 together. Before you factor, which two numbers multiply to 6 and add to 5?"
        } else {
            caption = "Let's work on \(problem?.text ?? "this") together. What do you notice first?"
        }
        runTurn(request: r) { t in [
            .emit(.agentTrace(AgentTrace(component: "mock-agent", action: "activate_skill:socratic-tutor", outcome: "ok", latencyMs: 0))),
            .emit(.tutorTurn(TutorTurn(move: .ask, caption: caption, audioURL: self.audioURL(t)))),
        ] }
    }

    private func check(_ r: String) {
        if problem?.item == "independent_check" { return requestTransferAnswer(r) }
        guard let last = lines.last, let index = last.lineIndex else {
            return runTurn(request: r) { t in [.emit(.tutorTurn(TutorTurn(
                move: .clarify, caption: "I don't see any working yet. Write a line, then tap Check.", audioURL: self.audioURL(t))))] }
        }
        if isPhysics {
            let e = evidence(r, .explanation, .unknown, .unsupported, label: .notVerified, line: index, highlights: [])
            return runTurn(request: r) { t in [
                .emit(.evidence(e)),
                .emit(.tutorTurn(TutorTurn(move: .ask, caption: "I can't verify physics answers yet, so this isn't graded. Which force balances the weight?",
                                           audioURL: self.audioURL(t), evidenceIDs: [e.evidenceID]))),
            ] }
        }
        let reading = MockLessonScript.boardReadings[min(index, MockLessonScript.boardReadings.count - 1)]
        if let confirmed = confirmedLines[index] { return evaluate(confirmed, region: last, request: r) }
        if reading.ambiguous {
            pending[r] = .board(lineIndex: index, text: reading.text)
            let e = evidence(r, .factorization, .unknown, .ambiguousInput, label: .notVerified, line: index, highlights: [last])
            return runTurn(request: r, endPhase: .confirm) { t in [
                .emit(.readBack(ReadBack(requestID: r, source: .board, text: reading.text, alternatives: reading.alternatives,
                                         ambiguous: true, note: "Is the second sign a plus or a minus?", regions: [last]))),
                .emit(.evidence(e)),
                .emit(.tutorTurn(TutorTurn(move: .clarify,
                                           caption: "I can't tell whether that second sign is a plus or a minus, so I haven't graded it. Is this what you wrote?",
                                           audioURL: self.audioURL(t), evidenceIDs: [e.evidenceID]))),
            ] }
        }
        emit(.readBack(ReadBack(requestID: r, source: .board, text: reading.text, ambiguous: false, regions: [last])), request: r)
        evaluate(reading.text, region: last, request: r)
    }

    private func evaluate(_ text: String, region: Region, request r: String) {
        let res = MockLessonScript.checkQuadratic(text)
        let e = evidence(r, res.stepKind, res.verdict, res.reason, label: res.verdict == .unknown ? .notVerified : .verified,
                         line: region.lineIndex, highlights: res.verdict == .invalid ? [region] : [], normalized: res.normalized)
        let complete = res.verdict == .valid && res.stepKind == .solutionSet
        runTurn(request: r) { t in
            var steps: [Step] = [
                .emit(.agentTrace(AgentTrace(component: "mock-agent", action: "check_math_step", outcome: res.verdict.rawValue,
                                             latencyMs: 0, evidenceID: e.evidenceID))),
                .emit(.evidence(e)),
                .emit(.tutorTurn(TutorTurn(move: complete ? .recap : .ask, caption: res.caption, audioURL: self.audioURL(t),
                                           evidenceIDs: [e.evidenceID], highlights: e.highlights))),
            ]
            if res.verdict == .invalid {
                steps += self.inkSteps(TutorInk(inkID: self.nextInk(), style: .circle, region: region.bbox.insetBy(dx: -14, dy: -12),
                                                strokes: [MockLessonScript.ellipse(around: region.bbox)]), turn: t)
            }
            if complete { self.recordItem(.valid, evidence: e.evidenceID); steps += self.transferSteps(t) }
            return steps
        }
    }

    private func hint(_ r: String) {
        let isTransfer = problem?.item == "independent_check"
        if isTransfer { assisted = true }
        let formula = isPhysics ? "ΣF = 0 when nothing accelerates" : "(x + a)(x + b) = x² + (a + b)x + ab"
        runTurn(request: r) { t in
            [.emit(.tutorTurn(TutorTurn(move: .hint, caption: self.isPhysics
                ? "Hint: if the box isn't accelerating, the forces on it must balance."
                : "Hint: compare your brackets with this pattern. The middle term comes from a + b, the last from a × b.",
                audioURL: self.audioURL(t), assisted: isTransfer)))]
            + self.inkSteps(TutorInk(inkID: self.nextInk(), region: self.layout.slots[0], formula: formula), turn: t)
        }
    }

    private func explainStep(_ r: String) {
        let caption = "If a product is zero, at least one factor is zero. So we can split (x + 2)(x + 3) = 0 into two small equations, one for each bracket."
        runTurn(request: r) { t in
            var steps: [Step] = [
                .emit(.tutorTurn(TutorTurn(move: .explain, caption: caption))),
                .emit(.audioChunk(AudioChunk(chunkIndex: 0, audioURL: self.audioURL(t, chunk: 0),
                                             caption: "If a product is zero, at least one factor is zero.", isFinal: false))),
                .pause(self.timing.slow),
                .emit(.audioChunk(AudioChunk(chunkIndex: 1, audioURL: self.audioURL(t, chunk: 1),
                                             caption: "So we split it into one equation per bracket.", isFinal: true))),
            ]
            steps += self.inkSteps(TutorInk(inkID: self.nextInk(), region: self.layout.slots[1], formula: "x + 2 = 0   or   x + 3 = 0"), turn: t)
            return steps
        }
    }

    private func draw(_ r: String) {
        counters.scene += 1
        let id = "scene-\(counters.scene)"
        let slots = layout.slots
        let region = slots[2].union(slots[3]).union(slots[4])
        let scene = isPhysics ? MockLessonScript.freeBodyScene(id: id, in: region) : MockLessonScript.parabolaScene(id: id, in: region)
        let caption = isPhysics
            ? "Here's a free-body diagram: the table pushes up, gravity pulls down. This drawing is illustrative, not a verified answer."
            : "Here's the graph of y = x² + 5x + 6. It crosses the x-axis at both roots, x = −3 and x = −2."
        runTurn(request: r) { t in [
            .emit(.tutorTurn(TutorTurn(move: .draw, caption: caption, audioURL: self.audioURL(t)))),
            .emit(.scenePreview(scene)),
            .pause(self.timing.step),
            .emit(.sceneCommit(CommitRef(id: id))),
        ] }
    }

    private func showAnswer(_ r: String) {
        assisted = true
        let answer = problem?.item == "independent_check" ? "x = −3   or   x = −4" : "x = −2   or   x = −3"
        skills = skills.map { var s = $0; if s.skill == "factor_quadratic" { s.assisted = true }; return s }
        let skillsNow = skills
        runTurn(request: r) { t in
            var steps: [Step] = [
                .emit(.tutorTurn(TutorTurn(move: .showAnswer,
                                           caption: "Here's the answer: \(answer.replacingOccurrences(of: "   ", with: " ")). Each bracket gives one root. I've marked this one as helped.",
                                           audioURL: self.audioURL(t), assisted: true))),
            ]
            steps += self.inkSteps(TutorInk(inkID: self.nextInk(), region: self.layout.slots[1], formula: answer), turn: t)
            steps.append(.emit(.skills(skillsNow)))
            if self.problem?.item != "independent_check" {
                self.recordItem(.valid, evidence: nil)
                steps += self.transferSteps(t)
            }
            return steps
        }
    }

    private func transferSteps(_ t: String) -> [Step] {
        problem = ProblemInfo(text: MockLessonScript.transferProblem, subject: "algebra", confirmed: true, item: "independent_check")
        assisted = false
        lines = []; confirmedLines = [:]
        return [
            .pause(timing.step),
            .emit(.state(snapshot(.independentCheck))),
            .emit(.tutorTurn(TutorTurn(move: .ask,
                                       caption: "Now one on your own, with no hints: solve x² + 7x + 12 = 0. Type or write both roots.",
                                       audioURL: audioURL(t)))),
        ]
    }

    private func ask(_ text: String, request r: String) {
        let lower = text.lowercased()
        let caption = (lower.contains("root") || lower.contains("why"))
            ? "Because a product is zero when either bracket is zero. Each bracket gives its own root, so stopping at one leaves out a real solution."
            : "Good question. Let's keep it to this problem: what does your last line tell you?"
        runTurn(request: r) { t in [.emit(.tutorTurn(TutorTurn(move: .explain, caption: caption, audioURL: self.audioURL(t))))] }
    }

    private func quiz(_ text: String, request r: String) {
        guard problem?.item == "independent_check" else { return ask(text, request: r) }
        let res = MockLessonScript.checkTransfer(text)
        let independent = !assisted && res.verdict == .valid
        let e = evidence(r, res.stepKind, res.verdict, res.reason, label: .verified, line: nil, highlights: [],
                         normalized: res.normalized, independent: independent)
        if independent {
            skills = skills.map { s in
                var s = s
                if s.skill == "factor_quadratic" { s.level = min(s.level + 1, 5); s.independentEvidence += 1 }
                return s
            }
        }
        let skillsNow = skills
        let done = res.verdict == .valid
        let caption = done && !independent ? "Correct. This one counts as helped." : res.caption
        if done { recordItem(.valid, evidence: e.evidenceID, independent: independent) }
        runTurn(request: r, endPhase: done ? .complete : .independentCheck) { t in [
            .emit(.agentTrace(AgentTrace(component: "mock-agent", action: "check_math_step", outcome: res.verdict.rawValue,
                                         latencyMs: 0, evidenceID: e.evidenceID))),
            .emit(.evidence(e)),
            .emit(.skills(skillsNow)),
            .emit(.tutorTurn(TutorTurn(move: done ? .recap : .ask, caption: caption, audioURL: self.audioURL(t),
                                       evidenceIDs: [e.evidenceID]))),
        ] }
    }

    private func requestTransferAnswer(_ requestID: String) {
        runTurn(request: requestID) { turnID in
            [.emit(.tutorTurn(TutorTurn(move: .clarify,
                                       caption: "Type both roots so I can check your answer in this demo.",
                                       audioURL: self.audioURL(turnID))))]
        }
    }

    private func confirm(requestID: String, accepted: Bool, text: String?, revision: BoardRevision) {
        guard let p = pending.removeValue(forKey: requestID) else { return }
        boardRevision = max(boardRevision, revision)
        switch p {
        case .question(let candidate):
            if accepted {
                problem = ProblemInfo(text: text ?? candidate, subject: "algebra", confirmed: true)
                emit(.state(snapshot(.ready)), request: requestID)
            } else {
                problem = nil
                emit(.state(snapshot(.idle)), request: requestID)
            }
        case let .board(lineIndex, candidate):
            guard accepted || text != nil else {
                return emit(.state(snapshot(itemPhase)), request: requestID)
            }
            confirmedLines[lineIndex] = text ?? candidate
            let region = lines.first { $0.lineIndex == lineIndex } ?? Region(lineIndex: lineIndex, bbox: layout.workArea)
            evaluate(text ?? candidate, region: region, request: requestID)
        case .transcript(let candidate):
            if accepted || text != nil { ask(text ?? candidate, request: requestID) }
        }
    }

    private func cancel(turnID: String) {
        guard runningTurn == turnID, !cancelledTurns.contains(turnID) else { return }
        cancelledTurns.insert(turnID)
        runner?.cancel()
        runner = nil
        runningTurn = nil
        emit(.turnCancelled(TurnCancelled(reason: "student_cancelled")), turn: turnID)
        emit(.state(snapshot(itemPhase)))
    }

    // MARK: Turn machinery

    /// Start a new turn after a cancellation boundary for any running one.
    private func runTurn(request r: String, endPhase: LessonPhase? = nil, _ build: @escaping (String) -> [Step]) {
        if let old = runningTurn {
            cancelledTurns.insert(old)
            runner?.cancel()
            emit(.turnCancelled(TurnCancelled(reason: "superseded")), turn: old)
        }
        counters.turn += 1
        let t = "t\(counters.turn)"
        runningTurn = t
        latestTurnID = t
        emit(.state(snapshot(.tutoring)), turn: t, request: r)
        let steps = build(t)
        let end = endPhase
        schedule(turn: t, request: r, steps + [.pause(timing.step)]) { [weak self] in
            await self?.finishTurn(t, endPhase: end)
        }
    }

    private func finishTurn(_ t: String, endPhase: LessonPhase?) {
        guard runningTurn == t else { return }
        runningTurn = nil
        runner = nil
        emit(.state(snapshot(endPhase ?? itemPhase)), turn: t)
    }

    private func schedule(turn: String?, request: String?, _ steps: [Step], then: (@Sendable () async -> Void)? = nil) {
        let task = Task {
            for step in steps {
                if Task.isCancelled { return }
                switch step {
                case .pause(let d): try? await Task.sleep(for: d)
                case .emit(let e):
                    if Task.isCancelled { return }
                    self.emitScripted(e, turn: turn, request: request)
                }
            }
            if !Task.isCancelled { await then?() }
        }
        if turn != nil { runner = task }
    }

    private func emitScripted(_ e: ServerEvent, turn: String?, request: String?) {
        if let turn, cancelledTurns.contains(turn) { return }
        emit(e, turn: turn, request: request)
    }

    private func inkSteps(_ ink: TutorInk, turn t: String) -> [Step] {
        if faults.remove(.staleNextInk) != nil {
            staleInk[ink.inkID] = (t, ink)
            return [.pause(timing.step), .emit(.unknown(type: "__stale_ink__\(ink.inkID)"))]
        }
        return [.pause(timing.step), .emit(.tutorInkPreview(ink)), .pause(timing.step), .emit(.tutorInkCommit(CommitRef(id: ink.inkID)))]
    }

    // MARK: Emission

    private func emit(_ event: ServerEvent, turn: String? = nil, request: String? = nil) {
        var event = event
        var revision = boardRevision
        if case .unknown(let type) = event, type.hasPrefix("__stale_ink__") {
            // Fault: a preview computed against the previous board revision.
            let id = String(type.dropFirst("__stale_ink__".count))
            guard let s = staleInk[id] else { return }
            event = .tutorInkPreview(s.ink)
            revision = boardRevision - 1
        }
        if case .state(let snap) = event { phase = snap.phase }
        seq += 1
        let env = ServerEnvelope(sessionID: sessionID, seq: seq, boardRevision: revision, turnID: turn,
                                 runID: turn.map { "run-\($0)" }, requestID: request, event: event)
        // Round-trip through the wire format so the mock exercises the real decoder.
        guard let data = try? WireCoding.encoder().encode(env),
              let decoded = try? WireCoding.decoder().decode(ServerEnvelope.self, from: data) else {
            assertionFailure("mock emitted an envelope that does not round-trip: \(env)")
            return
        }
        log.append(decoded)
        if connected {
            continuation?.yield(.envelope(decoded))
            deliveredSeq = decoded.seq
        }
    }

    private func resume() {
        guard !connected, continuation != nil else { return }
        connected = true
        continuation?.yield(.state(.connected))
        replayAndSnapshot(after: deliveredSeq)
    }

    /// Replay durable events after the cursor (speech never), with a two-event
    /// overlap the client must de-duplicate, then send a fresh state snapshot.
    private func replayAndSnapshot(after cursor: Int?) {
        if let cursor {
            for var env in log where env.seq > cursor - 2 && env.event.isDurable {
                env.replayed = true
                continuation?.yield(.envelope(env))
                deliveredSeq = env.seq
            }
        }
        emit(.state(snapshot(runningTurn == nil ? phase : .tutoring)), turn: latestTurnID)
    }

    // MARK: Helpers

    private func snapshot(_ p: LessonPhase) -> LessonSnapshot {
        LessonSnapshot(phase: p, problem: problem, latestTurnID: latestTurnID, skills: skills)
    }

    private func audioURL(_ turn: String, chunk: Int? = nil) -> String {
        let suffix = chunk.map { "\(turn)-\($0)" } ?? turn
        if faults.remove(.loseNextAudio) != nil { return "mock://lost/\(suffix).wav" }
        return "mock://clip/\(suffix).wav"
    }

    private func nextInk() -> String { counters.ink += 1; return "ink-\(counters.ink)" }

    private func evidence(_ r: String, _ kind: StepKind, _ verdict: Verdict, _ reason: ReasonCode, label: EvidenceLabel,
                          line: Int?, highlights: [Region], normalized: String? = nil, independent: Bool = false) -> Evidence {
        counters.evidence += 1
        return Evidence(requestID: r, evidenceID: "ev-\(counters.evidence)", subject: problem?.subject ?? "algebra", stepKind: kind,
                        verdict: verdict, reasonCode: reason, label: label, verifierVersion: MockLessonScript.verifierVersion,
                        normalizedStep: normalized, lineIndex: line, highlights: highlights, independent: independent)
    }

    private func recordItem(_ outcome: Verdict, evidence: String?, independent: Bool = false) {
        summaryItems.append(SessionSummary.Item(problem: problem?.text ?? "", outcome: outcome, assisted: assisted,
                                                independent: independent, evidenceIDs: evidence.map { [$0] } ?? []))
    }
}
