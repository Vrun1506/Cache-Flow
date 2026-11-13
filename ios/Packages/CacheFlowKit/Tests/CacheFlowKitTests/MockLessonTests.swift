import CoreGraphics
import Foundation
import Testing
@testable import CacheFlowKit

/// Drives MockServer through the same `LessonEngine` the app uses, performing
/// its effects the way `TutorClient` does (acks and audio_done go back on the
/// stream). Headless twin of the simulator UI test.
actor LessonHarness {
    let mock: MockServer
    let layout = BoardLayout(screenSize: CGSize(width: 1210, height: 834))
    private(set) var engine: LessonEngine
    private(set) var spoken: [SpeechRequest] = []
    private(set) var connections: [ConnectionState] = []
    private(set) var sid = ""
    private var ids = RequestIDGenerator(prefix: "h")
    private var lines = 0
    private var consumer: Task<Void, Never>?

    init(timing: MockServer.Timing = .fast) {
        mock = MockServer(layout: layout, timing: timing)
        engine = LessonEngine(sessionID: "", boardRevision: 0)
    }

    func start(preset: String? = nil) async throws {
        let info = try await mock.createSession(presetID: preset, locale: "en")
        sid = info.sessionID
        engine = LessonEngine(sessionID: sid, boardRevision: info.boardRevision, boardBounds: layout.boardBounds)
        let stream = await mock.events(sessionID: sid, afterSeq: nil)
        consumer = Task { for await item in stream { await self.handle(item) } }
    }

    private func handle(_ item: StreamItem) async {
        switch item {
        case .state(let s): connections.append(s)
        case .envelope(let env): await perform(engine.receive(env))
        }
    }

    private func perform(_ effects: [LessonEffect]) async {
        for effect in effects {
            switch effect {
            case .send(let m): await mock.send(ClientEnvelope(sessionID: sid, boardRevision: engine.state.boardRevision, message: m))
            case .speak(let r):
                spoken.append(r)
                await mock.send(ClientEnvelope(sessionID: sid, boardRevision: engine.state.boardRevision,
                                               message: .audioDone(turnID: r.turnID, chunkIndex: r.chunkIndex)))
            case .stopSpeech: break
            }
        }
    }

    var state: LessonState { engine.state }

    func waitUntil(_ what: String, timeout: Duration = .seconds(5), _ predicate: @Sendable (LessonState) -> Bool) async throws {
        let deadline = ContinuousClock.now + timeout
        while !predicate(engine.state) {
            guard ContinuousClock.now < deadline else {
                Issue.record("timed out waiting for \(what); phase=\(engine.state.phase.rawValue) caption=\(engine.state.caption)")
                throw CancellationError()
            }
            try await Task.sleep(for: .milliseconds(5))
        }
    }

    /// The student writes one more line of working and the iPad uploads the board.
    func writeLine() async throws {
        engine.studentInkChanged()
        lines += 1
        let regions = (0..<lines).map { Region(lineIndex: $0, bbox: CGRect(x: 40, y: 180 + 80 * $0, width: 300, height: 50)) }
        let snap = BoardSnapshot(boardRevision: engine.state.boardRevision, imageRegion: layout.workArea, png: Data([1]), lines: regions)
        _ = try await mock.submitBoard(sessionID: sid, snap, requestID: ids.next())
    }

    @discardableResult
    func intent(_ action: IntentAction, text: String? = nil, requestID: String? = nil) async throws -> String {
        let rid = requestID ?? ids.next()
        _ = try await mock.sendIntent(sessionID: sid, IntentRequest(action: action, boardRevision: engine.state.boardRevision,
                                                                     requestID: rid, text: text))
        return rid
    }

    func question() async throws {
        _ = try await mock.submitQuestion(sessionID: sid, .image(png: Data([1])), boardRevision: engine.state.boardRevision, requestID: ids.next())
    }

    func speak() async throws -> TranscriptCandidate {
        try await mock.submitAudio(sessionID: sid, wav: Data([1]), boardRevision: engine.state.boardRevision, requestID: ids.next())
    }

    func confirm(accepted: Bool = true, text: String? = nil) async { await perform(engine.confirmReadBack(accepted: accepted, text: text)) }
    func cancel() async { await perform(engine.cancelActiveTurn(requestID: ids.next())) }
    func stop() { consumer?.cancel() }
}

