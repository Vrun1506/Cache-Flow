import CoreGraphics
import Foundation
import Testing
@testable import CacheFlowKit

@Suite struct EngineTests {
    private func env(_ seq: Int, _ event: ServerEvent, rev: Int = 0, turn: String? = nil, session: String = "s",
                     replayed: Bool = false) -> ServerEnvelope {
        ServerEnvelope(sessionID: session, seq: seq, boardRevision: rev, turnID: turn, replayed: replayed, event: event)
    }
    private let slot = CGRect(x: 700, y: 10, width: 400, height: 100)

    @Test func dropsDuplicatesForeignSessionsAndOtherVersions() {
        var e = LessonEngine(sessionID: "s", boardRevision: 0)
        _ = e.receive(env(1, .skills([])))
        _ = e.receive(env(1, .skills([])))
        _ = e.receive(env(2, .skills([]), session: "other"))
        _ = e.receive(ServerEnvelope(sessionID: "s", seq: 3, boardRevision: 0, event: .skills([]), version: 3))
        #expect(e.state.counters.duplicatesDropped == 1)
        #expect(e.state.counters.staleRejected == 2)
        #expect(e.state.lastSeq == 1)
    }

    @Test func aNewTurnSupersedesTheOldOne() {
        var e = LessonEngine(sessionID: "s", boardRevision: 0)
        _ = e.receive(env(1, .tutorTurn(TutorTurn(move: .ask, caption: "a", audioURL: "/a.wav")), turn: "t1"))
        _ = e.receive(env(2, .tutorInkPreview(TutorInk(inkID: "i1", region: slot, formula: "x")), turn: "t1"))
        let fx = e.receive(env(3, .tutorTurn(TutorTurn(move: .hint, caption: "b")), turn: "t2"))
        #expect(fx == [.stopSpeech(turnID: "t1")])
        #expect(e.state.ink.isEmpty, "uncommitted ink of a superseded turn is dropped")
        _ = e.receive(env(4, .tutorInkCommit(CommitRef(id: "i1")), turn: "t1"))
        #expect(e.state.counters.cancelledDropped == 1)
        #expect(e.state.committedInk.isEmpty)
    }

    @Test func duplicatePreviewsAreIdempotent() {
        var e = LessonEngine(sessionID: "s", boardRevision: 0)
        let ink = TutorInk(inkID: "i1", region: slot, formula: "x")
        _ = e.receive(env(1, .tutorInkPreview(ink), turn: "t1"))
        _ = e.receive(env(2, .tutorInkCommit(CommitRef(id: "i1")), turn: "t1"))
        let again = e.receive(env(3, .tutorInkPreview(ink), turn: "t1"))
        _ = e.receive(env(4, .tutorInkCommit(CommitRef(id: "i1")), turn: "t1"))
        #expect(again == [.send(.tutorInkAck(turnID: "t1", inkID: "i1", placed: true, reason: nil))])
        #expect(e.state.ink.count == 1 && e.state.committedInk.count == 1)
    }

    @Test func outOfBoundsOrOversizedInkIsRefused() {
        var e = LessonEngine(sessionID: "s", boardRevision: 0, boardBounds: CGRect(x: 0, y: 0, width: 1000, height: 600))
        let outside = TutorInk(inkID: "a", region: CGRect(x: 900, y: 500, width: 300, height: 300), formula: "x")
        let strayPoint = TutorInk(inkID: "b", region: slot.offsetBy(dx: -300, dy: 0), strokes: [[CGPoint(x: 0, y: 0), CGPoint(x: 1, y: 1)]])
        let huge = TutorInk(inkID: "c", region: CGRect(x: 0, y: 0, width: 100, height: 100),
                            strokes: Array(repeating: [CGPoint(x: 1, y: 1)], count: TutorInk.maxStrokes + 1))
        for (i, ink) in [outside, strayPoint, huge].enumerated() {
            let fx = e.receive(env(i + 1, .tutorInkPreview(ink), turn: "t"))
            #expect(fx == [.send(.tutorInkAck(turnID: "t", inkID: ink.inkID, placed: false, reason: "invalid"))])
        }
        #expect(e.state.ink.isEmpty && e.state.counters.invalidRejected == 3)
    }

