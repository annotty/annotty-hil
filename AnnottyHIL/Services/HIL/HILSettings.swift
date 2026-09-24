import Foundation
import Combine

/// Persistent settings for HIL server connection
class HILSettings: ObservableObject {
    @Published var serverURL: String
    @Published var apiKey: String
    @Published var isEnabled: Bool

    /// Whether the server URL has been configured
    var isConfigured: Bool {
        !serverURL.isEmpty && isEnabled
    }

    /// Query parameter names accepted as the API key in a connection URL
    private static let keyQueryNames: Set<String> = ["key", "api_key", "apikey"]

    /// Fill `serverURL` / `apiKey` from a connection URL such as
    /// `https://host.trycloudflare.com/web/?key=XXXX` (pasted or scanned from a QR code).
    /// The API base is the origin only: the path (e.g. `/web/`) belongs to the
    /// server's browser page, not the API. Returns false if no key is found.
    @discardableResult
    func applyConnectionURL(_ text: String) -> Bool {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let components = URLComponents(string: trimmed),
              let scheme = components.scheme, let host = components.host,
              let key = components.queryItems?.first(where: {
                  Self.keyQueryNames.contains($0.name.lowercased())
              })?.value, !key.isEmpty
        else { return false }

        var origin = "\(scheme)://\(host)"
        if let port = components.port { origin += ":\(port)" }
        serverURL = origin
        apiKey = key
        isEnabled = true
        return true
    }

    private var cancellables = Set<AnyCancellable>()

    init() {
        self.serverURL = UserDefaults.standard.string(forKey: "hil_server_url") ?? ""
        self.apiKey = UserDefaults.standard.string(forKey: "hil_api_key") ?? ""
        self.isEnabled = UserDefaults.standard.bool(forKey: "hil_enabled")

        $serverURL
            .dropFirst()
            .sink { UserDefaults.standard.set($0, forKey: "hil_server_url") }
            .store(in: &cancellables)

        $apiKey
            .dropFirst()
            .sink { UserDefaults.standard.set($0, forKey: "hil_api_key") }
            .store(in: &cancellables)

        $isEnabled
            .dropFirst()
            .sink { UserDefaults.standard.set($0, forKey: "hil_enabled") }
            .store(in: &cancellables)
    }
}
