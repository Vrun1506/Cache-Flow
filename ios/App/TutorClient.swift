import CacheFlowKit
import Foundation
import Observation

/// Glue between the session API (HTTP + WSS), the audio devices, the student
/// canvases and `SessionModel`. All lesson rules live in `LessonEngine`; this
/// class performs its effects and turns taps into revisioned, idempotent
/// requests. Views call its methods; it never touches layout.
@MainActor @Observable
final class TutorClient {
    let model = SessionModel()
    let canvas = BoardCanvasController()
    let questionCanvas = BoardCanvasController()
    let config: LaunchConfig

    private(set) var profiles: [ServerProfile]
    private(set) var selectedID: String
    private(set) var layout = BoardLayout(screenSize: CGSize(width: 1210, height: 834))

    @ObservationIgnored private let audio: any AudioOutput
    @ObservationIgnored private let recorder: any VoiceRecorder
    @ObservationIgnored private var engine = LessonEngine(sessionID: "", boardRevision: 0)
    @ObservationIgnored private var api: (any TutorAPI)?
    @ObservationIgnored private var stream: (any EventStream)?
    @ObservationIgnored private(set) var mock: MockServer?
    @ObservationIgnored private var sessionID: String?
    @ObservationIgnored private var connectTask: Task<Void, Never>?
    @ObservationIgnored private var streamTask: Task<Void, Never>?
    @ObservationIgnored private var speech = SpeechQueue()
    @ObservationIgnored private var speechTask: Task<Void, Never>?
    @ObservationIgnored private var ids = RequestIDGenerator(prefix: "ipad-\(UUID().uuidString.prefix(8))")
    @ObservationIgnored private var uploadedRevision: BoardRevision?
    @ObservationIgnored private var started = false
    @ObservationIgnored private var wasConnected = false

    var profile: ServerProfile { profiles.first { $0.id == selectedID } ?? .mock }

    init(config: LaunchConfig = .current) {
        self.config = config
        if config.uiTest {
            audio = ScriptedAudio()
            recorder = ScriptedRecorder()
        } else {
            let a = TutorAudio()
            audio = a
            recorder = a
        }
        let defaults = UserDefaults.standard
        if let data = defaults.data(forKey: "profiles.v2"), let saved = try? JSONDecoder().decode([ServerProfile].self, from: data),
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
        selectedID = config.uiTest ? ServerProfile.mock.id : (defaults.string(forKey: "selectedProfile.v2") ?? fallback)

        for c in [canvas, questionCanvas] {
            c.onDrawingChanged = { [weak self] in self?.studentInkChanged() }
            c.onPenActive = { [weak self] down in self?.penActive(down) }
        }
    }

    // MARK: Lifecycle

    func start(layout: BoardLayout) {
        self.layout = layout
        engine.boardBounds = layout.boardBounds
        guard !started else { return }
        started = true
        connect()
    }

    func select(profileID: String) {
        selectedID = profileID
        UserDefaults.standard.set(profileID, forKey: "selectedProfile.v2")
        connect()
    }

    func updateURL(_ url: String, for id: String) {
        guard let i = profiles.firstIndex(where: { $0.id == id }) else { return }
        profiles[i].baseURL = url
        if let data = try? JSONEncoder().encode(profiles) { UserDefaults.standard.set(data, forKey: "profiles.v2") }
    }

    /// (Re)build the backend for the selected profile, create a session, and
    /// open the event stream. Retries while the server is unreachable.
    func connect(presetID: String? = nil) {
        connectTask?.cancel()
        streamTask?.cancel()
        stopSpeech(turnID: nil)
        uploadedRevision = nil
        let p = profile
        model.connection = .connecting
        let previous = stream
        connectTask = Task {
            await previous?.close()
            let api: any TutorAPI
            let stream: any EventStream
            if p.isMock {
                let m = mock ?? MockServer(layout: layout, timing: config.mockTiming)
                mock = m
                api = m; stream = m
            } else {
                mock = nil
                api = HTTPTutorAPI(profile: p); stream = WebSocketEventStream(profile: p)
            }
            self.api = api; self.stream = stream
            while !Task.isCancelled {
                do {
                    let info = try await api.createSession(presetID: presetID, locale: Locale.current.identifier)
                    sessionID = info.sessionID
                    engine = LessonEngine(sessionID: info.sessionID, boardRevision: info.boardRevision,
                                          caption: presetID == nil ? "Write a problem in the box, type it, or pick one."
                                                                   : "Tap Tutor me when you're ready.",
                                          boardBounds: layout.boardBounds)
                    wasConnected = false
                    sync()
                    open(stream: stream, sessionID: info.sessionID)
                    return
                } catch TutorAPIError.upgradeRequired {
                    model.connection = .offline
                    engine.setCaption("This app is out of date for the tutor server. Please update Socrates.")
                    sync()
                    return
                } catch {
                    model.connection = .offline
                    engine.setCaption("I can't reach the tutor server (\(p.name)). Retrying…")
                    sync()
                    try? await Task.sleep(for: .seconds(3))
                }
            }
        }
    }

