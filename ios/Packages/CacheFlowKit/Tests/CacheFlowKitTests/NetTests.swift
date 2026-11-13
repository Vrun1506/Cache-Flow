import CoreGraphics
import Foundation
import Testing
@testable import CacheFlowKit

@Suite struct NetTests {
    let profile = ServerProfile(id: "t", name: "T", baseURL: "https://tutor-spark:8443")

    @Test func profilesBuildV2URLs() {
        #expect(profile.url(path: "/v2/health")?.absoluteString == "https://tutor-spark:8443/v2/health")
        #expect(profile.eventsURL(sessionID: "abc", afterSeq: nil)?.absoluteString == "wss://tutor-spark:8443/v2/sessions/abc/events")
        #expect(profile.eventsURL(sessionID: "abc", afterSeq: 41)?.absoluteString
            == "wss://tutor-spark:8443/v2/sessions/abc/events?after_seq=41")
        let plain = ServerProfile(id: "p", name: "P", baseURL: "http://10.0.0.2:8000/")
        #expect(plain.eventsURL(sessionID: "a", afterSeq: nil)?.absoluteString == "ws://10.0.0.2:8000/v2/sessions/a/events")
        #expect(profile.resolve("/v2/audio/t1.wav")?.absoluteString == "https://tutor-spark:8443/v2/audio/t1.wav")
        #expect(ServerProfile.mock.url(path: "/v2/health") == nil)
        #expect(ServerProfile(id: "x", name: "x", baseURL: "not a url").url(path: "/h") == nil)
    }

    @Test func everyRequestCarriesTheProtocolVersion() throws {
        let r = try TutorRequests.health(profile)
        #expect(r.value(forHTTPHeaderField: "X-Socrates-Protocol") == "2")
        #expect(r.url?.path == "/v2/health")
        #expect(throws: TutorAPIError.notConfigured) { try TutorRequests.health(.mock) }
    }

    @Test func questionRequestIsMultipartWithRevisionAndRequestID() throws {
        let image = try TutorRequests.question(profile, sessionID: "s", .image(png: Data([1, 2])), boardRevision: 3, requestID: "r1")
        let body = String(decoding: image.httpBody!, as: UTF8.self)
        #expect(image.url?.path == "/v2/sessions/s/question")
        #expect(body.contains(#"name="board_revision""#) && body.contains("\r\n\r\n3\r\n"))
        #expect(body.contains(#"name="request_id""#) && body.contains("\r\n\r\nr1\r\n"))
        #expect(body.contains(#"name="image"; filename="question.png""#))
        let typed = try TutorRequests.question(profile, sessionID: "s", .text("2x + 3 = 11"), boardRevision: 0, requestID: "r2")
        #expect(String(decoding: typed.httpBody!, as: UTF8.self).contains("2x + 3 = 11"))
    }

    @Test func boardRequestIsBoundedAndCarriesLineBoxes() throws {
        let snap = BoardSnapshot(boardRevision: 4, imageRegion: CGRect(x: 0, y: 156, width: 700, height: 480), png: Data([7]),
                                 lines: [Region(lineIndex: 0, bbox: CGRect(x: 1, y: 2, width: 3, height: 4))])
        let r = try TutorRequests.board(profile, sessionID: "s", snap, requestID: "r9")
        let body = String(decoding: r.httpBody!, as: UTF8.self)
        #expect(r.url?.path == "/v2/sessions/s/board")
        #expect(body.contains(#"{"board_revision":4,"image_region":[0,156,700,480],"lines":[{"bbox":[1,2,3,4],"line_index":0}],"request_id":"r9"}"#))
        #expect(body.contains(#"name="image"; filename="board.png""#))
        var big = snap
        big.png = Data(count: BoardSnapshot.maxBytes + 1)
        #expect(throws: TutorAPIError.tooLarge) { try TutorRequests.board(profile, sessionID: "s", big, requestID: "r") }
    }

    @Test func audioAndLifecycleRequests() throws {
        let a = try TutorRequests.audio(profile, sessionID: "s", wav: Data([1]), boardRevision: 2, requestID: "r")
        #expect(a.url?.path == "/v2/sessions/s/audio")
        #expect(String(decoding: a.httpBody!, as: UTF8.self).contains(#"filename="utterance.wav""#))
        #expect(try TutorRequests.summary(profile, sessionID: "s").httpMethod == "GET")
        let d = try TutorRequests.delete(profile, sessionID: "s")
        #expect(d.httpMethod == "DELETE" && d.url?.path == "/v2/sessions/s")
    }
}

@Suite struct GeometryTests {
    @Test func partialStrokesGrowByPathLength() {
        let strokes: [[CGPoint]] = [[CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)], [CGPoint(x: 0, y: 5), CGPoint(x: 10, y: 5)]]
        #expect(StrokeGeometry.partial(strokes, progress: 0).isEmpty)
        #expect(StrokeGeometry.partial(strokes, progress: 0.25) == [[CGPoint(x: 0, y: 0), CGPoint(x: 5, y: 0)]])
        #expect(StrokeGeometry.partial(strokes, progress: 0.75).count == 2)
        #expect(StrokeGeometry.partial(strokes, progress: 1) == strokes)
    }

    @Test func questionBoxAndWorkAreaDoNotOverlap() {
        let l = BoardLayout(screenSize: CGSize(width: 1210, height: 834))
        #expect(l.studentRegion.contains(l.questionBox))
        #expect(l.questionBox.maxY <= l.workArea.minY)
        #expect(l.studentRegion.contains(l.workArea))
        #expect(l.boardBounds.contains(l.tutorStrip))
    }

    @Test func studentCanvasIsAlwaysPencilOnly() {
        #expect(CanvasPolicy.studentInput == .pencilOnly)
    }
}
