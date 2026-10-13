import Foundation

enum APIError: Error, LocalizedError {
    case notFound
    case http(Int)
    case noServer
    case busy

    var errorDescription: String? {
        switch self {
        case .notFound: return "Not available on this server version."
        case .http(let c): return "Server answered \(c)."
        case .noServer: return "The server is not running."
        case .busy: return "A scan is already running."
        }
    }
}

/// Talks to the local server over loopback, or reads fixtures in mock mode.
final class APIClient {
    private let session: URLSession
    var baseURL: URL?
    /// baseURL, but only if it is http://127.0.0.1:<port>. Anything else is never contacted.
    private var server: URL? { Loopback.validate(baseURL) }
    let fixturesDir: URL?
    /// Mock mode shifts every fixture timestamp so "last scan" lands at app launch;
    /// that keeps countdowns and elapsed timers realistic in screenshots.
    private var mockShift: TimeInterval = 0

    init(mockFixtures: URL? = nil) {
        let cfg = URLSessionConfiguration.ephemeral
        cfg.connectionProxyDictionary = [:]          // never route loopback through a VPN or proxy
        cfg.timeoutIntervalForRequest = 15
        cfg.requestCachePolicy = .reloadIgnoringLocalCacheData
        cfg.waitsForConnectivity = false
        session = URLSession(configuration: cfg)
        fixturesDir = mockFixtures
        if let dir = mockFixtures,
           let data = try? Data(contentsOf: dir.appendingPathComponent("meta.json")),
           let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
           let ls = obj["last_scan"] as? String, let d = ISODate.parse(ls) {
            mockShift = Date().addingTimeInterval(-90).timeIntervalSince(d)
        }
    }

    private func decoder() -> JSONDecoder {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        let shift = mockShift
        d.dateDecodingStrategy = .custom { dec in
            let c = try dec.singleValueContainer()
            let s = try c.decode(String.self)
            guard let date = ISODate.parse(s) else {
                throw DecodingError.dataCorruptedError(in: c, debugDescription: "Bad date \(s)")
            }
            return date.addingTimeInterval(shift)
        }
        return d
    }

    func get<T: Decodable>(_ path: String, query: [String: String] = [:], as: T.Type) async throws -> T {
        let data: Data
        if let dir = fixturesDir {
            // Any single-thread request reads the one sample tree.
            let name = path.hasPrefix("/api/threads/") ? "thread_detail"
                : path.replacingOccurrences(of: "/api/", with: "").replacingOccurrences(of: "/", with: "_")
            let file = dir.appendingPathComponent(name + ".json")
            guard FileManager.default.fileExists(atPath: file.path) else { throw APIError.notFound }
            data = try Data(contentsOf: file)
        } else {
            guard let base = server else { throw APIError.noServer }
            // `path` is already percent-encoded (thread ids contain ':'), so join as a string.
            guard path.hasPrefix("/api/"), let joined = URL(string: base.absoluteString + path),
                  var comps = URLComponents(url: joined, resolvingAgainstBaseURL: false) else { throw APIError.http(400) }
            if !query.isEmpty { comps.queryItems = query.sorted { $0.key < $1.key }.map { URLQueryItem(name: $0.key, value: $0.value) } }
            guard let url = comps.url, Loopback.sameOrigin(url, base) else { throw APIError.http(400) }
            var req = URLRequest(url: url)
            req.setValue("application/json", forHTTPHeaderField: "Accept")
            let (d, resp) = try await session.data(for: req)
            let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
            if code == 404 { throw APIError.notFound }
            guard (200..<300).contains(code) else { throw APIError.http(code) }
            data = d
        }
        return try decoder().decode(T.self, from: data)
    }

    func scan() async throws {
        if fixturesDir != nil {
            try await Task.sleep(nanoseconds: 700_000_000)
            return
        }
        guard let base = server else { throw APIError.noServer }
        var req = URLRequest(url: base.appendingPathComponent("api/scan"))
        req.httpMethod = "POST"
        req.setValue("1", forHTTPHeaderField: "X-Agent-Tree")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = Data("{}".utf8)
        req.timeoutInterval = 600
        let (_, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if code == 409 { throw APIError.busy }
        guard (200..<300).contains(code) else { throw APIError.http(code) }
    }

    /// POST /api/integrations/<tool> with {"action": ...}. Errors come back in the body.
    func integration(_ tool: String, action: String) async throws -> IntegrationResult {
        if let dir = fixturesDir {
            // Mock: flip the connected flag in memory so the UI can be exercised.
            var current = try decoder().decode(Integrations.self, from: Data(contentsOf: dir.appendingPathComponent("integrations.json")))
            current.claude?.connected = action == "connect"
            current.claude?.statusline = action == "connect" ? "agent-tree" : "none"
            current.claude?.hooks = action == "connect" ? "agent-tree" : "none"
            try await Task.sleep(nanoseconds: 400_000_000)
            return IntegrationResult(ok: true, integrations: current, error: nil)
        }
        guard let base = server, tool == "claude" else { throw APIError.noServer }
        var req = URLRequest(url: base.appendingPathComponent("api/integrations/\(tool)"))
        req.httpMethod = "POST"
        req.setValue("1", forHTTPHeaderField: "X-Agent-Tree")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.httpBody = try JSONSerialization.data(withJSONObject: ["action": action])
        let (data, resp) = try await session.data(for: req)
        let code = (resp as? HTTPURLResponse)?.statusCode ?? 0
        if let r = try? decoder().decode(IntegrationResult.self, from: data) { return r }
        throw APIError.http(code)
    }
}
