import AVFoundation
import CacheFlowKit

/// Plays tutor clips (half-duplex: the caller keeps the mic off meanwhile) and
/// records push-to-talk audio as 16 kHz mono WAV. A clip that cannot be loaded
/// falls back to on-device speech of its caption, so the tutor never goes
/// silent and the caption is always on screen.
@MainActor
final class TutorAudio: AudioOutput, VoiceRecorder {
    private var player: AVPlayer?
    private let synth = AVSpeechSynthesizer()
    private var recorder: AVAudioRecorder?
    private let recordingURL = FileManager.default.temporaryDirectory.appendingPathComponent("ptt.wav")
    private var isRecording = false

    func play(_ request: SpeechRequest, profile: ServerProfile) async -> AudioStatus {
        switch AudioURLKind.classify(request.url) {
        case .mockClip:
            // The mock has no audio files; on-device speech stands in for Magpie.
            await speak(request.caption)
            return .played
        case .mockLost:
            await speak(request.caption)
            return .fallback
        case .remote:
            if let url = profile.resolve(request.url), await playFile(url) { return .played }
            await speak(request.caption)
            return .fallback
        }
    }

    func stop() {
        player?.pause()
        player = nil
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
        while synth.isSpeaking, !Task.isCancelled {
            try? await Task.sleep(for: .milliseconds(60))
        }
    }

    // MARK: Push-to-talk

    func startRecording() async -> Bool {
        guard !Task.isCancelled, await AVAudioApplication.requestRecordPermission(), !Task.isCancelled else { return false }
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

    /// Returns the utterance and deletes the file: microphone audio is never
    /// kept beyond the active request.
    func stopRecording() -> Data? {
        guard isRecording else { return nil }
        recorder?.stop()
        recorder = nil
        isRecording = false
        defer { try? FileManager.default.removeItem(at: recordingURL) }
        return try? Data(contentsOf: recordingURL)
    }

    private func activateSession() {
        let s = AVAudioSession.sharedInstance()
        try? s.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothHFP])
        try? s.setActive(true)
    }
}
