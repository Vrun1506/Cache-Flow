import CacheFlowKit
import PencilKit
import UIKit

/// Owns a student `PKCanvasView` and everything that reads its ink. It only
/// ever holds the student's own Pencil strokes: tutor writing is rendered in a
/// separate SwiftUI layer and is never inserted here, so the student's drawing
/// and undo history stay untouched.
@MainActor
final class BoardCanvasController: NSObject, PKCanvasViewDelegate {
    let canvas = PKCanvasView()

    /// Fired after every student edit (stroke added, erased or undone).
    var onDrawingChanged: (() -> Void)?
    /// Pencil down / up, so tutor speech never starts mid-stroke.
    var onPenActive: ((Bool) -> Void)?

    private var programmatic = false

    override init() {
        super.init()
        canvas.delegate = self
    }

    var isEmpty: Bool { canvas.drawing.strokes.isEmpty }

    func configure(input: PenInput) {
        #if targetEnvironment(simulator)
        canvas.drawingPolicy = .anyInput // no Apple Pencil in the Simulator; let the mouse draw
        #else
        canvas.drawingPolicy = input == .pencilOnly ? .pencilOnly : .anyInput
        #endif
        canvas.tool = PKInkingTool(.pen, color: UIColor(ink: .student), width: 4)
    }

    func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) {
        guard !programmatic else { return }
        onDrawingChanged?()
    }

    func canvasViewDidBeginUsingTool(_ canvasView: PKCanvasView) { onPenActive?(true) }
    func canvasViewDidEndUsingTool(_ canvasView: PKCanvasView) { onPenActive?(false) }

    /// Strokes reduced to bounds + owner, ready for `LineSegmenter`.
    func inkStrokes() -> [InkStroke] {
        canvas.drawing.strokes.enumerated().map { i, stroke in
            InkStroke(id: i, bounds: stroke.renderBounds, owner: .student)
        }
    }

    /// A bounded PNG of `rect` (board space) flattened onto white.
    func snapshotPNG(of rect: CGRect, maxPixels: CGFloat = 2048) -> Data? {
        guard rect.width > 0, rect.height > 0 else { return nil }
        let scale = min(2, maxPixels / max(rect.width, rect.height))
        return Self.flatten(canvas.drawing, in: rect, scale: scale)
    }

    /// Everything on the canvas as one PNG on white, or nil if empty.
    func flattenedPNG() -> Data? {
        let drawing = canvas.drawing
        guard !drawing.strokes.isEmpty else { return nil }
        return Self.flatten(drawing, in: drawing.bounds.insetBy(dx: -16, dy: -16), scale: 2)
    }

    /// PencilKit renders transparent; vision models misread black-on-transparent.
    private static func flatten(_ drawing: PKDrawing, in rect: CGRect, scale: CGFloat) -> Data? {
        let ink = drawing.image(from: rect, scale: scale)
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        return UIGraphicsImageRenderer(size: rect.size, format: format).image { ctx in
            UIColor.white.setFill()
            ctx.fill(CGRect(origin: .zero, size: rect.size))
            ink.draw(at: .zero)
        }.pngData()
    }

    func clearAll() {
        programmatic = true
        canvas.drawing = PKDrawing()
        programmatic = false
        onDrawingChanged?()
    }
}

extension UIColor {
    convenience init(ink: InkColor) { self.init(red: ink.red, green: ink.green, blue: ink.blue, alpha: 1) }
}
