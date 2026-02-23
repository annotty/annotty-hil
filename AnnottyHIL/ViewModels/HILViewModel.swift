import SwiftUI
import Combine

/// Coordinates HIL server interactions and state
/// Does NOT own CanvasViewModel — receives it as a method parameter
@MainActor
class HILViewModel: ObservableObject {
    // MARK: - Published State

    @Published var isConnected = false
    @Published var isLoading = false
    @Published var serverInfo: HILServerClient.ServerInfo?
    @Published var imageList: [HILServerClient.ImageInfo] = []
    @Published var currentImageId: String?
    @Published var trainingStatus: HILServerClient.TrainingStatus?
    @Published var errorMessage: String?

    @Published var isHILSubmitting = false

    // MARK: - Dependencies

    private let settings: HILSettings
    private let client: HILServerClient
    private let cache = CacheManager.shared

    private var pollingTask: Task<Void, Never>?

    init(settings: HILSettings) {
        self.settings = settings
        self.client = HILServerClient(baseURL: settings.serverURL)
    }

    // MARK: - Connection

    /// Connect to server and fetch image list
    func connect() async {
        guard settings.isConfigured else { return }
        await updateBaseURL()
        isLoading = true
        errorMessage = nil

        do {
            let info = try await client.getInfo()
            serverInfo = info
            isConnected = true

            let response = try await client.listImages()
            imageList = response.images
        } catch {
            isConnected = false
            errorMessage = error.localizedDescription
        }

        isLoading = false
    }

    // MARK: - On-Device Prediction

    /// Run on-device CoreML prediction via CanvasViewModel
    func requestPrediction(canvasVM: CanvasViewModel) {
        canvasVM.runUNetPrediction()
    }

    // MARK: - Submit & Next

    /// Submit current mask to server, then load next recommended image
    func submitAndNext(canvasVM: CanvasViewModel) async {
        guard let imageId = currentImageId else {
            errorMessage = "No image to submit"
            return
        }
        await updateBaseURL()
        isHILSubmitting = true
        errorMessage = nil

        do {
            // Export mask from canvas
            guard let maskPNG = canvasVM.exportMaskForServer() else {
                throw HILError.maskConversionFailed
            }

            // Submit to server
            _ = try await client.submitLabel(imageId: imageId, maskPNG: maskPNG)
            print("[HIL] Label submitted for \(imageId)")

            // Refresh image list
            let response = try await client.listImages()
            imageList = response.images

            // Update server info
            serverInfo = try await client.getInfo()

            // Get next sample
            await fetchNextSample()

            // Load next image into canvas
            if let nextId = currentImageId {
                await loadImageIntoCanvas(imageId: nextId, canvasVM: canvasVM)
            }
        } catch {
            errorMessage = "Submit failed: \(error.localizedDescription)"
        }

        isHILSubmitting = false
    }

    // MARK: - Training

    /// Start training on the server and begin polling status
    func startTraining() async {
        await updateBaseURL()
        errorMessage = nil

        do {
            _ = try await client.startTraining()
            startPollingTrainingStatus()
        } catch {
            errorMessage = "Failed to start training: \(error.localizedDescription)"
        }
    }

    /// Cancel ongoing training
    func cancelTraining() async {
        await updateBaseURL()
        do {
            _ = try await client.cancelTraining()
            pollingTask?.cancel()
            trainingStatus = nil
        } catch {
            errorMessage = "Failed to cancel training: \(error.localizedDescription)"
        }
    }

    // MARK: - Next Sample

    /// Fetch the next recommended sample from active learning
    func fetchNextSample() async {
        await updateBaseURL()
        do {
            let response = try await client.getNextSample()
            currentImageId = response.imageId
        } catch {
            errorMessage = "Failed to get next sample: \(error.localizedDescription)"
        }
    }

    // MARK: - Image Loading

    /// Download image from server and load into canvas
    func loadImageIntoCanvas(imageId: String, canvasVM: CanvasViewModel) async {
        await updateBaseURL()
        isLoading = true
        currentImageId = imageId

        do {
            // Download (or use cache)
            let imageData: Data
            if let cached = cache.loadImage(imageId: imageId) {
                imageData = cached
            } else {
                imageData = try await client.downloadImage(imageId: imageId)
                cache.saveImage(imageData, imageId: imageId)
            }

            // Save to project folder and import into canvas
            let tempURL = FileManager.default.temporaryDirectory.appendingPathComponent("\(imageId).png")
            try imageData.write(to: tempURL)
            canvasVM.importImage(from: tempURL)

            print("[HIL] Image \(imageId) loaded into canvas")
        } catch {
            errorMessage = "Failed to load image: \(error.localizedDescription)"
        }

        isLoading = false
    }

    // MARK: - Private Helpers

    private func updateBaseURL() async {
        await client.updateSettings(baseURL: settings.serverURL, apiKey: settings.apiKey)
    }

    // MARK: - Training Status Polling

    private func startPollingTrainingStatus() {
        pollingTask?.cancel()
        pollingTask = Task {
            while !Task.isCancelled {
                do {
                    let status = try await client.getTrainingStatus()
                    trainingStatus = status

                    if status.status == "completed" || status.status == "error" || status.status == "idle" {
                        break
                    }
                } catch {
                    break
                }

                try? await Task.sleep(nanoseconds: 3_000_000_000) // 3 seconds
            }
        }
    }
}