    private func open(stream: any EventStream, sessionID: String) {
        streamTask?.cancel()
        streamTask = Task {
            for await item in await stream.events(sessionID: sessionID, afterSeq: nil) {
                switch item {
                case .state(let s):
                    if s == .connected { if wasConnected { model.reconnects += 1 }; wasConnected = true }
                    model.connection = s
                case .envelope(let env):
                    perform(engine.receive(env))
                    sync()
                }
            }
        }
    }

    private func sync() { model.lesson = engine.state }

    // MARK: Effects

    private func perform(_ effects: [LessonEffect]) {
        for effect in effects {
            switch effect {
            case .send(let message): send(message)
            case .speak(let request):
                speech.enqueue(request)
                pumpSpeech()
            case .stopSpeech(let turn): stopSpeech(turnID: turn)
            }
        }
    }

    private func send(_ message: ClientMessage) {
        guard let sessionID, let stream else { return }
        let env = ClientEnvelope(sessionID: sessionID, boardRevision: engine.state.boardRevision, message: message)
        Task { await stream.send(env) }
    }

    // MARK: Speech

    private func pumpSpeech() {
        guard speechTask == nil, let request = speech.next() else { return }
        model.speaking = true
        let p = profile
        speechTask = Task {
            let status = await audio.play(request, profile: p)
            guard !Task.isCancelled, speech.current == request else { return }
            speech.finished()
            model.audioStatus = status
            if status == .played { model.clipsPlayed += 1 } else { model.fallbacks += 1 }
            send(.audioDone(turnID: request.turnID, chunkIndex: request.chunkIndex))
            speechTask = nil
            model.speaking = false
            pumpSpeech()
        }
    }

    private func stopSpeech(turnID: String?) {
        if speech.cancel(turnID: turnID) {
            speechTask?.cancel()
            speechTask = nil
            audio.stop()
            model.speaking = false
        }
        pumpSpeech()
    }

    private func penActive(_ down: Bool) {
        speech.setPen(down: down)
        if !down { pumpSpeech() }
    }

    // MARK: Student board

    private func studentInkChanged() {
        engine.studentInkChanged()
        sync()
    }

    private func boardSnapshot() -> BoardSnapshot? {
        let area = layout.workArea
        let lines = LineSegmenter(region: area).segment(canvas.inkStrokes())
            .prefix(BoardSnapshot.maxLines)
            .map { Region(lineIndex: $0.index, bbox: $0.bounds) }
        guard let png = canvas.snapshotPNG(of: area) else { return nil }
        return BoardSnapshot(boardRevision: engine.state.boardRevision, imageRegion: area, png: png, lines: Array(lines))
    }

    // MARK: Student actions

    func pickPreset(_ preset: Preset) {
        questionCanvas.clearAll()
        canvas.clearAll()
        connect(presetID: preset.id)
    }

    func readQuestion() {
        guard let png = questionCanvas.flattenedPNG() else { return }
        submitQuestion(.image(png: png))
        engine.setCaption("Let me read that…")
        sync()
    }

    func typeQuestion(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        submitQuestion(.text(t))
    }

    private func submitQuestion(_ input: QuestionInput) {
        guard let api, let sessionID else { return }
        let rid = ids.next()
        let rev = engine.state.boardRevision
        run("I couldn't send that. Check the connection and try again.") {
            _ = try await api.submitQuestion(sessionID: sessionID, input, boardRevision: rev, requestID: rid)
        }
    }

