import Testing
@testable import CacheFlowKit

@Suite struct PolicyTests {
    @Test func studentPhasesRejectPalms() {
        for phase in [Phase.working, .hinting, .pose, .stepCheck] {
            #expect(CanvasPolicy.input(for: phase) == .pencilOnly)
            #expect(CanvasPolicy.ink(for: phase) == .student)
        }
    }

    @Test func armPhasesAcceptTheStylusAndUseTutorInk() {
        for phase in [Phase.stepWrite, .armStep] {
            #expect(CanvasPolicy.input(for: phase) == .anyInput)
            #expect(CanvasPolicy.ink(for: phase) == .tutor)
        }
        #expect(CanvasPolicy.input(for: .spotMistake) == .anyInput)
    }
}
