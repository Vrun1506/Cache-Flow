import CoreGraphics

/// The one place that decides where everything sits on the iPad screen.
///
/// The arm is calibrated against canvas coordinates, so the board must never
/// move or scale. The layout is therefore a pure function of the screen size
/// in points plus the fixed constants below. Calibration data stores
/// `fingerprint` and is rejected if it no longer matches.
///
/// Coordinates: `header`, `board` and `captionBar` are in screen space.
/// `studentRegion`, `tutorStrip` and `slots` are in **board space** (origin at
/// the top-left of the canvas), which is the space the arm is calibrated in.
public struct BoardLayout: Equatable, Sendable, Codable {
    public static let slotCount = 5
    public static let headerHeight: CGFloat = 64
    public static let captionBarHeight: CGFloat = 88
    /// Wide enough for ~10 characters at the ~80 pt (15 mm) height the arm needs
    /// to write legibly (01-plan.md section 8).
    public static let tutorStripWidth: CGFloat = 540
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
    public let tutorStrip: CGRect
    public let slots: [CGRect]

    public init(screenSize: CGSize) {
        self.screenSize = screenSize
        let w = screenSize.width
        let h = screenSize.height
        header = CGRect(x: 0, y: 0, width: w, height: Self.headerHeight)
        captionBar = CGRect(x: 0, y: h - Self.captionBarHeight, width: w, height: Self.captionBarHeight)
        board = CGRect(
            x: 0, y: Self.headerHeight,
            width: w, height: h - Self.headerHeight - Self.captionBarHeight
        )

        let local = CGRect(origin: .zero, size: board.size)
        studentRegion = CGRect(x: 0, y: 0, width: local.width - Self.tutorStripWidth, height: local.height)
        let q = Self.questionBoxInset
        questionBox = CGRect(x: q, y: q, width: studentRegion.width - 2 * q, height: Self.questionBoxHeight)
        workArea = CGRect(x: 0, y: questionBox.maxY + q, width: studentRegion.width,
                          height: studentRegion.height - questionBox.maxY - q)
        tutorStrip = CGRect(x: studentRegion.maxX, y: 0, width: Self.tutorStripWidth, height: local.height)

        let inner = tutorStrip.insetBy(dx: Self.stripInset, dy: Self.stripInset)
        let totalSpacing = Self.slotSpacing * CGFloat(Self.slotCount - 1)
        let slotHeight = (inner.height - totalSpacing) / CGFloat(Self.slotCount)
        slots = (0..<Self.slotCount).map { i in
            CGRect(
                x: inner.minX,
                y: inner.minY + CGFloat(i) * (slotHeight + Self.slotSpacing),
                width: inner.width,
                height: slotHeight
            )
        }
    }

    /// Identifies the geometry a calibration was taken against.
    public var fingerprint: String {
        "\(Int(screenSize.width))x\(Int(screenSize.height))"
    }

    /// Index (0-based) of the tutor slot containing a board-space point.
    public func slotIndex(containing point: CGPoint) -> Int? {
        slots.firstIndex { $0.contains(point) }
    }
}
