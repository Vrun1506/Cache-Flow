/// Which input the canvas accepts and whose ink it lays down, per phase.
///
/// Student phases are `.pencilOnly` so a resting palm leaves no ink. While the
/// arm writes, its passive stylus must register, so the canvas accepts any
/// input and the pen switches to tutor ink.
public enum PenInput: Sendable, Equatable {
    case pencilOnly
    case anyInput
}

public enum CanvasPolicy {
    public static func input(for phase: Phase) -> PenInput {
        switch phase {
        case .stepWrite, .armStep, .spotMistake: .anyInput
        default: .pencilOnly
        }
    }

    public static func ink(for phase: Phase) -> InkOwner {
        switch phase {
        case .stepWrite, .armStep: .tutor
        default: .student
        }
    }
}
