import CoreGraphics
import Foundation
import Testing
@testable import CacheFlowKit

@Suite struct NetTests {
    let profile = ServerProfile(id: "t", name: "T", baseURL: "http://tutor-spark:8000")

    @Test func profilesBuildURLs() {
        #expect(profile.url(path: "/health")?.absoluteString == "http://tutor-spark:8000/health")
        #expect(profile.webSocketURL(sessionID: "abc")?.absoluteString == "ws://tutor-spark:8000/ws/abc")
        let tls = ServerProfile(id: "s", name: "S", baseURL: "https://spark.example.ts.net/")
        #expect(tls.webSocketURL(sessionID: "abc")?.absoluteString == "wss://spark.example.ts.net/ws/abc")
        #expect(profile.resolve("/audio/s2i.wav")?.absoluteString == "http://tutor-spark:8000/audio/s2i.wav")
        #expect(ServerProfile.mock.url(path: "/health") == nil)
        #expect(ServerProfile(id: "x", name: "x", baseURL: "not a url").url(path: "/h") == nil)
    }

    @Test func createSessionRequest() throws {
        let r = try TutorRequests.createSession(profile, problemID: "quad_5_6")
        #expect(r.httpMethod == "POST")
        #expect(r.url?.path == "/session")
        #expect(String(decoding: r.httpBody!, as: UTF8.self) == #"{"problem_id":"quad_5_6"}"#)
        #expect(String(decoding: try TutorRequests.createSession(profile, problemID: nil).httpBody!, as: UTF8.self) == "{}")
    }

    @Test func linesRequestIsMultipartWithMetaAndFiles() throws {
        let lines = [LineUpload(index: 0, bbox: CGRect(x: 1, y: 2, width: 3, height: 4), png: Data([1, 2, 3]))]
        let r = try TutorRequests.lines(profile, sessionID: "abc", lines: lines)
        #expect(r.url?.path == "/session/abc/lines")
        #expect(r.value(forHTTPHeaderField: "Content-Type")?.hasPrefix("multipart/form-data; boundary=") == true)
        let body = String(decoding: r.httpBody!, as: UTF8.self)
        #expect(body.contains(#"name="meta""#))
        #expect(body.contains(#"[{"bbox":[1,2,3,4],"index":0}]"#))
        #expect(body.contains(#"name="line_0"; filename="line_0.png""#))
        #expect(body.hasSuffix("--\r\n"))
    }

    @Test func rawBodyRequests() throws {
        #expect(try TutorRequests.question(profile, sessionID: "a", png: Data([9])).value(forHTTPHeaderField: "Content-Type") == "image/png")
        #expect(try TutorRequests.audio(profile, sessionID: "a", wav: Data([9])).value(forHTTPHeaderField: "Content-Type") == "audio/wav")
        #expect(try TutorRequests.admin(profile, action: "tts_backend", value: "kokoro").url?.path == "/admin/tts_backend")
        #expect(throws: TutorAPIError.notConfigured) { try TutorRequests.health(.mock) }
    }

    @Test func healthDecodes() throws {
        let json = #"{"services":{"llm":{"ok":true,"latency_ms":120},"arm":{"ok":false,"detail":"unplugged"}},"tailscale_ip":"100.1.2.3","lan_ip":"192.168.1.5"}"#
        let h = try JSONDecoder().decode(HealthReport.self, from: Data(json.utf8))
        #expect(h.services["llm"] == ServiceStatus(ok: true, latencyMs: 120))
        #expect(h.services["arm"]?.detail == "unplugged")
        #expect(h.tailscaleIP == "100.1.2.3" && h.lanIP == "192.168.1.5")
        #expect(!h.allOK)
    }
}

@Suite struct GeometryTests {
    @Test func partialStrokesGrowByPathLength() {
        let strokes: [[CGPoint]] = [[CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)], [CGPoint(x: 0, y: 5), CGPoint(x: 10, y: 5)]]
        #expect(StrokeGeometry.partial(strokes, progress: 0).isEmpty)
        #expect(StrokeGeometry.partial(strokes, progress: 0.25) == [[CGPoint(x: 0, y: 0), CGPoint(x: 5, y: 0)]])
        #expect(StrokeGeometry.partial(strokes, progress: 0.75).count == 2)
        #expect(StrokeGeometry.partial(strokes, progress: 1) == strokes)
        #expect(StrokeGeometry.partial(strokes, progress: 7) == strokes)
    }

    @Test func circlePicksTheMostCoveredTarget() {
        let targets = [(index: 0, rect: CGRect(x: 0, y: 0, width: 100, height: 50)),
                       (index: 1, rect: CGRect(x: 0, y: 60, width: 100, height: 50))]
        #expect(StrokeGeometry.bestTarget(for: CGRect(x: 0, y: 55, width: 100, height: 60), among: targets) == 1)
        #expect(StrokeGeometry.bestTarget(for: CGRect(x: 500, y: 500, width: 5, height: 5), among: targets) == nil)
    }

    @Test func calibrationLogSnapsTouchesToNearestTarget() {
        let layout = BoardLayout(screenSize: CGSize(width: 1210, height: 834))
        var log = CalibrationLog(layout: layout)
        #expect(log.targets.count == 9)
        let first = CalibrationLog.targets(in: layout)[0]
        log.record(CGPoint(x: first.x + 3, y: first.y - 2), input: "direct")
        #expect(log.touches[0].target == 0)
        #expect(log.layoutFingerprint == "1210x834")
    }

    @Test func questionBoxAndWorkAreaDoNotOverlap() {
        let l = BoardLayout(screenSize: CGSize(width: 1210, height: 834))
        #expect(l.studentRegion.contains(l.questionBox))
        #expect(l.questionBox.maxY <= l.workArea.minY)
        #expect(l.studentRegion.contains(l.workArea))
    }
}

@Suite struct MockServerTests {
    private func next(_ it: inout AsyncStream<StreamItem>.Iterator, matching: (ServerEvent) -> Bool) async -> ServerEvent? {
        while let item = await it.next() {
            if case .event(let e) = item, matching(e) { return e }
        }
        return nil
    }

    @Test func runsATeachStepThenAsksTheCheckQuestion() async throws {
        let server = MockServer(layout: BoardLayout(screenSize: CGSize(width: 1210, height: 834)))
        var it = await server.events(sessionID: "s").makeAsyncIterator()
        await server.send(.chooseMode(.teach))
        guard case let .step(index, of, slot, _, clips, arm)? = await next(&it, matching: { if case .step = $0 { true } else { false } }) else {
            Issue.record("no step"); return
        }
        #expect((index, of, slot) == (1, 5, 1))
        #expect(arm?.strokes.isEmpty == false)
        for clip in clips { await server.send(.audioDone(clip: clip.id)) }
        let ask = await next(&it, matching: { if case .ask = $0 { true } else { false } })
        #expect(ask != nil)
        await server.send(.answerText("two times three"))
        let step2 = await next(&it, matching: { if case .step(let i, _, _, _, _, _) = $0 { i == 2 } else { false } })
        #expect(step2 != nil)
    }

    @Test func checkWithoutWorkAsksForSome() async throws {
        let server = MockServer(layout: BoardLayout(screenSize: CGSize(width: 1210, height: 834)))
        var it = await server.events(sessionID: "s").makeAsyncIterator()
        await server.send(.check)
        let say = await next(&it, matching: { if case .tutorSay = $0 { true } else { false } })
        #expect(say != nil)
    }
}
