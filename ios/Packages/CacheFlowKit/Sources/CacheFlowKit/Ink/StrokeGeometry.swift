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
}