@Suite(.serialized) struct MockLessonTests {
    @Test func theWholeDemoRunsAgainstTheMock() async throws {
        let h = LessonHarness(timing: MockServer.Timing(step: .milliseconds(1), slow: .milliseconds(400), reconnect: .milliseconds(250)))
        try await h.start()

        // 1. Handwritten question with an ambiguous read-back.
        try await h.question()
        try await h.waitUntil("question read-back") { $0.pendingReadBack?.source == .question }
        #expect(await h.state.pendingReadBack?.ambiguous == true)
        await h.confirm(text: "x² + 5x + 6 = 0")
        try await h.waitUntil("ready") { $0.phase == .ready && $0.problem?.confirmed == true }

        // 2. Tutor me.
        try await h.intent(.tutor)
        try await h.waitUntil("first question") { $0.phase == .studentTurn && $0.caption.contains("multiply to 6") }

        // 3. Sign error: invalid/sign_mismatch with a highlight circle.
        try await h.writeLine()
        try await h.intent(.check)
        try await h.waitUntil("sign mismatch") { $0.latestEvidence?.reasonCode == .signMismatch && $0.committedInk.count == 1 && $0.phase == .studentTurn }

        // 4. Ambiguous handwriting is ungraded until confirmed, then valid.
        try await h.writeLine()
        try await h.intent(.check)
        try await h.waitUntil("ambiguous line") { $0.pendingReadBack?.source == .board }
        #expect(await h.state.latestEvidence?.verdict == .unknown)
        #expect(await h.state.latestEvidence?.reasonCode == .ambiguousInput)
        await h.confirm(text: "(x + 2)(x + 3) = 0")
        try await h.waitUntil("valid factors") { $0.latestEvidence?.verdict == .valid && $0.phase == .studentTurn }

        // 5. A single root is partial progress.
        try await h.writeLine()
        try await h.intent(.check)
        try await h.waitUntil("partial root") { $0.latestEvidence?.reasonCode == .partialRoot && $0.phase == .studentTurn }

        // 6. Lost audio: the hint is still captioned and the clip is flagged lost.
        await h.mock.inject(.loseNextAudio)
        try await h.intent(.hint)
        try await h.waitUntil("hint") { $0.lastMove == .hint && $0.committedInk.count == 2 && $0.phase == .studentTurn }
        #expect(await h.spoken.last.map { AudioURLKind.classify($0.url) } == .mockLost)

        // 7. Push-to-talk: the transcript needs confirmation, then the tutor answers.
        let candidate = try await h.speak()
        #expect(candidate.ambiguous)
        try await h.waitUntil("transcript read-back") { $0.pendingReadBack?.source == .transcript }
        await h.confirm(text: "Why does the other root matter?")
        try await h.waitUntil("answer to the question") { $0.caption.contains("product is zero") && $0.phase == .studentTurn }

        // 8. Cancellation: stop a long explanation; its later audio and ink never arrive.
        let spokenBefore = await h.spoken.count
        try await h.intent(.explainStep)
        try await h.waitUntil("explaining") { $0.phase == .tutoring && $0.lastMove == .explain }
        try await Task.sleep(for: .milliseconds(30))
        await h.cancel()
        try await h.waitUntil("cancelled") { $0.phase == .studentTurn }
        try await Task.sleep(for: .milliseconds(600))
        #expect(await h.state.committedInk.count == 2)
        #expect(await h.spoken.count <= spokenBefore + 1, "only the first chunk may have played")
        #expect(await h.mock.log.contains { if case .turnCancelled = $0.event { true } else { false } })

        // 9. Stale ink: rejected, then re-issued against the current board exactly once.
        await h.mock.inject(.staleNextInk)
        try await h.intent(.hint)
        try await h.waitUntil("stale ink recovered") { $0.counters.staleRejected == 1 && $0.committedInk.count == 3 }
        #expect(await h.mock.received.contains { if case .tutorInkAck(_, _, false, "stale_revision") = $0.message { true } else { false } })

        // 10. Reconnect: drop the stream, draw while offline, resume from the cursor.
        let speechBeforeDraw = await h.spoken.count
        await h.mock.dropConnection()
        try await h.intent(.draw)
        try await h.waitUntil("scene after reconnect", timeout: .seconds(5)) { $0.committedScenes.count == 1 && $0.phase == .studentTurn }
        #expect(await h.connections.suffix(2) == [.reconnecting, .connected])
        #expect(await h.spoken.count == speechBeforeDraw, "speech produced while offline is never replayed")
        #expect(await h.state.counters.replayedSpeechSuppressed >= 1)
        #expect(await h.state.lastSeq == h.mock.log.last?.seq)

        // 11. Show the answer: assisted, then a fresh independent item.
        try await h.intent(.showAnswer)
        try await h.waitUntil("transfer item") { $0.phase == .independentCheck && $0.caption.contains("on your own") }
        #expect(await h.state.skills.first { $0.skill == "factor_quadratic" }?.assisted == true)
        #expect(await h.state.assistedThisItem == false)

        // 12. Independent check answered by typing.
        try await h.intent(.quizAnswer, text: "x = -3 or x = -4")
        try await h.waitUntil("complete") { $0.phase == .complete }
        #expect(await h.state.latestEvidence?.independent == true)
        let summary = try await h.mock.summary(sessionID: h.sid)
        #expect(summary.items.map(\.assisted) == [true, false])
        #expect(summary.items.map(\.independent) == [false, true])

        // Every envelope the mock produced decodes and is strictly sequenced.
        let seqs = await h.mock.log.map(\.seq)
        #expect(seqs == Array(1...seqs.count))
        await h.stop()
    }

