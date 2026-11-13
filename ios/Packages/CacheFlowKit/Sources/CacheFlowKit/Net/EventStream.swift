import Foundation

public enum ConnectionState: Sendable, Equatable {
    case connecting, connected, reconnecting, offline
}

public enum StreamItem: Sendable, Equatable {
    case state(ConnectionState)
    case event(ServerEvent)
}

/// The WebSocket half of the contract. The real implementation reconnects by
/// itself; the mock implements the same protocol with a scripted lesson.
public protocol EventStream: Sendable {
    func events(sessionID: String) async -> AsyncStream<StreamItem>
    func send(_ event: ClientEvent) async
    func close() async
}

/// `URLSessionWebSocketTask` with automatic reconnect (capped exponential
/// back-off) and a ping every 10 s.
public actor WebSocketEventStream: EventStream {
    private let profile: ServerProfile
    private let urlSession: URLSession
    private let pingInterval: Duration
    private var socket: URLSessionWebSocketTask?
    private var runner: Task<Void, Never>?

    public init(profile: ServerProfile, urlSession: URLSession = .shared, pingInterval: Duration = .seconds(10)) {
        self.profile = profile
        self.urlSession = urlSession
        self.pingInterval = pingInterval
    }

    public func events(sessionID: String) -> AsyncStream<StreamItem> {
        runner?.cancel()
        let (stream, continuation) = AsyncStream<StreamItem>.makeStream()
        runner = Task { [weak self] in
            await self?.run(sessionID: sessionID, continuation: continuation)
            continuation.finish()
        }
        continuation.onTermination = { [weak self] _ in Task { await self?.close() } }
        return stream
    }

    public func send(_ event: ClientEvent) async {
        guard let socket, let data = try? JSONEncoder().encode(event) else { return }
        try? await socket.send(.string(String(decoding: data, as: UTF8.self)))
    }

    public func close() {
        runner?.cancel()
        runner = nil
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil
    }

    private func run(sessionID: String, continuation: AsyncStream<StreamItem>.Continuation) async {
        guard let url = profile.webSocketURL(sessionID: sessionID) else {
            continuation.yield(.state(.offline))
            return
        }
        var attempt = 0
        while !Task.isCancelled {
            continuation.yield(.state(attempt == 0 ? .connecting : .reconnecting))
            let ws = urlSession.webSocketTask(with: url)
            socket = ws
            ws.resume()
            let interval = pingInterval
            let pinger = Task {
                while !Task.isCancelled {
                    try? await Task.sleep(for: interval)
                    ws.sendPing { _ in }
                }
            }
            do {
                var announced = false
                while !Task.isCancelled {
                    let message = try await ws.receive()
                    if !announced { announced = true; attempt = 0; continuation.yield(.state(.connected)) }
                    let data: Data
                    switch message {
                    case .string(let s): data = Data(s.utf8)
                    case .data(let d): data = d
                    @unknown default: continue
                    }
                    if let event = try? JSONDecoder().decode(ServerEvent.self, from: data) {
                        continuation.yield(.event(event))
                    }
                }
            } catch {
                // fall through to reconnect
            }
            pinger.cancel()
            ws.cancel(with: .goingAway, reason: nil)
            if Task.isCancelled { break }
            attempt += 1
            continuation.yield(.state(.reconnecting))
            try? await Task.sleep(for: .seconds(min(pow(2.0, Double(attempt)), 8)))
        }
        continuation.yield(.state(.offline))
    }
}