    @Test func clearingTheTutorLayerNeverTouchesStudentState() {
        var e = LessonEngine(sessionID: "s", boardRevision: 4)
        _ = e.receive(env(1, .tutorInkPreview(TutorInk(inkID: "i", region: slot, formula: "x")), rev: 4, turn: "t"))
        _ = e.receive(env(2, .tutorInkCommit(CommitRef(id: "i")), rev: 4, turn: "t"))
        e.clearTutorLayer()
        #expect(e.state.ink.isEmpty && e.state.boardRevision == 4)
    }

    @Test func snapshotNeverMovesTheBoardRevisionBackwards() {
        var e = LessonEngine(sessionID: "s", boardRevision: 0)
        e.studentInkChanged(); e.studentInkChanged()
        _ = e.receive(env(1, .state(LessonSnapshot(phase: .studentTurn, problem: nil, latestTurnID: nil, skills: [])), rev: 1))
        #expect(e.state.boardRevision == 2)
        e.adopt(serverRevision: 7)
        #expect(e.state.boardRevision == 7)
    }

    @Test func replayedAudioChunksAreSuppressed() {
        var e = LessonEngine(sessionID: "s", boardRevision: 0)
        let chunk = AudioChunk(chunkIndex: 0, audioURL: "/c.wav", caption: "c", isFinal: true)
        #expect(e.receive(env(1, .audioChunk(chunk), turn: "t", replayed: true)).isEmpty)
        #expect(e.receive(env(2, .audioChunk(chunk), turn: "t")) == [.speak(SpeechRequest(turnID: "t", chunkIndex: 0, url: "/c.wav", caption: "c"))])
    }

    @Test func cancelWithoutAnActiveTurnDoesNothing() {
        var e = LessonEngine(sessionID: "s", boardRevision: 0)
        #expect(e.cancelActiveTurn(requestID: "r").isEmpty)
    }

    @Test func actionPolicyFollowsThePhase() {
        var s = LessonState(sessionID: "s", boardRevision: 0, caption: "")
        #expect(ActionPolicy.available(s).isEmpty)
        s.phase = .ready
        #expect(ActionPolicy.available(s) == [.tutor, .ask])
        s.phase = .studentTurn
        #expect(ActionPolicy.available(s).isSuperset(of: [.check, .hint, .explainStep, .draw, .showAnswer, .ask]))
        #expect(ActionPolicy.replyAction(s) == .ask)
        s.phase = .tutoring
        #expect(ActionPolicy.available(s) == [.cancel])
        s.phase = .independentCheck
        #expect(ActionPolicy.available(s).contains(.quizAnswer) && ActionPolicy.replyAction(s) == .quizAnswer)
        #expect(IntentAction.check.needsBoardSnapshot && !IntentAction.tutor.needsBoardSnapshot)
    }
}

@Suite struct SpeechQueueTests {
    let a = SpeechRequest(turnID: "t1", url: "/a.wav", caption: "a")
    let b = SpeechRequest(turnID: "t2", url: "/b.wav", caption: "b")

    @Test func playsOneAtATimeInOrder() {
        var q = SpeechQueue()
        q.enqueue(a); q.enqueue(b); q.enqueue(a)
        let first = q.next(), busy = q.next(), done = q.finished(), second = q.next()
        #expect(first == a)
        #expect(busy == nil, "busy")
        #expect(done == a)
        #expect(second == b)
    }

    @Test func neverStartsWhileThePencilIsDown() {
        var q = SpeechQueue()
        q.enqueue(a)
        q.setPen(down: true)
        let held = q.next()
        q.setPen(down: false)
        let released = q.next()
        #expect(held == nil)
        #expect(released == a)
    }

    @Test func cancellingATurnDropsItsClips() {
        var q = SpeechQueue()
        q.enqueue(a); q.enqueue(b)
        _ = q.next()
        let stopCurrent = q.cancel(turnID: "t1")
        let next = q.next()
        let unrelated = q.cancel(turnID: "t9")
        let all = q.cancel(turnID: nil)
        #expect(stopCurrent, "the playing clip belongs to t1")
        #expect(next == b)
        #expect(!unrelated)
        #expect(all)
        #expect(q.pending.isEmpty && q.current == nil)
    }

    @Test func classifiesAudioURLs() {
        #expect(AudioURLKind.classify("mock://lost/t1.wav") == .mockLost)
        #expect(AudioURLKind.classify("mock://clip/t1.wav") == .mockClip)
        #expect(AudioURLKind.classify("/v2/audio/t1.wav") == .remote)
    }
}