    /// Accept the read-back as shown (`text == nil`), pick/correct it, or reject it.
    func confirmReadBack(accepted: Bool, text: String? = nil) {
        let source = engine.state.pendingReadBack?.source
        perform(engine.confirmReadBack(accepted: accepted, text: text))
        if !accepted, source == .question { questionCanvas.clearAll() }
        sync()
    }

    /// Tutor me, Check, Hint, Explain this step, Draw it, Show the answer,
    /// Ask and quiz answers: one idempotent, revisioned command each.
    func request(_ action: IntentAction, text: String? = nil) {
        guard let api, let sessionID else { return }
        let rid = ids.next()
        let rev = engine.state.boardRevision
        let snapshot = action.needsBoardSnapshot && uploadedRevision != rev ? boardSnapshot() : nil
        let snapshotID = snapshot == nil ? nil : ids.next()
        run("I couldn't send that. Check the connection and try again.") {
            if let snapshot, let snapshotID {
                _ = try await api.submitBoard(sessionID: sessionID, snapshot, requestID: snapshotID)
                self.uploadedRevision = rev
            }
            _ = try await api.sendIntent(sessionID: sessionID, IntentRequest(action: action, boardRevision: rev, requestID: rid, text: text))
        }
    }

    /// Stop the active tutor turn now: its audio, ink and scenes are dropped
    /// locally before the server confirms the cancellation boundary.
    func cancelTurn() {
        perform(engine.cancelActiveTurn(requestID: ids.next()))
        sync()
    }

    func sendReply(_ text: String) {
        let t = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !t.isEmpty else { return }
        request(ActionPolicy.replyAction(engine.state), text: t)
    }

    func clearTutorLayer() {
        engine.clearTutorLayer()
        sync()
    }

    func newProblem() {
        questionCanvas.clearAll()
        canvas.clearAll()
        model.summary = nil
        connect()
    }

    func loadSummary() {
        guard let api, let sessionID else { return }
        run("I couldn't load the summary.") {
            self.model.summary = try await api.summary(sessionID: sessionID)
            self.model.showSummary = true
        }
    }

    /// Operator action: delete this session's stored evidence on the server.
    func deleteSession() {
        guard let api, let sessionID else { return }
        run("Deleting the session failed.") {
            try await api.deleteSession(sessionID: sessionID)
            self.newProblem()
        }
    }

    // MARK: Voice

    func talkBegan() {
        guard !model.speaking, !model.recording else { return }
        model.recording = true
        Task {
            if await recorder.startRecording() == false { model.recording = false }
        }
    }

    func talkEnded() {
        guard model.recording else { return }
        model.recording = false
        guard let wav = recorder.stopRecording(), let api, let sessionID else { return }
        let rid = ids.next()
        let rev = engine.state.boardRevision
        engine.setNotice("Listening…")
        sync()
        run("I couldn't hear that. Try again, or type your reply.") {
            let candidate = try await api.submitAudio(sessionID: sessionID, wav: wav, boardRevision: rev, requestID: rid)
            // An unambiguous transcript is used directly; an ambiguous one
            // arrives as a read_back the student must confirm.
            self.engine.setNotice(candidate.ambiguous ? nil : "You said: “\(candidate.text)”")
            self.sync()
        }
    }

    // MARK: Settings, health and debug

    func refreshHealth() async {
        model.health = try? await api?.health()
    }

    func inject(_ fault: MockServer.Fault) {
        guard let mock else { return }
        Task { await mock.inject(fault) }
    }

    func dropConnection() {
        guard let mock else { return }
        Task { await mock.dropConnection() }
    }

    private func run(_ failure: String, _ work: @escaping @MainActor () async throws -> Void) {
        Task {
            do {
                try await work()
            } catch TutorAPIError.staleRevision(let latest) {
                engine.adopt(serverRevision: latest)
                engine.setNotice("Your board changed while I was looking. Tap it again.")
                sync()
            } catch TutorAPIError.upgradeRequired {
                engine.setNotice("This app is out of date for the tutor server.")
                sync()
            } catch {
                engine.setNotice(failure)
                sync()
            }
        }
    }
}

extension TutorClient {
    /// The board-space box of a student line, from the last uploaded snapshot geometry.
    func lineRect(_ index: Int) -> CGRect? {
        let lines = LineSegmenter(region: layout.workArea).segment(canvas.inkStrokes())
        return lines.first { $0.index == index }?.bounds
    }
}
