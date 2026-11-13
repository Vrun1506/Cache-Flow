import CacheFlowKit
import Foundation
import Observation
import UIKit

/// Glue between the server (HTTP + WebSocket), the audio devices, the canvases
/// and `SessionModel`. Views call its methods; it never touches layout.
@MainActor @Observable
final class TutorClient {
    let session = SessionModel()
    let canvas = BoardCanvasController()
    let questionCanvas = BoardCanvasController()
    private let audio = TutorAudio()

    private(set) var profiles: [ServerProfile]
    private(set) var selectedID: String
    var forceGhostInk = false
    var ttsBackend = "magpie"

    private(set) var layout = BoardLayout(screenSize: CGSize(width: 1210, height: 834))
    @ObservationIgnored private var api: (any TutorAPI)?
    @ObservationIgnored private var stream: (any EventStream)?
    @ObservationIgnored private var sessionID: String?
    @ObservationIgnored private var connectTask: Task<Void, Never>?
    @ObservationIgnored private var streamTask: Task<Void, Never>?
    @ObservationIgnored private var audioQueue: [(clip: Clip, notify: Bool)] = []
    @ObservationIgnored private var audioWorker: Task<Void, Never>?
    @ObservationIgnored private var touchesDuringArm = 0
    @ObservationIgnored private var jobStrokes: [String: ([[CGPoint]], Double)] = [:]
    @ObservationIgnored private var started = false
    @ObservationIgnored private var lessonEpoch = 0

    var profile: ServerProfile { profiles.first { $0.id == selectedID } ?? .mock }

    init() {
        let defaults = UserDefaults.standard
        if let data = defaults.data(forKey: "profiles"), let saved = try? JSONDecoder().decode([ServerProfile].self, from: data),
           !saved.isEmpty {
            profiles = saved
        } else {
            profiles = ServerProfile.defaults
        }
        #if DEBUG
        let fallback = ServerProfile.mock.id
        #else
        let fallback = ServerProfile.magicDNS.id
        #endif
        selectedID = defaults.string(forKey: "selectedProfile") ?? fallback

        canvas.onTouch = { [weak self] point, type in self?.boardTouched(point, type) }
        canvas.onStrokeAdded = { [weak self] bounds in self?.strokeAdded(bounds) }
    }

    // MARK: Lifecycle

    func start(layout: BoardLayout) {
        self.layout = layout
        guard !started else { return }
        started = true
        connect()
        #if DEBUG
        runDemoIfRequested()
        #endif
    }

    #if DEBUG
    /// `-demo teach|guide|check` launch argument: pick the first preset and start that mode,
    /// so the mock lesson can be exercised without tapping (screenshots, smoke tests).
    private func runDemoIfRequested() {
        let args = ProcessInfo.processInfo.arguments
        guard let i = args.firstIndex(of: "-demo"), i + 1 < args.count, let mode = Mode(rawValue: args[i + 1]) else { return }
        Task {
            try? await Task.sleep(for: .seconds(1))
            pickPreset(Preset.all[0])
            try? await Task.sleep(for: .seconds(1.5))
            choose(mode)
        }
    }
    #endif

    func select(profileID: String) {
        selectedID = profileID
        UserDefaults.standard.set(profileID, forKey: "selectedProfile")
        connect()
    }

    func updateURL(_ url: String, for id: String) {
        guard let i = profiles.firstIndex(where: { $0.id == id }) else { return }
        profiles[i].baseURL = url
        if let data = try? JSONEncoder().encode(profiles) { UserDefaults.standard.set(data, forKey: "profiles") }
    }

