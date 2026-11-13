import CoreGraphics
import Foundation
import Testing
@testable import CacheFlowKit

/// The wire contract: every fixture decodes, re-encodes losslessly, and
/// client messages/HTTP bodies match their fixtures byte-for-byte in meaning.
@Suite struct ContractTests {
    @Test func everyEventFixtureDecodesAndRoundTrips() throws {
        let files = try Fixture.files(in: "events")
        #expect(files.count == 14)
        for file in files {
            let raw = try Fixture.data(file)
            let env = try WireCoding.decoder().decode(ServerEnvelope.self, from: raw)
            let name = (file as NSString).lastPathComponent.replacingOccurrences(of: ".json", with: "")
            if name == "_unknown_type" {
                #expect(env.event == .unknown(type: "from_the_future"))
                continue
            }
            #expect(env.event.type == name, "\(file) decoded as \(env.event.type)")
            #expect(env.version == 2 && env.sessionID == "s-fixture")
            let again = try WireCoding.encoder().encode(env)
            #expect(try Fixture.object(again) == Fixture.object(raw), "\(file) does not round-trip")
        }
    }

    @Test func malformedFixturesAreRejected() throws {
        for file in try Fixture.files(in: "malformed") {
            #expect(throws: (any Error).self, "\(file) should not decode") { try Fixture.envelope(file) }
        }
    }

    @Test func keyPayloadsDecodeToTheRightValues() throws {
        guard case .readBack(let rb) = try Fixture.envelope("events/read_back.json").event else { Issue.record("read_back"); return }
        #expect(rb.source == .question && rb.ambiguous && rb.alternatives.count == 2)
        guard case .evidence(let e) = try Fixture.envelope("events/evidence.json").event else { Issue.record("evidence"); return }
        #expect((e.verdict, e.reasonCode, e.stepKind, e.label) == (.invalid, .signMismatch, .factorization, .verified))
        #expect(e.highlights.first?.bbox == CGRect(x: 40, y: 180, width: 300, height: 50))
        guard case .tutorInkPreview(let ink) = try Fixture.envelope("events/tutor_ink_preview.json").event else { Issue.record("ink"); return }
        #expect(ink.style == .circle && ink.strokes.first?.count == 5)
        guard case .scenePreview(let scene) = try Fixture.envelope("events/scene_preview.json").event else { Issue.record("scene"); return }
        #expect(scene.primitives.map(\.kind) == [.axes, .plot, .point])
        guard case .error(let err) = try Fixture.envelope("events/error.json").event else { Issue.record("error"); return }
        #expect(err.latestBoardRevision == 5 && err.retryable)
        let turn = try Fixture.envelope("events/tutor_turn.json")
        #expect(turn.turnID == "t2" && turn.runID == "run-t2" && turn.requestID == "r3" && turn.boardRevision == 2)
    }

    @Test func missingEvidenceLabelNeverMeansVerified() throws {
        let json = #"{"v":2,"type":"evidence","session_id":"s","seq":1,"board_revision":0,"payload":{"evidence_id":"e","step_kind":"factorization","verdict":"valid","reason_code":"ok","verifier_version":"x"}}"#
        guard case .evidence(let e) = try WireCoding.decoder().decode(ServerEnvelope.self, from: Data(json.utf8)).event else {
            Issue.record("expected evidence"); return
        }
        #expect(e.label == .notVerified)
        #expect(e.summary == "Looks right (not verified)")
    }

    @Test func clientMessagesMatchFixtures() throws {
        let cases: [(String, ClientMessage)] = [
            ("read_back_confirm", .readBackConfirm(requestID: "r5", accepted: true, text: "(x + 2)(x + 3) = 0")),
            ("audio_done", .audioDone(turnID: "t4", chunkIndex: 1)),
            ("tutor_ink_ack", .tutorInkAck(turnID: "t2", inkID: "ink-1", placed: false, reason: "stale_revision")),
            ("scene_ack", .sceneAck(turnID: "t3", sceneID: "scene-1", placed: true, reason: nil)),
            ("cancel_turn", .cancelTurn(turnID: "t4", requestID: "r12")),
        ]
        #expect(try Fixture.files(in: "client").count == cases.count)
        for (name, message) in cases {
            let encoded = try WireCoding.encoder().encode(ClientEnvelope(sessionID: "s-fixture", boardRevision: 3, message: message))
            #expect(try Fixture.object(encoded) == Fixture.object(try Fixture.data("client/\(name).json")), "\(name)")
        }
    }

    @Test func httpBodiesMatchFixtures() throws {
        let p = ServerProfile(id: "t", name: "T", baseURL: "https://spark.local:8443")
        let create = try TutorRequests.createSession(p, presetID: "quad_5_6", locale: "en_GB")
        #expect(try Fixture.object(create.httpBody!) == Fixture.object(try Fixture.data("http/create_session_request.json")))
        let intent = try TutorRequests.intent(p, sessionID: "s", IntentRequest(action: .check, boardRevision: 3, requestID: "r3"))
        #expect(try Fixture.object(intent.httpBody!) == Fixture.object(try Fixture.data("http/intent_request.json")))
        let cancel = try TutorRequests.intent(p, sessionID: "s", IntentRequest(action: .cancel, boardRevision: 3, requestID: "r12", turnID: "t4"))
        #expect(try Fixture.object(cancel.httpBody!) == Fixture.object(try Fixture.data("http/intent_cancel_request.json")))

        let d = WireCoding.decoder()
        #expect(try d.decode(SessionInfo.self, from: Fixture.data("http/create_session_response.json"))
            == SessionInfo(sessionID: "s-fixture", boardRevision: 0))
        #expect(try d.decode(Accepted.self, from: Fixture.data("http/accepted.json")).requestID == "r3")
        #expect(try d.decode(TranscriptCandidate.self, from: Fixture.data("http/audio_response.json"))
            == TranscriptCandidate(requestID: "r7", text: "Why does the other root matter?", ambiguous: true))
        let health = try d.decode(HealthReport.self, from: Fixture.data("http/health.json"))
        #expect(health.components["vllm"]?.latencyMs == 180 && !health.allOK)
        let summary = try d.decode(SessionSummary.self, from: Fixture.data("http/summary.json"))
        #expect(summary.items.map(\.independent) == [false, true] && summary.items[0].assisted)
        #expect(TutorRequests.error(status: 409, body: try Fixture.data("http/conflict_409.json")) == .staleRevision(latest: 5))
        #expect(TutorRequests.error(status: 426, body: Data()) == .upgradeRequired)
    }
}
