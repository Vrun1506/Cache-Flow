import CoreGraphics
import Foundation
import Testing
@testable import CacheFlowKit

@Suite struct EventsTests {
    private func decode(_ json: String) throws -> ServerEvent {
        try JSONDecoder().decode(ServerEvent.self, from: Data(json.utf8))
    }

    @Test func decodesEverySpecExample() throws {
        #expect(try decode(#"{"type":"problem","display":"x² + 5x + 6 = 0","kind":"quadratic","needs_confirm":true}"#)
            == .problem(display: "x² + 5x + 6 = 0", kind: "quadratic", needsConfirm: true))
        #expect(try decode(#"{"type":"phase","mode":"teach","phase":"STEP_WRITE"}"#)
            == .phase(mode: .teach, phase: .stepWrite))
        #expect(try decode(#"{"type":"ask","clip":{"id":"s2c","url":"/audio/s2c.wav","caption":"Why?"},"answer":"voice"}"#)
            == .ask(clip: Clip(id: "s2c", url: "/audio/s2c.wav", caption: "Why?"), answer: .voice))
        #expect(try decode(#"{"type":"tutor_say","caption":"Hi","url":"/audio/x.wav"}"#)
            == .tutorSay(caption: "Hi", url: "/audio/x.wav"))
        #expect(try decode(#"{"type":"read_back","lines":[{"index":1,"expr":"(x+2)*(x+3)=0","ok":false}]}"#)
            == .readBack(lines: [ReadBackLine(index: 1, expr: "(x+2)*(x+3)=0", ok: false)]))
        #expect(try decode(#"{"type":"highlight","line_index":1,"bbox":[40,180,520,70]}"#)
            == .highlight(lineIndex: 1, bbox: CGRect(x: 40, y: 180, width: 520, height: 70)))
        #expect(try decode(#"{"type":"arm","job":"j7","state":"drawing"}"#) == .arm(job: "j7", state: .drawing))
        #expect(try decode(#"{"type":"skills","skills":{"factor_pair":3,"signs":1}}"#)
            == .skills(["factor_pair": 3, "signs": 1]))
    }

    @Test func decodesStepWithArmStrokes() throws {
        let json = #"""
        {"type":"step","index":2,"of":5,"slot":2,"display":"(x+2)(x+3)=0",
         "clips":[{"id":"s2i","url":"/audio/s2i.wav","caption":"a"},{"id":"s2e","url":"/audio/s2e.wav","caption":"b"}],
         "arm":{"job":"j7","strokes":[[[0,0],[10,5]],[[3,3],[4,4]]],"est_s":11}}
        """#
        guard case let .step(index, of, slot, display, clips, arm) = try decode(json) else {
            Issue.record("expected a step event"); return
        }
        #expect((index, of, slot, display) == (2, 5, 2, "(x+2)(x+3)=0"))
        #expect(clips.map(\.id) == ["s2i", "s2e"])
        #expect(arm?.strokes == [[CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 5)], [CGPoint(x: 3, y: 3), CGPoint(x: 4, y: 4)]])
        #expect(arm?.estimatedSeconds == 11)
    }

    @Test func unknownTypesAreIgnoredNotFatal() throws {
        #expect(try decode(#"{"type":"from_the_future","x":1}"#) == .unknown(type: "from_the_future"))
    }

    @Test func malformedPayloadsThrow() {
        #expect(throws: (any Error).self) { try decode(#"{"type":"highlight","line_index":1,"bbox":[1,2]}"#) }
        #expect(throws: (any Error).self) { try decode(#"{"type":"phase","mode":"teach","phase":"NOPE"}"#) }
    }

    @Test func clientEventsEncodeToTheWireShape() throws {
        func json(_ e: ClientEvent) throws -> [String: Any] {
            let data = try JSONEncoder().encode(e)
            return try JSONSerialization.jsonObject(with: data) as! [String: Any]
        }
        #expect(try json(.check) as NSDictionary == ["type": "check"])
        #expect(try json(.chooseMode(.guide)) as NSDictionary == ["type": "choose_mode", "mode": "guide"])
        #expect(try json(.armTouches(job: "j7", count: 12)) as NSDictionary == ["type": "arm_touches", "job": "j7", "count": 12])
        #expect(try json(.mistakeCircled(lineIndex: 2)) as NSDictionary == ["type": "mistake_circled", "line_index": 2])
    }
}
