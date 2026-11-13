/// Which input the student canvas accepts. In v2 the student canvas is always
/// Pencil-only (a resting palm leaves no ink) and only ever holds student ink;
/// tutor writing is rendered in its own layer, never through touches.
public enum PenInput: Sendable, Equatable {
    case pencilOnly
    case anyInput
}

public enum CanvasPolicy {
    public static let studentInput: PenInput = .pencilOnly
}
