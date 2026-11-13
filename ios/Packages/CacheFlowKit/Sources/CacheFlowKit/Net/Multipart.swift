import Foundation

/// Minimal `multipart/form-data` builder (no third-party dependency).
public struct MultipartForm: Sendable {
    public let boundary: String
    private var body = Data()

    public init(boundary: String = "CacheFlow-\(UUID().uuidString)") { self.boundary = boundary }

    public var contentType: String { "multipart/form-data; boundary=\(boundary)" }

    public mutating func addField(name: String, value: String) {
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"\r\n\r\n\(value)\r\n")
    }

    public mutating func addFile(name: String, filename: String, mimeType: String, data: Data) {
        append("--\(boundary)\r\nContent-Disposition: form-data; name=\"\(name)\"; filename=\"\(filename)\"\r\nContent-Type: \(mimeType)\r\n\r\n")
        body.append(data)
        append("\r\n")
    }

    public func finalized() -> Data {
        var out = body
        out.append(Data("--\(boundary)--\r\n".utf8))
        return out
    }

    private mutating func append(_ s: String) { body.append(Data(s.utf8)) }
}
