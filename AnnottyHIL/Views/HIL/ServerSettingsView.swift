import SwiftUI

/// Settings sheet for HIL server configuration
struct ServerSettingsView: View {
    @ObservedObject var settings: HILSettings
    @ObservedObject var hilViewModel: HILViewModel
    @Environment(\.dismiss) private var dismiss

    @State private var testResult: String?
    @State private var isTesting = false

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    PasteButton(payloadType: String.self) { strings in
                        guard let text = strings.first else { return }
                        Task { @MainActor in applyConnection(text) }
                    }
                } header: {
                    Text("かんたん接続")
                } footer: {
                    Text("サーバーが表示するキー付きURL（例: https://xxxx.trycloudflare.com/web/?key=...）を貼り付けると、URLとAPIキーを自動で入力して接続テストします。")
                }

                Section("HIL Server") {
                    Toggle("Enable HIL", isOn: $settings.isEnabled)

                    if settings.isEnabled {
                        TextField("Server URL", text: $settings.serverURL)
                            .keyboardType(.URL)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()
                            .onChange(of: settings.serverURL) { _, newValue in
                                // A key-bearing URL typed/pasted here is split automatically
                                if newValue.contains("key=") { applyConnection(newValue) }
                            }

                        SecureField("API Key", text: $settings.apiKey)
                            .textInputAutocapitalization(.never)
                            .autocorrectionDisabled()

                        Button(action: testConnection) {
                            HStack {
                                if isTesting {
                                    ProgressView()
                                        .scaleEffect(0.8)
                                }
                                Text(isTesting ? "Testing..." : "Test Connection")
                            }
                        }
                        .disabled(settings.serverURL.isEmpty || isTesting)

                        if let result = testResult {
                            Text(result)
                                .font(.caption)
                                .foregroundColor(result.hasPrefix("OK") ? .green : .red)
                        }
                    }
                }

                if settings.isConfigured {
                    Section("Status") {
                        HStack {
                            Circle()
                                .fill(hilViewModel.isConnected ? .green : .gray)
                                .frame(width: 10, height: 10)
                            Text(hilViewModel.isConnected ? "Connected" : "Not connected")
                                .foregroundColor(.secondary)
                        }
                    }
                }
            }
            .navigationTitle("HIL Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    /// Split a connection URL into server URL + API key, then test right away
    private func applyConnection(_ text: String) {
        if settings.applyConnectionURL(text) {
            testConnection()
        } else {
            testResult = "Error: キー付きURLではありません（?key=... が見つかりません）"
        }
    }

    private func testConnection() {
        isTesting = true
        testResult = nil

        Task {
            do {
                let client = HILServerClient(baseURL: settings.serverURL, apiKey: settings.apiKey)
                let info = try await client.getInfo()
                testResult = "OK — \(info.totalImages) images, \(info.labeledImages) labeled"
            } catch {
                testResult = "Error: \(error.localizedDescription)"
            }
            isTesting = false
        }
    }
}
