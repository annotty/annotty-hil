import SwiftUI

/// Dashboard showing HIL server images and controls
struct HILDashboardView: View {
    @ObservedObject var hilViewModel: HILViewModel
    @ObservedObject var settings: HILSettings
    let onImageSelected: (String) -> Void
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 0) {
                // Status bar
                statusBar
                    .padding()

                Divider()

                if hilViewModel.isLoading {
                    Spacer()
                    ProgressView("Loading images...")
                    Spacer()
                } else if hilViewModel.imageList.isEmpty {
                    Spacer()
                    Text("No images on server")
                        .foregroundColor(.secondary)
                    Spacer()
                } else {
                    // Image list
                    ScrollView {
                        LazyVStack(spacing: 8) {
                            ForEach(hilViewModel.imageList) { image in
                                imageRow(image)
                            }
                        }
                        .padding()
                    }
                }

                Divider()

                // Bottom controls
                bottomControls
                    .padding()
            }
            .navigationTitle("HIL Dashboard")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
            .task {
                await hilViewModel.connect()
            }
        }
    }

    // MARK: - Status Bar

    private var statusBar: some View {
        HStack {
            // Connection status
            HStack(spacing: 6) {
                Circle()
                    .fill(hilViewModel.isConnected ? .green : .red)
                    .frame(width: 10, height: 10)
                Text(hilViewModel.isConnected ? "Connected" : "Disconnected")
                    .font(.caption)
            }

            Spacer()

            // Progress
            if let info = hilViewModel.serverInfo {
                VStack(alignment: .trailing, spacing: 2) {
                    Text("\(info.labeledImages) / \(info.totalImages) labeled")
                        .font(.caption)
                    ProgressView(value: Double(info.labeledImages), total: max(Double(info.totalImages), 1))
                        .frame(width: 120)
                }
            }
        }
    }

    // MARK: - Image Row

    private func imageRow(_ image: HILServerClient.ImageInfo) -> some View {
        Button {
            onImageSelected(image.id)
        } label: {
            HStack(spacing: 12) {
                // Thumbnail placeholder
                RoundedRectangle(cornerRadius: 6)
                    .fill(Color(white: 0.2))
                    .frame(width: 50, height: 50)
                    .overlay {
                        Image(systemName: "photo")
                            .foregroundColor(.gray)
                    }

                // File info
                VStack(alignment: .leading, spacing: 4) {
                    Text(image.id)
                        .font(.subheadline)
                        .foregroundColor(.primary)
                        .lineLimit(1)
                }

                Spacer()

                // Label status badge
                if image.hasLabel {
                    Label("Labeled", systemImage: "checkmark.circle.fill")
                        .font(.caption2)
                        .foregroundColor(.green)
                } else {
                    Label("Unlabeled", systemImage: "circle")
                        .font(.caption2)
                        .foregroundColor(.secondary)
                }

                Image(systemName: "chevron.right")
                    .font(.caption)
                    .foregroundColor(.secondary)
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 10)
            .background(Color(white: 0.15))
            .cornerRadius(10)
        }
        .buttonStyle(.plain)
    }

    // MARK: - Bottom Controls

    private var bottomControls: some View {
        HStack(spacing: 12) {
            // Next (Active Learning) button
            Button {
                Task {
                    await hilViewModel.fetchNextSample()
                    if let nextId = hilViewModel.currentImageId {
                        onImageSelected(nextId)
                        dismiss()
                    }
                }
            } label: {
                Label("Next (AL)", systemImage: "sparkles")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.orange)

            // Start Training button
            Button {
                Task { await hilViewModel.startTraining() }
            } label: {
                Label("Train", systemImage: "brain")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.purple)
            .disabled(hilViewModel.trainingStatus?.status == "running")
        }
    }
}
