import CacheFlowKit
import Foundation

/// Launch-time configuration. `-uitest` runs the in-process mock with
/// deterministic fake audio and microphone and shows the fault-injection
/// panel, so the full lesson is testable in the Simulator with no Spark.
struct LaunchConfig {
    var uiTest = false
    var mockTiming: MockServer.Timing = .demo

    static let current: LaunchConfig = {
        var c = LaunchConfig()
        let args = ProcessInfo.processInfo.arguments
        c.uiTest = args.contains("-uitest")
        return c
    }()
}

/// Plays one tutor clip and reports how it reached the student.
@MainActor protocol AudioOutput: AnyObject {
    func play(_ request: SpeechRequest, profile: ServerProfile) async -> AudioStatus
    func stop()
}

/// Push-to-talk capture of one 16 kHz mono WAV utterance. A cancelled start
/// must not open the microphone after permission has been resolved.
@MainActor protocol VoiceRecorder: AnyObject {
    func startRecording() async -> Bool
    func stopRecording() -> Data?
}

/// UI-test audio: silent, quick and deterministic. `mock://lost/…` fails.
@MainActor final class ScriptedAudio: AudioOutput {
    func play(_ request: SpeechRequest, profile: ServerProfile) async -> AudioStatus {
        try? await Task.sleep(for: .milliseconds(250))
        return AudioURLKind.classify(request.url) == .mockLost ? .fallback : .played
    }
    func stop() {}
}

/// UI-test microphone: returns a short silent WAV without a permission prompt.
@MainActor final class ScriptedRecorder: VoiceRecorder {
    private var recording = false
    func startRecording() async -> Bool {
        guard !Task.isCancelled else { return false }
        recording = true
        return true
    }
    func stopRecording() -> Data? {
        guard recording else { return nil }
        recording = false
        return WAV.silence(milliseconds: 300)
    }
}

enum WAV {
    /// A valid 16 kHz mono 16-bit PCM WAV of silence.
    static func silence(milliseconds: Int) -> Data {
        let samples = 16 * milliseconds
        let dataSize = UInt32(samples * 2)
        var d = Data("RIFF".utf8)
        func u32(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func u16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        u32(36 + dataSize); d.append(Data("WAVEfmt ".utf8))
        u32(16); u16(1); u16(1); u32(16_000); u32(32_000); u16(2); u16(16)
        d.append(Data("data".utf8)); u32(dataSize)
        d.append(Data(count: Int(dataSize)))
        return d
    }
}
