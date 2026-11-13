import Foundation
import Testing
@testable import CacheFlowKit

/// Interface.md fixtures fed through `LessonEngine`, the same reducer the app uses.
@Suite struct ScenarioTests {
    @Test func firstLessonFromSignErrorToIndependentCheck() throws {
        let (engine, effects) = try Fixture.run("first_lesson")
        let s = engine.state
        #expect(s.evidence.map(\.verdict) == [.invalid, .valid, .partial, .valid])
        #expect(s.evidence.map(\.reasonCode) == [.signMismatch, .ok, .partialRoot, .ok])
        #expect(s.evidence.last?.independent == true)
        #expect(s.committedInk.map(\.ink.inkID) == ["ink-1", "ink-2"])
        #expect(s.phase == .complete && s.problem?.item == "independent_check")
        #expect(s.skills.first?.level == 2 && s.skills.first?.independentEvidence == 1 && s.skills.first?.assisted == true)
        #expect(s.lineReadings[0] == "(x − 2)(x − 3) = 0")
        #expect(s.caption == "Correct, and you did that one without help.")
        #expect(effects.compactMap(\.spoken).map(\.url) ==
                ["/v2/audio/t1.wav", "/v2/audio/t2.wav", "/v2/audio/t3.wav", "/v2/audio/t4.wav",
                 "/v2/audio/t5.wav", "/v2/audio/t5b.wav", "/v2/audio/t6.wav"])
        #expect(effects.compactMap(\.sent) == [
            .tutorInkAck(turnID: "t2", inkID: "ink-1", placed: true, reason: nil),
            .tutorInkAck(turnID: "t5", inkID: "ink-2", placed: true, reason: nil),
        ])
        #expect(s.counters == LessonCounters())
    }

    @Test func showTheAnswerMarksTheItemAssisted() throws {
        let s = try JSONDecoder().decode(Fixture.Scenario.self, from: try Fixture.data("scenarios/first_lesson.json"))
        var engine = LessonEngine(sessionID: s.session_id, boardRevision: 0)
        for step in s.steps {
            if let env = step.server {
                _ = engine.receive(env)
                if env.seq == 15 { #expect(engine.state.assistedThisItem && engine.state.lastMove == .showAnswer) }
                if env.seq == 19 { #expect(!engine.state.assistedThisItem, "a fresh transfer item starts unassisted") }
            }
            if step.student == "ink" { engine.studentInkChanged() }
        }
    }

    @Test func ambiguousHandwritingIsUngradedUntilConfirmed() throws {
        let s = try JSONDecoder().decode(Fixture.Scenario.self, from: try Fixture.data("scenarios/ambiguous_handwriting.json"))
        var engine = LessonEngine(sessionID: s.session_id, boardRevision: 0)
        var sent: [ClientMessage] = []
        for step in s.steps {
            if step.student == "confirm" {
                #expect(engine.state.pendingReadBack?.source == .board)
                #expect(engine.state.latestEvidence?.verdict == .unknown)
                #expect(engine.state.latestEvidence?.summary == "Not graded: please confirm what you wrote")
                #expect(ActionPolicy.available(engine.state).isEmpty, "no actions while a read-back waits")
                sent += engine.confirmReadBack(accepted: true, text: step.text).compactMap(\.sent)
            }
            if step.student == "ink" { engine.studentInkChanged() }
            if let env = step.server { _ = engine.receive(env) }
        }
        #expect(sent == [.readBackConfirm(requestID: "r2", accepted: true, text: "(x + 2)(x + 3) = 0")])
        #expect(engine.state.pendingReadBack == nil)
        #expect(engine.state.lineReadings[1] == "(x + 2)(x + 3) = 0")
        #expect(engine.state.latestEvidence?.verdict == .valid)
    }

    @Test func staleInkIsRejectedAndOnlyTheCurrentRevisionCommits() throws {
        let (engine, effects) = try Fixture.run("stale_ink")
        #expect(engine.state.boardRevision == 2)
        #expect(effects.compactMap(\.sent) == [
            .tutorInkAck(turnID: "t1", inkID: "ink-1", placed: false, reason: "stale_revision"),
            .tutorInkAck(turnID: "t1", inkID: "ink-2", placed: true, reason: nil),
        ])
        #expect(engine.state.committedInk.map(\.ink.inkID) == ["ink-2"])
        #expect(engine.state.counters.staleRejected == 2)
    }

    @Test func penMovementInvalidatesAPlacedPreview() throws {
        let (engine, _) = try Fixture.run("preview_invalidated_by_pen")
        #expect(engine.state.ink.isEmpty)
        #expect(engine.state.counters.previewsInvalidated == 1)
        #expect(engine.state.counters.staleRejected == 1)
    }

    @Test func lostAudioKeepsTheCaption() throws {
        let (engine, effects) = try Fixture.run("lost_audio")
        #expect(engine.state.caption == "Look at the signs of the middle term.")
        let clip = try #require(effects.compactMap(\.spoken).first)
        #expect(AudioURLKind.classify(clip.url) == .mockLost)
        #expect(clip.caption == engine.state.caption, "the fallback voice reads exactly the caption")
    }

    @Test func cancellationDropsTheTurnsAudioInkAndScenes() throws {
        let (engine, effects) = try Fixture.run("cancellation")
        #expect(effects.contains(.stopSpeech(turnID: "t1")))
        #expect(effects.compactMap(\.sent).contains(.cancelTurn(turnID: "t1", requestID: "test-1")))
        #expect(effects.compactMap(\.spoken).map(\.url) == ["/v2/audio/t1-0.wav", "/v2/audio/t2.wav"])
        #expect(!effects.compactMap(\.sent).contains { if case .tutorInkAck = $0 { true } else { false } })
        #expect(engine.state.ink.isEmpty)
        #expect(engine.state.counters.cancelledDropped == 3)
        #expect(engine.state.caption == "A new turn after the boundary plays normally.")
    }

    @Test func reconnectRestoresCommandsWithoutReplayingSpeech() throws {
        let (engine, effects) = try Fixture.run("reconnect_replay")
        #expect(effects.compactMap(\.spoken).map(\.url) == ["/v2/audio/t1.wav"])
        #expect(engine.state.counters.duplicatesDropped == 2)
        #expect(engine.state.counters.replayedSpeechSuppressed == 1)
        #expect(engine.state.committedInk.map(\.ink.inkID) == ["ink-1"])
        #expect(engine.state.committedScenes.map(\.scene.sceneID) == ["scene-1"])
        #expect(engine.state.caption == "Here's the graph.")
        #expect(engine.state.lastSeq == 8)
    }

    @Test func guardrailDenialIsAPlainLanguageError() throws {
        let (engine, effects) = try Fixture.run("guardrail_denial")
        #expect(engine.state.lastError?.code == "guardrail_blocked")
        #expect(engine.state.notice == "I can't help with that here. Let's get back to the problem.")
        #expect(effects.isEmpty)
        #expect(engine.state.evidence.isEmpty)
    }

    @Test func secondSubjectIsLabelledNotVerifiedAndUnapprovedScenesAreRefused() throws {
        let (engine, effects) = try Fixture.run("second_subject_diagram")
        #expect(engine.state.problem?.subject == "physics")
        #expect(engine.state.latestEvidence?.label == .notVerified)
        #expect(engine.state.latestEvidence?.summary == "Not graded")
        #expect(engine.state.committedScenes.map(\.scene.sceneID) == ["scene-1"])
        #expect(effects.compactMap(\.sent).contains(.sceneAck(turnID: "t1", sceneID: "scene-2", placed: false, reason: "invalid")))
        #expect(engine.state.counters.invalidRejected == 1)
    }
}
