import CoreGraphics
import Testing
@testable import CacheFlowKit

@Suite struct BoardLayoutTests {
    // 11-inch iPad Pro (M4), landscape, in points.
    let layout = BoardLayout(screenSize: CGSize(width: 1210, height: 834))

    @Test func regionsTileTheScreenWithoutOverlap() {
        #expect(layout.header.maxY == layout.board.minY)
        #expect(layout.board.maxY == layout.captionBar.minY)
        #expect(layout.captionBar.maxY == 834)
        #expect(layout.studentRegion.maxX == layout.tutorStrip.minX)
        #expect(layout.tutorStrip.maxX == layout.board.width)
    }

    @Test func fiveSlotsStayInsideTheStripInOrder() {
        #expect(layout.slots.count == BoardLayout.slotCount)
        for (a, b) in zip(layout.slots, layout.slots.dropFirst()) { #expect(a.maxY < b.minY) }
        for slot in layout.slots { #expect(layout.tutorStrip.contains(slot)) }
    }

    @Test func slotLookupUsesBoardSpace() {
        let centre = CGPoint(x: layout.slots[2].midX, y: layout.slots[2].midY)
        #expect(layout.slotIndex(containing: centre) == 2)
        #expect(layout.slotIndex(containing: CGPoint(x: 10, y: 10)) == nil)
    }

    @Test func layoutIsDeterministicAndFingerprinted() {
        #expect(layout == BoardLayout(screenSize: CGSize(width: 1210, height: 834)))
        #expect(layout.fingerprint == "1210x834")
        #expect(layout.fingerprint != BoardLayout(screenSize: CGSize(width: 1180, height: 820)).fingerprint)
    }
}
