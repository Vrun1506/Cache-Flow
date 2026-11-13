import CoreGraphics

public enum StrokeGeometry {
    /// The polylines as they should look after `progress` (0...1) of the total
    /// drawing time, measured by path length, so the pen moves at constant speed.
    public static func partial(_ strokes: [[CGPoint]], progress: Double) -> [[CGPoint]] {
        let p = min(max(progress, 0), 1)
        if p >= 1 { return strokes }
        let lengths = strokes.map(length)
        var remaining = lengths.reduce(0, +) * p
        var out: [[CGPoint]] = []
        for (stroke, len) in zip(strokes, lengths) {
            if remaining <= 0 { break }
            if remaining >= len { out.append(stroke); remaining -= len; continue }
            var part: [CGPoint] = stroke.first.map { [$0] } ?? []
            for (a, b) in zip(stroke, stroke.dropFirst()) {
                let seg = hypot(b.x - a.x, b.y - a.y)
                if remaining >= seg { part.append(b); remaining -= seg; continue }
                let t = seg == 0 ? 0 : remaining / seg
                part.append(CGPoint(x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t))
                break
            }
            out.append(part)
            break
        }
        return out
    }

    public static func length(_ stroke: [CGPoint]) -> Double {
        zip(stroke, stroke.dropFirst()).reduce(0) { $0 + hypot($1.1.x - $1.0.x, $1.1.y - $1.0.y) }
    }

    /// Which candidate a lasso/circle most covers (largest intersection area),
    /// or nil if it touches none. Used by spot-the-mistake.
    public static func bestTarget(for circle: CGRect, among targets: [(index: Int, rect: CGRect)]) -> Int? {
        var best: (Int, CGFloat)?
        for t in targets {
            let i = circle.intersection(t.rect)
            let area = i.isNull ? 0 : i.width * i.height
            if area > 0, area > (best?.1 ?? 0) { best = (t.index, area) }
        }
        return best?.0
    }
}

/// Hidden calibration screen support (01-plan.md section 8): a 3x3 grid of
/// targets in the tutor strip, and a log of where touches actually landed.
public struct CalibrationLog: Codable, Equatable, Sendable {
    public struct Touch: Codable, Equatable, Sendable {
        public var x: Double, y: Double
        public var input: String
        public var target: Int?
    }
    public var layoutFingerprint: String
    public var targets: [[Double]]
    public var touches: [Touch] = []

    public init(layout: BoardLayout) {
        layoutFingerprint = layout.fingerprint
        targets = Self.targets(in: layout).map { [$0.x, $0.y] }
    }

    public static func targets(in layout: BoardLayout) -> [CGPoint] {
        let r = layout.tutorStrip.insetBy(dx: 60, dy: 60)
        return (0..<3).flatMap { row in
            (0..<3).map { col in
                CGPoint(x: r.minX + r.width * Double(col) / 2, y: r.minY + r.height * Double(row) / 2)
            }
        }
    }

    public mutating func record(_ p: CGPoint, input: String) {
        let nearest = targets.enumerated().min {
            hypot($0.element[0] - p.x, $0.element[1] - p.y) < hypot($1.element[0] - p.x, $1.element[1] - p.y)
        }?.offset
        touches.append(Touch(x: p.x, y: p.y, input: input, target: nearest))
    }
}
