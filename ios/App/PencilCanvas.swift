import CacheFlowKit
import PencilKit
import SwiftUI

/// Hosts a controller's student `PKCanvasView`. Scrolling and zoom are off and
/// the frame is fixed by the caller so board-space coordinates stay stable.
struct PencilCanvas: UIViewRepresentable {
    let controller: BoardCanvasController
    var input: PenInput = CanvasPolicy.studentInput

    func makeUIView(context: Context) -> PKCanvasView {
        let canvas = controller.canvas
        canvas.backgroundColor = .clear
        canvas.isOpaque = false
        canvas.isScrollEnabled = false
        canvas.bounces = false
        canvas.minimumZoomScale = 1
        canvas.maximumZoomScale = 1
        controller.configure(input: input)
        return canvas
    }

    func updateUIView(_ canvas: PKCanvasView, context: Context) {
        controller.configure(input: input)
    }
}
