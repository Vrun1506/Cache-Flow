import CoreGraphics

/// Who made a stroke. The student writes in black; during arm phases the app
/// switches the pen to blue "tutor ink", so the tutor's writing is never
/// mistaken for the student's work when lines are segmented.
public enum InkOwner: String, Sendable, Equatable {
    case student
    case tutor
}

public struct InkColor: Equatable, Sendable {
    public var red: Double
    public var green: Double
    public var blue: Double

    public init(red: Double, green: Double, blue: Double) {
        self.red = red
        self.green = green
        self.blue = blue
    }

    public static let student = InkColor(red: 0, green: 0, blue: 0)
    public static let tutor = InkColor(red: 0.05, green: 0.35, blue: 0.9)

    public var owner: InkOwner {
        func dist(_ a: InkColor, _ b: InkColor) -> Double {
            let dr = a.red - b.red, dg = a.green - b.green, db = a.blue - b.blue
            return dr * dr + dg * dg + db * db
        }
        return dist(self, .tutor) < dist(self, .student) ? .tutor : .student
    }

    public static func color(for owner: InkOwner) -> InkColor {
        owner == .tutor ? .tutor : .student
    }
}

/// A stroke reduced to what segmentation needs. The app converts `PKStroke`
/// into this, which keeps the algorithm testable without PencilKit.
public struct InkStroke: Equatable, Sendable {
    public var id: Int
    public var bounds: CGRect
    public var owner: InkOwner

    public init(id: Int, bounds: CGRect, owner: InkOwner) {
        self.id = id
        self.bounds = bounds
        self.owner = owner
    }
}

public struct InkLine: Equatable, Sendable {
    public var index: Int
    public var strokeIDs: [Int]
    public var bounds: CGRect
}

/// Groups the student's strokes into lines of working, top to bottom.
public struct LineSegmenter: Sendable {
    /// A stroke starts a new line when its top is more than this far below the
    /// current line's bottom (points).
    public var lineGap: CGFloat
    /// Only strokes whose centre lies inside this board-space region count.
    public var region: CGRect?

    public init(lineGap: CGFloat = 35, region: CGRect? = nil) {
        self.lineGap = lineGap
        self.region = region
    }

    public func segment(_ strokes: [InkStroke]) -> [InkLine] {
        let student = strokes
            .filter { $0.owner == .student }
            .filter { s in region.map { $0.contains(CGPoint(x: s.bounds.midX, y: s.bounds.midY)) } ?? true }
            .sorted { $0.bounds.midY < $1.bounds.midY }

        var lines: [InkLine] = []
        for stroke in student {
            if let last = lines.last, stroke.bounds.minY - last.bounds.maxY <= lineGap {
                lines[lines.count - 1].strokeIDs.append(stroke.id)
                lines[lines.count - 1].bounds = last.bounds.union(stroke.bounds)
            } else {
                lines.append(InkLine(index: lines.count, strokeIDs: [stroke.id], bounds: stroke.bounds))
            }
        }
        return lines
    }
}