    @Test func duplicateRequestIDsDoNotDuplicateTurnsOrInk() async throws {
        let h = LessonHarness()
        try await h.start(preset: "quad_5_6")
        try await h.writeLine()
        let rid = try await h.intent(.hint)
        try await h.intent(.hint, requestID: rid)
        try await h.waitUntil("hint done") { $0.committedInk.count == 1 && $0.phase == .studentTurn }
        try await Task.sleep(for: .milliseconds(50))
        #expect(await h.state.committedInk.count == 1)
        #expect(await h.mock.log.filter { if case .tutorTurn = $0.event { true } else { false } }.count == 1)
        await h.stop()
    }

    @Test func staleRequestsConflictWithTheLatestRevision() async throws {
        let mock = MockServer(layout: BoardLayout(screenSize: CGSize(width: 1210, height: 834)), timing: .fast)
        let info = try await mock.createSession(presetID: "quad_5_6", locale: "en")
        _ = try await mock.sendIntent(sessionID: info.sessionID, IntentRequest(action: .hint, boardRevision: 3, requestID: "a"))
        await #expect(throws: TutorAPIError.staleRevision(latest: 3)) {
            _ = try await mock.sendIntent(sessionID: info.sessionID, IntentRequest(action: .check, boardRevision: 2, requestID: "b"))
        }
    }

    @Test func secondSubjectIsNeverGraded() async throws {
        let h = LessonHarness()
        try await h.start(preset: "phys_forces_box")
        try await h.intent(.tutor)
        try await h.waitUntil("physics question") { $0.phase == .studentTurn && $0.caption.contains("forces") }
        try await h.writeLine()
        try await h.intent(.check)
        try await h.waitUntil("not verified") { $0.latestEvidence?.label == .notVerified && $0.phase == .studentTurn }
        try await h.intent(.draw)
        try await h.waitUntil("diagram") { $0.committedScenes.count == 1 && $0.phase == .studentTurn }
        #expect(await h.state.committedScenes.first?.scene.title == "Forces on a resting box")
        await h.stop()
    }
}