    /// (Re)build the backend for the selected profile, create a session, and
    /// open the event stream. Retries while the server is unreachable.
    func connect(problemID: String? = nil) {
        lessonEpoch += 1
        connectTask?.cancel()
        streamTask?.cancel()
        audioWorker?.cancel()
        audioWorker = nil
        audioQueue = []
        audio.stop()
        let p = profile
        session.connection = .connecting
        let previous = stream
        connectTask = Task {
            await previous?.close()
            let api: any TutorAPI
            let stream: any EventStream
            if p.isMock {
                let mock = MockServer(layout: layout)
                api = mock; stream = mock
            } else {
                api = HTTPTutorAPI(profile: p); stream = WebSocketEventStream(profile: p)
            }
            self.api = api; self.stream = stream
            while !Task.isCancelled {
                do {
                    let id = try await api.createSession(problemID: problemID)
                    sessionID = id
                    open(stream: stream, sessionID: id)
                    return
                } catch {
                    session.connection = .offline
                    session.caption = "I can't reach the tutor server (\(p.name)). Retrying…"
                    try? await Task.sleep(for: .seconds(3))
                }
            }
        }
    }

    private func open(stream: any EventStream, sessionID: String) {
        streamTask?.cancel()
        streamTask = Task {
            for await item in await stream.events(sessionID: sessionID) {
                switch item {
                case .state(let s): session.connection = s
                case .event(let e): handle(e)
                }
            }
        }
    }

    // MARK: Server events

    private func handle(_ event: ServerEvent) {
        session.apply(event) { [layout] slot in layout.slots.indices.contains(slot - 1) ? layout.slots[slot - 1] : nil }
        switch event {
        case .step(_, _, _, _, let clips, let arm):
            touchesDuringArm = 0
            if let arm { jobStrokes[arm.job] = (arm.strokes, arm.estimatedSeconds) }
            for clip in clips { enqueue(clip, notify: true) }
            if forceGhostInk, let arm { playback(job: arm.job) }
        case .ask(let clip, _):
            enqueue(clip, notify: true)
        case .tutorSay(let text, let url):
            if let url, !url.isEmpty { enqueue(Clip(id: "say", url: url, caption: text), notify: false) }
        case .arm(let job, let state):
            guard state != .drawing else { break }
            let touches = touchesDuringArm
            if state == .done { Task { await stream?.send(.armTouches(job: job, count: touches)) } }
            // Done with no touches means the stylus never registered: draw it ourselves.
            if state == .error || touches == 0 { playback(job: job) }
        case .highlight:
            let id = session.highlight?.id
            Task {
                try? await Task.sleep(for: .seconds(4))
                if session.highlight?.id == id { session.highlight = nil }
            }
        case .phase(_, let phase):
            if phase == .idle { jobStrokes = [:]; lessonEpoch += 1 }
        default:
            break
        }
    }

    private func playback(job: String) {
        guard let (strokes, seconds) = jobStrokes[job],
              !session.ghosts.contains(where: { $0.job == job && $0.kind == .playback }) else { return }
        let duration = max(seconds, 1)
        session.setGhost(Ghost(job: job, strokes: strokes, kind: .playback, duration: duration))
        let epoch = lessonEpoch
        Task {
            try? await Task.sleep(for: .seconds(duration))
            guard epoch == lessonEpoch else { return } // the lesson was reset meanwhile
            canvas.addTutorStrokes(strokes)
            session.ghosts.removeAll { $0.job == job }
        }
    }

    // MARK: Audio

    private func enqueue(_ clip: Clip, notify: Bool) {
        audioQueue.append((clip, notify))
        guard audioWorker == nil else { return }
        audioWorker = Task { await drainAudio() }
    }

    private func drainAudio() async {
        session.speaking = true
        while !audioQueue.isEmpty, !Task.isCancelled {
            let (clip, notify) = audioQueue.removeFirst()
            if !clip.caption.isEmpty { session.caption = clip.caption }
            await audio.play(clip, profile: profile)
            if notify { await stream?.send(.audioDone(clip: clip.id)) }
        }
        session.speaking = false
        audioWorker = nil
    }

    // MARK: Student actions

    func pickPreset(_ preset: Preset) {
        questionCanvas.clearAll()
        canvas.clearAll()
        session.resetLesson()
        session.problem = preset.display
        connect(problemID: preset.id)
    }

