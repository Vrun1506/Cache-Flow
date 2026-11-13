import CoreGraphics

/// The one place that decides where everything sits on the iPad screen.
///
/// Board-space coordinates (origin at the top-left of the board) are shared by
/// the student canvas, uploaded line boxes and every tutor annotation, so the
/// layout is a pure function of the screen size. The tutor strip on the right
/// is the first digital writing region (Interface.md: it no longer needs
/// physical calibration and can later resize).
///
/// `header`, `board` and `captionBar` are in screen space; `studentRegion`,
/// `questionBox`, `workArea`, `tutorStrip` and `slots` are in board space.
public struct BoardLayout: Equatable, Sendable, Codable {
    public static let slotCount = 5
    public static let headerHeight: CGFloat = 64
    /// Caption line plus the action bar.
    public static let captionBarHeight: CGFloat = 132
    public static let tutorStripWidth: CGFloat = 500
    public static let questionBoxHeight: CGFloat = 132
    public static let questionBoxInset: CGFloat = 12
    public static let slotSpacing: CGFloat = 8
    public static let stripInset: CGFloat = 12

    public let screenSize: CGSize
    public let header: CGRect
    public let board: CGRect
    public let captionBar: CGRect
    /// The whole left-hand area.
    public let studentRegion: CGRect
    /// Where the student writes the problem (its own small canvas).
    public let questionBox: CGRect
    /// The student's working area; line segmentation only looks here.
    public let workArea: CGRect
    /// The tutor's writing region.
    public let tutorStrip: CGRect
    /// Rows inside the tutor strip for written steps and diagrams.
    public let slots: [CGRect]

    public init(screenSize: CGSize) {
        self.screenSize = screenSize
        let w = screenSize.width
        let h = screenSize.height
        header = CGRect(x: 0, y: 0, width: w, height: Self.headerHeight)
        captionBar = CGRect(x: 0, y: h - Self.captionBarHeight, width: w, height: Self.captionBarHeight)
        board = CGRect(x: 0, y: Self.headerHeight, width: w, height: max(0, h - Self.headerHeight - Self.captionBarHeight))

        let local = CGRect(origin: .zero, size: board.size)
        let stripWidth = min(Self.tutorStripWidth, local.width * 0.45)
        studentRegion = CGRect(x: 0, y: 0, width: local.width - stripWidth, height: local.height)
        let q = Self.questionBoxInset
        questionBox = CGRect(x: q, y: q, width: studentRegion.width - 2 * q, height: Self.questionBoxHeight)
        workArea = CGRect(x: 0, y: questionBox.maxY + q, width: studentRegion.width,
                          height: max(0, studentRegion.height - questionBox.maxY - q))
        tutorStrip = CGRect(x: studentRegion.maxX, y: 0, width: stripWidth, height: local.height)

        let inner = tutorStrip.insetBy(dx: Self.stripInset, dy: Self.stripInset)
        let totalSpacing = Self.slotSpacing * CGFloat(Self.slotCount - 1)
        let slotHeight = max(0, (inner.height - totalSpacing) / CGFloat(Self.slotCount))
        slots = (0..<Self.slotCount).map { i in
            CGRect(x: inner.minX, y: inner.minY + CGFloat(i) * (slotHeight + Self.slotSpacing), width: inner.width, height: slotHeight)
        }
    }

    /// Board-space bounds of the whole board.
    public var boardBounds: CGRect { CGRect(origin: .zero, size: board.size) }

    /// Identifies the geometry (used to rebuild views when the size changes).
    public var fingerprint: String { "\(Int(screenSize.width))x\(Int(screenSize.height))" }

    /// Index (0-based) of the tutor slot containing a board-space point.
    public func slotIndex(containing point: CGPoint) -> Int? {
        slots.firstIndex { $0.contains(point) }
    }
}
