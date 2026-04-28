import Foundation

/// API client for the Annotty HIL Server.
/// Conforms to **protocol v1.0** as defined in `docs/protocol.md`.
///
/// Single source of truth for the wire format is the protocol document — this
/// client must follow it. If the server's behavior diverges, fix the server
/// (or the spec), not this client.
actor HILServerClient {
    static let protocolMajor = 1

    private let session: URLSession
    private let decoder: JSONDecoder
    private let encoder: JSONEncoder

    var baseURL: String
    var apiKey: String

    /// Update the base URL and API key (called when settings change)
    func updateSettings(baseURL: String, apiKey: String) {
        self.baseURL = baseURL
        self.apiKey = apiKey
    }

    init(baseURL: String = "", apiKey: String = "") {
        self.baseURL = baseURL
        self.apiKey = apiKey
        self.session = URLSession(configuration: .default)
        self.decoder = JSONDecoder()
        self.decoder.keyDecodingStrategy = .convertFromSnakeCase
        self.encoder = JSONEncoder()
        self.encoder.keyEncodingStrategy = .convertToSnakeCase
    }

    // MARK: - Response Models (protocol v1)

    struct Counts: Codable {
        let unannotated: Int
        let completed: Int
        let total: Int
    }

    struct ModelInfo: Codable {
        let bestExists: Bool
        let coremlExists: Bool
        let version: String
        let updatedAt: Double
        let md5: String?
    }

    struct ServerInfo: Codable {
        let name: String
        let protocolVersion: String
        let numClasses: Int
        let classNames: [String]
        let inputSize: Int
        let counts: Counts
        let model: ModelInfo

        // UI compatibility aliases
        var totalImages: Int { counts.total }
        var labeledImages: Int { counts.completed }
        var unlabeledImages: Int { counts.unannotated }
        var modelLoaded: Bool { model.bestExists }
    }

    /// Lightweight UI-facing image entry. Built client-side by joining
    /// `pool=unannotated` and `pool=completed` listings.
    struct ImageInfo: Codable, Identifiable, Hashable {
        let id: String
        let hasLabel: Bool
    }

    struct ImageListResponse {
        let images: [ImageInfo]
    }

    /// Raw envelope returned by `GET /images?pool=...`
    struct ImagesEnvelope: Codable {
        let pool: String
        let count: Int
        let items: [String]
    }

    struct ImageMeta: Codable {
        let imageId: String
        let pool: String
        let hasSeed: Bool
        let hasAnnotation: Bool
        let bytes: Int
        let width: Int
        let height: Int
    }

    struct SubmitResponse: Codable {
        let status: String
        let imageId: String?
        let pool: String?
    }

    /// Training status. Per spec, only `state` is required; everything else is
    /// optional and may be missing or null.
    struct TrainingStatus: Codable {
        let state: String
        let epoch: Int?
        let maxEpochs: Int?
        let bestMetric: Double?
        let metricName: String?
        let currentFold: Int?
        let nFolds: Int?
        let startedAt: String?
        let completedAt: String?
        let version: String?
        let error: String?

        // UI compatibility aliases
        var status: String { state }
        var bestDice: Double? { bestMetric }
    }

    /// `/next` envelope. When the pool is empty, all fields except `imageId`
    /// (=nil) are absent.
    struct NextSampleResponse: Codable {
        let imageId: String?
        let pool: String?
        let hasSeed: Bool?
        let hasAnnotation: Bool?
        let bytes: Int?
        let width: Int?
        let height: Int?
    }

    struct TrainStartResponse: Codable {
        let status: String
        let maxEpochs: Int?
        let trainingPairs: Int?
        let message: String?
    }

    /// Result of `GET /models/latest`. Includes ZIP body plus headers used for
    /// differential sync (per spec §7.13).
    struct ModelDownload {
        let zipData: Data
        let version: String?
        let md5: String?
        let updatedAt: Double?
    }

    // MARK: - API Methods

    /// Get server status info. Throws `HILError.protocolMismatch` when the
    /// server's `protocol_version` major differs from this client's.
    func getInfo() async throws -> ServerInfo {
        let data = try await get(path: "/info")
        let info = try decode(ServerInfo.self, from: data, path: "/info")
        try assertProtocolCompatible(info.protocolVersion)
        return info
    }

    /// Push the iPad-side palette / class names / class count to the server.
    /// Per spec §7.2 the client owns the palette; the server simply records it.
    func postConfig(palette: [[Int]], classNames: [String], numClasses: Int) async throws {
        struct ConfigPayload: Codable {
            let palette: [[Int]]
            let classNames: [String]
            let numClasses: Int
        }
        let body = try encoder.encode(ConfigPayload(
            palette: palette, classNames: classNames, numClasses: numClasses))
        _ = try await post(path: "/config", body: body)
    }

    /// Raw images listing for a single pool (`unannotated` or `completed`).
    func listImagesEnvelope(pool: String = "unannotated") async throws -> ImagesEnvelope {
        let data = try await get(path: "/images?pool=\(pool)")
        return try decode(ImagesEnvelope.self, from: data, path: "/images")
    }

    /// Composite listing for the dashboard: every server image with a
    /// `hasLabel` flag derived from whether it lives in the `completed` pool.
    func listImages() async throws -> ImageListResponse {
        async let unannotated = listImagesEnvelope(pool: "unannotated")
        async let completed = listImagesEnvelope(pool: "completed")
        let (u, c) = try await (unannotated, completed)
        let completedSet = Set(c.items)
        let merged = (u.items + c.items).map {
            ImageInfo(id: $0, hasLabel: completedSet.contains($0))
        }
        return ImageListResponse(images: merged)
    }

    /// Per-image metadata (pool, seed/annotation flags, dimensions).
    func imageMeta(imageId: String) async throws -> ImageMeta {
        let data = try await get(path: "/images/\(imageId)/meta")
        return try decode(ImageMeta.self, from: data, path: "/images/{id}/meta")
    }

    /// Download an image by ID (JPEG or PNG bytes).
    func downloadImage(imageId: String) async throws -> Data {
        return try await get(path: "/images/\(imageId)/download")
    }

    /// Download the mask for an image as RGB PNG bytes (palette-colored per spec §5.1).
    func downloadLabel(imageId: String) async throws -> Data {
        return try await get(path: "/labels/\(imageId)/download")
    }

    /// Run server-side inference, returns RGB PNG mask bytes.
    func infer(imageId: String) async throws -> Data {
        return try await post(path: "/infer/\(imageId)", body: nil)
    }

    /// Submit a labeled mask (RGB PNG, palette-colored) for an image.
    func submitLabel(imageId: String, maskPNG: Data) async throws -> SubmitResponse {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()
        body.append("--\(boundary)\r\n")
        body.append("Content-Disposition: form-data; name=\"file\"; filename=\"mask.png\"\r\n")
        body.append("Content-Type: image/png\r\n\r\n")
        body.append(maskPNG)
        body.append("\r\n--\(boundary)--\r\n")

        let url = try makeURL(path: "/submit/\(imageId)")
        var request = URLRequest(url: url)
        request.httpMethod = "PUT"
        request.setValue("multipart/form-data; boundary=\(boundary)", forHTTPHeaderField: "Content-Type")
        applyAuth(&request)
        request.httpBody = body

        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return try decode(SubmitResponse.self, from: data, path: "/submit/{id}")
    }

    /// Start model training.
    func startTraining(maxEpochs: Int? = nil) async throws -> TrainStartResponse {
        let suffix = maxEpochs.map { "?max_epochs=\($0)" } ?? ""
        let data = try await post(path: "/train\(suffix)", body: nil)
        return try decode(TrainStartResponse.self, from: data, path: "/train")
    }

    /// Cancel ongoing training.
    func cancelTraining() async throws -> TrainStartResponse {
        let data = try await post(path: "/train/cancel", body: nil)
        return try decode(TrainStartResponse.self, from: data, path: "/train/cancel")
    }

    /// Get current training status. `state` is required; other fields optional.
    func getTrainingStatus() async throws -> TrainingStatus {
        let data = try await get(path: "/status")
        return try decode(TrainingStatus.self, from: data, path: "/status")
    }

    /// Get next recommended sample (active learning). Returns nil when the pool is empty.
    func getNextSample() async throws -> NextSampleResponse {
        let data = try await get(path: "/next")
        return try decode(NextSampleResponse.self, from: data, path: "/next")
    }

    /// Download the latest CoreML model ZIP (~50MB, 300s timeout).
    /// Returns the body plus `X-Model-*` headers for differential sync.
    func downloadLatestModel() async throws -> ModelDownload {
        let url = try makeURL(path: "/models/latest")
        var request = URLRequest(url: url)
        request.timeoutInterval = 300
        applyAuth(&request)
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        let http = response as? HTTPURLResponse
        return ModelDownload(
            zipData: data,
            version: http?.value(forHTTPHeaderField: "X-Model-Version"),
            md5: http?.value(forHTTPHeaderField: "X-Model-Md5"),
            updatedAt: http?.value(forHTTPHeaderField: "X-Model-Updated-At").flatMap(Double.init)
        )
    }

    // MARK: - Private Helpers

    private func makeURL(path: String) throws -> URL {
        guard !baseURL.isEmpty, let url = URL(string: baseURL + path) else {
            throw HILError.invalidURL
        }
        return url
    }

    private func get(path: String) async throws -> Data {
        let url = try makeURL(path: path)
        var request = URLRequest(url: url)
        request.timeoutInterval = 30
        applyAuth(&request)
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return data
    }

    private func post(path: String, body: Data?) async throws -> Data {
        let url = try makeURL(path: path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.timeoutInterval = 120
        applyAuth(&request)
        if let body = body {
            request.httpBody = body
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        let (data, response) = try await session.data(for: request)
        try validateResponse(response, data: data)
        return data
    }

    private func applyAuth(_ request: inout URLRequest) {
        if !apiKey.isEmpty {
            request.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        }
    }

    private func validateResponse(_ response: URLResponse, data: Data? = nil) throws {
        guard let http = response as? HTTPURLResponse else {
            throw HILError.invalidResponse
        }
        guard (200...299).contains(http.statusCode) else {
            // Per spec §4: error body is always `{"detail": "..."}`.
            // Tolerate legacy `error` / `message` fields as well.
            var serverMessage: String?
            if let data = data,
               let json = try? JSONSerialization.jsonObject(with: data) as? [String: Any] {
                serverMessage = (json["detail"] as? String)
                    ?? (json["error"] as? String)
                    ?? (json["message"] as? String)
            }
            throw HILError.serverError(statusCode: http.statusCode, message: serverMessage)
        }
    }

    private func decode<T: Decodable>(_ type: T.Type, from data: Data, path: String) throws -> T {
        do {
            return try decoder.decode(type, from: data)
        } catch {
            throw HILError.decodingError(path: path, underlying: error)
        }
    }

    private func assertProtocolCompatible(_ version: String) throws {
        let parts = version.split(separator: ".")
        let serverMajor = parts.first.flatMap { Int($0) } ?? -1
        if serverMajor != Self.protocolMajor {
            throw HILError.protocolMismatch(
                serverVersion: version, clientMajor: Self.protocolMajor)
        }
    }
}

// MARK: - Error Types

enum HILError: LocalizedError {
    case invalidURL
    case invalidResponse
    case serverError(statusCode: Int, message: String? = nil)
    case maskConversionFailed
    case decodingError(path: String, underlying: Error)
    case protocolMismatch(serverVersion: String, clientMajor: Int)

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid server URL"
        case .invalidResponse:
            return "Invalid response from server"
        case .serverError(let code, let message):
            if code == 409 {
                if let msg = message {
                    if msg.contains("GPU") { return "GPU使用中" }
                    if msg.contains("already running") { return "トレーニング中" }
                    return msg
                }
                return "サーバービジー"
            }
            if let message = message { return message }
            return "Server error (HTTP \(code))"
        case .maskConversionFailed:
            return "Failed to convert mask data"
        case .decodingError(let path, let underlying):
            return "応答のデコードに失敗 (\(path)): \(underlying.localizedDescription)"
        case .protocolMismatch(let serverVersion, let clientMajor):
            return "プロトコル不一致 (server=\(serverVersion), client major=\(clientMajor)). サーバー更新が必要です"
        }
    }
}

// MARK: - Data Helper

private extension Data {
    mutating func append(_ string: String) {
        if let data = string.data(using: .utf8) {
            append(data)
        }
    }
}
