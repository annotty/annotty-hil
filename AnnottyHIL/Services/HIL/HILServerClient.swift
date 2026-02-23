import Foundation

/// API client for the HIL FastAPI server
/// Thread-safe actor-based implementation using URLSession
actor HILServerClient {
    private let session: URLSession
    private let decoder: JSONDecoder

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
    }

    // MARK: - Response Models

    struct ServerInfo: Codable {
        let name: String
        let totalImages: Int
        let labeledImages: Int
        let unlabeledImages: Int
        let modelLoaded: Bool
        let trainingStatus: String
    }

    struct ImageInfo: Codable, Identifiable {
        let id: String
        let hasLabel: Bool
    }

    struct ImageListResponse: Codable {
        let images: [ImageInfo]
    }

    struct SubmitResponse: Codable {
        let status: String
        let imageId: String?
    }

    struct TrainingStatus: Codable {
        let status: String  // "idle", "running", "completed", "error"
        let epoch: Int?
        let maxEpochs: Int?
        let bestDice: Double?
        let startedAt: String?
        let completedAt: String?
    }

    struct NextSampleResponse: Codable {
        let imageId: String?
    }

    struct TrainStartResponse: Codable {
        let status: String
        let message: String?
    }

    // MARK: - API Methods

    /// Get server status info
    func getInfo() async throws -> ServerInfo {
        let data = try await get(path: "/info")
        return try decoder.decode(ServerInfo.self, from: data)
    }

    /// List all images on the server
    func listImages() async throws -> ImageListResponse {
        let data = try await get(path: "/images")
        return try decoder.decode(ImageListResponse.self, from: data)
    }

    /// Download an image by ID, returns JPEG/PNG data
    func downloadImage(imageId: String) async throws -> Data {
        return try await get(path: "/images/\(imageId)/download")
    }

    /// Submit a labeled mask for an image (multipart/form-data)
    func submitLabel(imageId: String, maskPNG: Data) async throws -> SubmitResponse {
        let boundary = "Boundary-\(UUID().uuidString)"
        var body = Data()

        // Build multipart body — server expects field name "file"
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
        try validateResponse(response)
        return try decoder.decode(SubmitResponse.self, from: data)
    }

    /// Start model training
    func startTraining() async throws -> TrainStartResponse {
        let data = try await post(path: "/train", body: nil)
        return try decoder.decode(TrainStartResponse.self, from: data)
    }

    /// Cancel ongoing training
    func cancelTraining() async throws -> TrainStartResponse {
        let data = try await post(path: "/train/cancel", body: nil)
        return try decoder.decode(TrainStartResponse.self, from: data)
    }

    /// Get current training status
    func getTrainingStatus() async throws -> TrainingStatus {
        let data = try await get(path: "/status")
        return try decoder.decode(TrainingStatus.self, from: data)
    }

    /// Get next recommended sample (active learning)
    func getNextSample() async throws -> NextSampleResponse {
        let data = try await get(path: "/next")
        return try decoder.decode(NextSampleResponse.self, from: data)
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
        try validateResponse(response)
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
        try validateResponse(response)
        return data
    }

    private func applyAuth(_ request: inout URLRequest) {
        if !apiKey.isEmpty {
            request.setValue(apiKey, forHTTPHeaderField: "X-API-Key")
        }
    }

    private func validateResponse(_ response: URLResponse) throws {
        guard let http = response as? HTTPURLResponse else {
            throw HILError.invalidResponse
        }
        guard (200...299).contains(http.statusCode) else {
            throw HILError.serverError(statusCode: http.statusCode)
        }
    }
}

// MARK: - Error Types

enum HILError: LocalizedError {
    case invalidURL
    case invalidResponse
    case serverError(statusCode: Int)
    case maskConversionFailed

    var errorDescription: String? {
        switch self {
        case .invalidURL:
            return "Invalid server URL"
        case .invalidResponse:
            return "Invalid response from server"
        case .serverError(let code):
            return "Server error (HTTP \(code))"
        case .maskConversionFailed:
            return "Failed to convert mask data"
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
