import AVFoundation
import CacheFlowKit

/// Plays tutor clips (half-duplex: the caller keeps the mic off meanwhile) and
/// records push-to-talk audio as 16 kHz mono WAV.
@MainActor
final class TutorAudio {
    private var player: AVPlayer?
    private let synth = AVSpeechSynthesizer()
    private var recorder: AVAudioRecorder?
    private let recordingURL = FileManager.default.temporaryDirectory.appendingPathComponent("ptt.wav")
    private(set) var isRecording = false

    /// Returns when the clip has finished. Falls back to on-device speech of
    /// the caption if the file can't be loaded, so the tutor never goes silent.
    func play(_ clip: Clip, profile: ServerProfile) async {
        if !clip.url.isEmpty, let url = profile.resolve(clip.url), url.scheme != "mock", await playFile(url) { return }
        await speak(clip.caption)
    }

    func stop() {
        player?.pause()
        synth.stopSpeaking(at: .immediate)
    }

    private func playFile(_ url: URL) async -> Bool {
        activateSession()
        let item = AVPlayerItem(url: url)
        let p = AVPlayer(playerItem: item)
        player = p
        p.play()
        let deadline = ContinuousClock.now + .seconds(90)
        while ContinuousClock.now < deadline {
            try? await Task.sleep(for: .milliseconds(60))
            if Task.isCancelled { p.pause(); return true }
            switch item.status {
            case .failed: return false
            case .readyToPlay:
                let d = item.duration.seconds
                if d.isFinite, d > 0, item.currentTime().seconds >= d - 0.05 { return true }
            default: break
            }
        }
        p.pause()
        return true
    }

    private func speak(_ text: String) async {
        guard !text.isEmpty else { return }
        activateSession()
        synth.stopSpeaking(at: .immediate)
        synth.speak(AVSpeechUtterance(string: text))
        let words = Double(text.split(separator: " ").count)
        try? await Task.sleep(for: .seconds(words * 0.4 + 0.5))
    }

    // MARK: Push-to-talk

    func startRecording() async -> Bool {
        guard await AVAudioApplication.requestRecordPermission() else { return false }
        activateSession()
        let settings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: 16_000, AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false,
        ]
        guard let r = try? AVAudioRecorder(url: recordingURL, settings: settings), r.record() else { return false }
        recorder = r
        isRecording = true
        return true
    }

    func stopRecording() -> Data? {
        guard isRecording else { return nil }
        recorder?.stop()
        recorder = nil
        isRecording = false
        return try? Data(contentsOf: recordingURL)
    }

    private func activateSession() {
        let s = AVAudioSession.sharedInstance()
        try? s.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try? s.setActive(true)
    }
}
