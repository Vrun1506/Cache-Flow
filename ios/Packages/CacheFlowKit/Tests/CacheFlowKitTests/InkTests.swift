import CoreGraphics
import Testing
@testable import CacheFlowKit

@Suite struct InkTests {
    @Test func colourMapsToOwner() {
        #expect(InkColor.student.owner == .student)
        #expect(InkColor.tutor.owner == .tutor)
        #expect(InkColor(red: 0.1, green: 0.1, blue: 0.1).owner == .student)
        #expect(InkColor(red: 0, green: 0.4, blue: 1).owner == .tutor)
    }

    private func stroke(_ id: Int, y: CGFloat, h: CGFloat = 40, owner: InkOwner = .student, x: CGFloat = 50) -> InkStroke {
        InkStroke(id: id, bounds: CGRect(x: x, y: y, width: 60, height: h), owner: owner)
    }

    @Test func groupsStrokesIntoLinesTopToBottom() {
        // Handed over out of order, with two strokes on each of two lines.
        let lines = LineSegmenter().segment([
            stroke(3, y: 300), stroke(1, y: 100), stroke(4, y: 310), stroke(2, y: 105),
        ])
        #expect(lines.map(\.strokeIDs) == [[1, 2], [3, 4]])
        #expect(lines.map(\.index) == [0, 1])
    }

    @Test func tutorInkNeverCountsAsStudentWork() {
        let lines = LineSegmenter().segment([stroke(1, y: 100), stroke(2, y: 200, owner: .tutor)])
        #expect(lines.count == 1)
        #expect(lines[0].strokeIDs == [1])
    }

    @Test func verticallyOverlappingStrokesMerge() {
        // A tall fraction bar and the digits beside it belong to one line.
        let lines = LineSegmenter().segment([stroke(1, y: 100, h: 120), stroke(2, y: 140, h: 30, x: 200)])
        #expect(lines.count == 1)
    }

    @Test func regionExcludesStrokesOutsideTheStudentArea() {
        let seg = LineSegmenter(region: CGRect(x: 0, y: 0, width: 400, height: 600))
        let lines = seg.segment([stroke(1, y: 100), stroke(2, y: 100, x: 800)])
        #expect(lines.flatMap(\.strokeIDs) == [1])
    }

    @Test func lineBoundsAreTheUnionOfItsStrokes() {
        let lines = LineSegmenter().segment([stroke(1, y: 100), stroke(2, y: 130, x: 200)])
        #expect(lines[0].bounds == CGRect(x: 50, y: 100, width: 210, height: 70))
    }

    @Test func emptyInputGivesNoLines() {
        #expect(LineSegmenter().segment([]).isEmpty)
    }
}
