import XCTest

/// Simulator end-to-end test of the full v2 lesson against the in-process
/// mock (`-uitest`): handwritten question with ambiguous read-back, Tutor me,
/// sign error, ambiguous handwriting corrected by typing, partial root, lost
/// audio with caption fallback, push-to-talk, cancellation, stale ink,
/// reconnect without speech replay, Show the answer and an independent check.
/// No Spark is involved; this proves the iPad's contract handling only.
@MainActor
final class LessonE2ETests: XCTestCase {
    private var app: XCUIApplication!

    override func setUpWithError() throws {
        continueAfterFailure = false
        app = XCUIApplication()
        app.launchArguments = ["-uitest"]
        app.launch()
        XCUIDevice.shared.orientation = .landscapeLeft
    }

    // MARK: Helpers

    private func el(_ id: String) -> XCUIElement { app.descendants(matching: .any).matching(identifier: id).firstMatch }

    @discardableResult
    private func waitFor(_ id: String, timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line) -> XCUIElement {
        let e = el(id)
        XCTAssertTrue(e.waitForExistence(timeout: timeout), "missing \(id)", file: file, line: line)
        return e
    }

    private func wait(_ id: String, labelContains text: String, timeout: TimeInterval = 10,
                      file: StaticString = #filePath, line: UInt = #line) {
        let e = waitFor(id, timeout: timeout, file: file, line: line)
        let p = NSPredicate(format: "label CONTAINS %@", text)
        let r = XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: p, object: e)], timeout: timeout)
        XCTAssertEqual(r, .completed, "\(id) never contained “\(text)”; was “\(e.label)”", file: file, line: line)
    }

    private func status() -> [String: String] {
        var out: [String: String] = [:]
        for pair in el("debug.status").label.split(separator: " ") {
            let kv = pair.split(separator: "=", maxSplits: 1)
            if kv.count == 2 { out[String(kv[0])] = String(kv[1]) }
        }
        return out
    }

    private func number(_ key: String) -> Int { Int(status()[key] ?? "") ?? -1 }

    private func waitStatus(_ what: String, timeout: TimeInterval = 10, file: StaticString = #filePath, line: UInt = #line,
                            _ predicate: @escaping ([String: String]) -> Bool) {
        let deadline = Date().addingTimeInterval(timeout)
        while Date() < deadline {
            if predicate(status()) { return }
            RunLoop.current.run(until: Date().addingTimeInterval(0.1))
        }
        XCTFail("timed out waiting for \(what); status: \(el("debug.status").label)", file: file, line: line)
    }

    private func phase(_ title: String, file: StaticString = #filePath, line: UInt = #line) {
        wait("phase.chip", labelContains: title, file: file, line: line)
    }

    private func idle() { waitStatus("tutor idle") { $0["speaking"] == "0" && $0["phase"] != "tutoring" } }

    /// Draw a zig-zag stroke across `element` at normalized height `y`.
    private func scribble(on element: XCUIElement, y: CGFloat, from x0: CGFloat = 0.06, to x1: CGFloat = 0.3) {
        let start = element.coordinate(withNormalizedOffset: CGVector(dx: x0, dy: y))
        let end = element.coordinate(withNormalizedOffset: CGVector(dx: x1, dy: y + 0.02))
        start.press(forDuration: 0.05, thenDragTo: end)
    }

    private func tap(_ id: String, file: StaticString = #filePath, line: UInt = #line) {
        let e = waitFor(id, file: file, line: line)
        e.tap()
    }

    // MARK: The lesson

    func testFullLessonWithFaults() throws {
        waitStatus("connected") { $0["conn"] == "connected" }

        // Handwritten question, ambiguous read-back, confirm the first reading.
        let question = waitFor("question.canvas")
        scribble(on: question, y: 0.5, from: 0.1, to: 0.6)
        tap("question.read")
        wait("readback.text", labelContains: "x² + 5x + 6 = 0")
        tap("readback.alt.0")
        phase("Ready")
        wait("problem.text", labelContains: "x² + 5x + 6 = 0")

        // Tutor me: one focused question, captioned.
        tap("action.tutor")
        wait("caption.text", labelContains: "multiply to 6")
        phase("Your turn")
        idle()

        // Line 1: (x − 2)(x − 3) = 0 → invalid / sign mismatch with a tutor circle.
        let board = waitFor("board.canvas")
        let inkBefore = number("ink")
        scribble(on: board, y: 0.36)
        tap("action.check")
        wait("evidence.latest", labelContains: "check the signs")
        waitStatus("highlight ink committed") { Int($0["ink"] ?? "") == inkBefore + 1 }
        idle()

        // Line 2: ambiguous sign → not graded; the student corrects it by typing.
        scribble(on: board, y: 0.5)
        tap("action.check")
        wait("readback.text", labelContains: "(x + 2)(x + 3) = 0")
        let edit = waitFor("readback.edit")
        edit.tap()
        edit.typeText("(x + 2)(x + 3) = 0")
        tap("readback.useEdit")
        wait("evidence.latest", labelContains: "Checked: correct")
        idle()

        // Line 3: x = −2 → partial root.
        scribble(on: board, y: 0.64, to: 0.18)
        tap("action.check")
        wait("evidence.latest", labelContains: "one root found")
        idle()

        // Lost audio: the hint stays captioned and the fallback is shown.
        tap("debug.loseAudio")
        tap("action.hint")
        wait("caption.text", labelContains: "Hint:")
        waitFor("audio.fallback")
        waitStatus("fallback counted") { Int($0["fallback"] ?? "") ?? 0 >= 1 }
        idle()

        // Push-to-talk: the transcript must be confirmed before it counts.
        waitFor("talk.button").press(forDuration: 1.0)
        wait("readback.text", labelContains: "other root")
        tap("readback.alt.0")
        wait("caption.text", labelContains: "product is zero")
        idle()

        // Cancellation: stop a long explanation; its ink never lands.
        let inkBeforeExplain = number("ink")
        tap("action.explain")
        tap("action.cancel")
        phase("Your turn")
        RunLoop.current.run(until: Date().addingTimeInterval(4))
        XCTAssertEqual(number("ink"), inkBeforeExplain, "cancelled turn committed ink")
        XCTAssertFalse(el("tutor.ink.preview").exists)
        idle()

        // Stale ink: rejected against the old revision, re-issued once.
        let inkBeforeStale = number("ink")
        tap("debug.staleInk")
        tap("action.hint")
        waitStatus("stale rejected and recovered") { $0["stale"] == "1" && Int($0["ink"] ?? "") == inkBeforeStale + 1 }
        idle()

        // Reconnect: drop the stream, ask for a drawing, resume from the cursor without replaying speech.
        let spokenBefore = number("speech") + number("fallback")
        tap("debug.dropConnection")
        waitStatus("reconnecting") { $0["conn"] == "reconnecting" }
        tap("action.draw")
        waitStatus("reconnected with the scene", timeout: 15) {
            $0["conn"] == "connected" && $0["reconnects"] == "1" && $0["scenes"] == "1"
        }
        waitFor("tutor.scene")
        XCTAssertEqual(number("speech") + number("fallback"), spokenBefore, "speech was replayed after reconnect")
        XCTAssertGreaterThanOrEqual(number("replaySuppressed"), 1)
        idle()

        // Show the answer: assisted, then a fresh independent item.
        tap("action.showAnswer")
        wait("caption.text", labelContains: "on your own")
        phase("On your own")
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS 'helped'")).firstMatch.exists)
        idle()

        // Independent check answered by typing.
        let reply = waitFor("reply.field")
        reply.tap()
        reply.typeText("x = -3 or x = -4")
        tap("reply.send")
        phase("Complete")
        wait("caption.text", labelContains: "without any help")
        tap("summary.button")
        waitFor("summary.sheet")
        let independent = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS 'Independent'")).firstMatch
        XCTAssertTrue(independent.waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS 'Helped'")).firstMatch.exists)
    }
}