    func readQuestion() {
        guard let png = questionCanvas.flattenedPNG(), let api, let sessionID else { return }
        session.caption = "Let me read that…"
        perform("I couldn't send that. Check the connection and try again.") {
            try await api.submitQuestion(sessionID: sessionID, png: png)
        }
    }

    func confirmProblem(_ ok: Bool) {
        session.needsConfirm = false
        if !ok { questionCanvas.clearAll() }
        Task { await stream?.send(.confirmProblem(ok: ok)) }
    }

    func choose(_ mode: Mode) { Task { await stream?.send(.chooseMode(mode)) } }
    func stuck() { Task { await stream?.send(.stuck) } }

    func submitAnswer(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        session.ask = nil
        Task { await stream?.send(.answerText(t)) }
    }

    /// Segment the student's work into lines on-device, upload them, then tell
    /// the server to check.
    func check() {
        guard let api, let sessionID else { return }
        let lines = LineSegmenter(region: layout.workArea).segment(canvas.inkStrokes())
        session.lineRects = Dictionary(uniqueKeysWithValues: lines.map { ($0.index, $0.bounds) })
        session.readBack = []
        let uploads = canvas.lineUploads(for: lines)
        session.caption = lines.isEmpty ? "I don't see any working yet." : "Looking at your working…"
        perform("I couldn't send your working. Check the connection and try again.") {
            if !uploads.isEmpty { try await api.submitLines(sessionID: sessionID, lines: uploads) }
            await self.stream?.send(.check)
        }
    }

    func clearBoard() {
        canvas.clearTutorInk()
        session.readBack = []
        session.highlight = nil
        Task { await stream?.send(.clearBoard) }
    }

    func newProblem() {
        questionCanvas.clearAll()
        canvas.clearAll()
        session.resetLesson()
        session.problem = nil
        session.caption = "Write a problem in the box, or pick one."
        connect()
    }

    // MARK: Voice

    func talkBegan() {
        guard !session.speaking, !session.recording else { return }
        session.recording = true
        Task {
            if await audio.startRecording() == false { session.recording = false }
        }
    }

    func talkEnded() {
        guard session.recording else { return }
        session.recording = false
        guard let wav = audio.stopRecording(), let api, let sessionID else { return }
        session.caption = "Listening…"
        perform("I couldn't hear that. Try again.") {
            let transcript = try await api.submitAudio(sessionID: sessionID, wav: wav)
            self.session.caption = "You said: “\(transcript)”"
        }
    }

    // MARK: Canvas input

    private func boardTouched(_ point: CGPoint, _ type: UITouch.TouchType) {
        if session.arm?.state == .drawing { touchesDuringArm += 1 }
        if session.calibration != nil {
            session.calibration?.record(point, input: type == .pencil ? "pencil" : type == .direct ? "direct" : "pointer")
        }
    }

    /// A circle drawn in spot-the-mistake: report the tutor line it covers most.
    private func strokeAdded(_ bounds: CGRect) {
        guard session.phase == .spotMistake else { return }
        let targets = layout.slots.enumerated().map { (index: $0.offset, rect: $0.element) }
        if let line = StrokeGeometry.bestTarget(for: bounds, among: targets) {
            Task { await stream?.send(.mistakeCircled(lineIndex: line)) }
        }
    }

    // MARK: Settings and operator

    func refreshHealth() async {
        session.health = try? await api?.health()
    }

    func admin(_ action: String, value: String? = nil) {
        perform("That operator action failed.") { try await self.api?.admin(action, value: value) }
    }

    func setTTS(_ backend: String) {
        ttsBackend = backend
        admin("tts_backend", value: backend)
    }

    func toggleCalibration() {
        session.calibration = session.calibration == nil ? CalibrationLog(layout: layout) : nil
    }

    private func perform(_ failure: String, _ work: @escaping @MainActor () async throws -> Void) {
        Task {
            do { try await work() } catch { session.caption = failure }
        }
    }
}
