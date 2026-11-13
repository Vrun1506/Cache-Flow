import CacheFlowKit
import PencilKit
import SwiftUI

/// Hosts a controller's `PKCanvasView`. Scrolling and zoom are off and the
/// frame is fixed by the caller: anything that moves the canvas breaks calibration.
struct PencilCanvas: UIViewRepresentable {
    let controller: BoardCanvasController
    let input: PenInput
    let ink: InkOwner

    func makeUIView(context: Context) -> PKCanvasView {
        let canvas = controller.canvas
        canvas.backgroundColor = .clear
        canvas.isOpaque = false
        canvas.isScrollEnabled = false
        canvas.bounces = false
        canvas.minimumZoomScale = 1
        canvas.maximumZoomScale = 1
        controller.configure(input: input, ink: ink)
        return canvas
    }

    func updateUIView(_ canvas: PKCanvasView, context: Context) {
        controller.configure(input: input, ink: ink)
    }
}
