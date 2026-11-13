import CacheFlowKit
import PencilKit
import UIKit

/// Sees every touch without interfering with PencilKit. Used to count the
/// arm's stylus touches and to log calibration points from any input type.
private final class TouchLogger: UIGestureRecognizer {
    var onTouch: ((CGPoint, UITouch.TouchType) -> Void)?

    override func touchesBegan(_ touches: Set<UITouch>, with event: UIEvent) {
        for t in touches { onTouch?(t.location(in: view), t.type) }
        state = .failed
    }
}

/// Owns a `PKCanvasView` and everything that reads or edits its ink. The main
/// board uses one spanning the whole board so student ink, arm ink and
/// calibration touches share a coordinate space; the question box uses a
/// second, small one.
@MainActor
final class BoardCanvasController: NSObject, PKCanvasViewDelegate {
    let canvas = PKCanvasView()

    var onTouch: ((CGPoint, UITouch.TouchType) -> Void)? {
        didSet { logger.onTouch = onTouch }
    }
    /// Fired for each new stroke the user draws (not for programmatic ones).
    var onStrokeAdded: ((CGRect) -> Void)?
    var onDrawingChanged: (() -> Void)?

    private let logger = TouchLogger()
    private var strokeCount = 0
    private var programmatic = false

    override init() {
        super.init()
        canvas.delegate = self
        logger.cancelsTouchesInView = false
        logger.delaysTouchesBegan = false
        logger.allowedTouchTypes = [0, 1, 2, 3].map { NSNumber(value: $0) }
        canvas.addGestureRecognizer(logger)
    }

    var isEmpty: Bool { canvas.drawing.strokes.isEmpty }

    func configure(input: PenInput, ink: InkOwner) {
        #if targetEnvironment(simulator)
        canvas.drawingPolicy = .anyInput // no Apple Pencil in the Simulator; let the mouse draw
        #else
        canvas.drawingPolicy = input == .pencilOnly ? .pencilOnly : .anyInput
        #endif
        canvas.tool = PKInkingTool(.pen, color: UIColor(ink: .color(for: ink)), width: 4)
    }

    func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) {
        guard !programmatic else { return }
        let strokes = canvasView.drawing.strokes
        if strokes.count > strokeCount, let last = strokes.last { onStrokeAdded?(last.renderBounds) }
        strokeCount = strokes.count
        onDrawingChanged?()
    }

    /// Strokes reduced to bounds + owner, ready for `LineSegmenter`.
    func inkStrokes() -> [InkStroke] {
        canvas.drawing.strokes.enumerated().map { i, stroke in
            var r: CGFloat = 0, g: CGFloat = 0, b: CGFloat = 0, a: CGFloat = 0
            stroke.ink.color.getRed(&r, green: &g, blue: &b, alpha: &a)
            return InkStroke(id: i, bounds: stroke.renderBounds, owner: InkColor(red: r, green: g, blue: b).owner)
        }
    }

    /// One PNG per line, flattened onto white.
    func lineUploads(for lines: [InkLine]) -> [LineUpload] {
        let strokes = canvas.drawing.strokes
        return lines.compactMap { line in
            let drawing = PKDrawing(strokes: line.strokeIDs.map { strokes[$0] })
            guard let png = Self.flatten(drawing, in: line.bounds.insetBy(dx: -12, dy: -12)) else { return nil }
            return LineUpload(index: line.index, bbox: line.bounds, png: png)
        }
    }

    /// Everything on the canvas as one PNG on white, or nil if empty.
    func flattenedPNG() -> Data? {
        let drawing = canvas.drawing
        guard !drawing.strokes.isEmpty else { return nil }
        return Self.flatten(drawing, in: drawing.bounds.insetBy(dx: -16, dy: -16))
    }

    /// PencilKit renders transparent; vision models misread black-on-transparent.
    private static func flatten(_ drawing: PKDrawing, in rect: CGRect, scale: CGFloat = 2) -> Data? {
        let ink = drawing.image(from: rect, scale: scale)
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        return UIGraphicsImageRenderer(size: rect.size, format: format).image { ctx in
            UIColor.white.setFill()
            ctx.fill(CGRect(origin: .zero, size: rect.size))
            ink.draw(at: .zero)
        }.pngData()
    }

    /// Write polylines onto the canvas in tutor ink (the ghost-ink fallback).
    func addTutorStrokes(_ polylines: [[CGPoint]]) {
        let ink = PKInk(.pen, color: UIColor(ink: .tutor))
        let strokes = polylines.compactMap { points -> PKStroke? in
            guard points.count > 1 else { return nil }
            let control = points.enumerated().map { i, p in
                PKStrokePoint(location: p, timeOffset: Double(i) * 0.01, size: CGSize(width: 4, height: 4),
                              opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
            }
            return PKStroke(ink: ink, path: PKStrokePath(controlPoints: control, creationDate: Date()))
        }
        replace(canvas.drawing.appending(PKDrawing(strokes: strokes)))
    }

    /// Wipe the tutor's blue ink and keep the student's work.
    func clearTutorInk() {
        let all = canvas.drawing.strokes
        replace(PKDrawing(strokes: inkStrokes().filter { $0.owner == .student }.map { all[$0.id] }))
    }

    func clearAll() { replace(PKDrawing()) }

    private func replace(_ drawing: PKDrawing) {
        programmatic = true
        canvas.drawing = drawing
        strokeCount = drawing.strokes.count
        programmatic = false
        onDrawingChanged?()
    }
}

extension UIColor {
    convenience init(ink: InkColor) { self.init(red: ink.red, green: ink.green, blue: ink.blue, alpha: 1) }
}
