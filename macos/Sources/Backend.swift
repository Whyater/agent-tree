import Foundation

/// The only server origin the app will ever talk to or show: http://127.0.0.1:<port>.
enum Loopback {
    /// Returns a normalised base URL, or nil if `url` is anything other than plain
    /// http on 127.0.0.1 with a valid nonzero port (no credentials, path, query or fragment).
    static func validate(_ url: URL?) -> URL? {
        guard let u = url, let c = URLComponents(url: u, resolvingAgainstBaseURL: false),
              c.scheme?.lowercased() == "http", c.host == "127.0.0.1",
              let port = c.port, (1...65535).contains(port),
              c.user == nil, c.password == nil, c.query == nil, c.fragment == nil,
              c.path.isEmpty || c.path == "/" else { return nil }
        return URL(string: "http://127.0.0.1:\(port)")
    }

    static func validate(_ s: String?) -> URL? { validate(s.flatMap { URL(string: $0) }) }

    /// True when `url` has exactly the same origin as `base`.
    static func sameOrigin(_ url: URL?, _ base: URL?) -> Bool {
        guard let u = url, let b = base, let c = URLComponents(url: u, resolvingAgainstBaseURL: false) else { return false }
        return c.scheme?.lowercased() == "http" && c.host == "127.0.0.1" && c.port != nil && c.port == b.port
            && c.user == nil && c.password == nil
    }
}

/// Owns the bundled Python server: spawns `python3 -m agent_tree serve --port 0 ...`,
/// waits for its ready file, and reports when it dies.
///
/// Environment overrides (for development):
///   AGENT_TREE_MOCK=1          use JSON fixtures, never start a server
///   AGENT_TREE_DEV_URL=URL     use an already running server; must be http://127.0.0.1:<port>
///   AGENT_TREE_PYTHON=PATH     python3 to run instead of the bundled one
///   AGENT_TREE_APP_PATH=DIR    PYTHONPATH (the folder that contains agent_tree/)
final class Backend {
    enum State: Equatable {
        case idle
        case starting
        case ready(URL)
        case mock
        case failed(String)
    }

    var onChange: ((State) -> Void)?
    private(set) var state: State = .idle {
        didSet { let s = state; DispatchQueue.main.async { self.onChange?(s) } }
    }
    private(set) var version: String?

    private var process: Process?
    private var stopping = false
    private var stderrTail = ""
    private var tempDir: URL?
    private var logFile: FileHandle?
    private var pendingLine = Data()
    private let env = ProcessInfo.processInfo.environment

    var isMock: Bool { env["AGENT_TREE_MOCK"] == "1" }

    /// Validated AGENT_TREE_DEV_URL, if set and acceptable.
    private lazy var devURL: URL? = {
        guard let raw = env["AGENT_TREE_DEV_URL"], !raw.isEmpty else { return nil }
        if let u = Loopback.validate(raw) { return u }
        FileHandle.standardError.write(Data("Agent Tree: ignoring AGENT_TREE_DEV_URL (only http://127.0.0.1:<port> is allowed); starting the bundled server.\n".utf8))
        return nil
    }()

    /// Base URL for the full view. In mock mode this is the dev URL if one is given.
    var webURL: URL? {
        if case .ready(let u) = state { return Loopback.validate(u) }
        return devURL
    }

    var resourcesURL: URL { Bundle.main.resourceURL ?? URL(fileURLWithPath: "/") }

    var pythonPath: String {
        env["AGENT_TREE_PYTHON"] ?? resourcesURL.appendingPathComponent("python/bin/python3").path
    }

    var appPath: String {
        env["AGENT_TREE_APP_PATH"] ?? resourcesURL.appendingPathComponent("app").path
    }

    func start() {
        if isMock { state = .mock; return }
        if let dev = devURL { state = .ready(dev); return }
        guard FileManager.default.isExecutableFile(atPath: pythonPath) else {
            state = .failed("The bundled Python runtime is missing. Reinstall Agent Tree, or set AGENT_TREE_PYTHON and AGENT_TREE_APP_PATH when running from source.")
            return
        }
        stop()
        stopping = false
        stderrTail = ""
        state = .starting

        let dir = FileManager.default.temporaryDirectory.appendingPathComponent("agent-tree-\(UUID().uuidString.prefix(8))")
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        tempDir = dir
        let ready = dir.appendingPathComponent("ready.json")

        let p = Process()
        p.executableURL = URL(fileURLWithPath: pythonPath)
        p.arguments = ["-m", "agent_tree", "serve", "--port", "0", "--ready-file", ready.path,
                       "--parent-pid", String(ProcessInfo.processInfo.processIdentifier), "--no-open", "--rescan", "600"]
        var e = env
        e["PYTHONPATH"] = appPath
        e["PYTHONDONTWRITEBYTECODE"] = "1"
        e.removeValue(forKey: "AGENT_TREE_DEV_URL")
        p.environment = e
        p.standardInput = FileHandle.nullDevice

        // stdout and stderr share one pipe so request lines can be filtered out before
        // anything reaches the log.
        if logFile == nil { logFile = openLog() }
        let pipe = Pipe()
        p.standardOutput = pipe
        p.standardError = pipe
        pendingLine = Data()
        pipe.fileHandleForReading.readabilityHandler = { [weak self] h in
            let data = h.availableData
            guard !data.isEmpty else { h.readabilityHandler = nil; return }
            DispatchQueue.main.async { self?.consume(data) }
        }
        p.terminationHandler = { [weak self] proc in
            DispatchQueue.main.async {
                guard let self = self, self.process === proc else { return }
                self.process = nil
                if self.stopping { return }
                let last = self.stderrTail.split(separator: "\n").last.map(String.init) ?? ""
                var msg = "The background server stopped (exit code \(proc.terminationStatus))."
                if !last.isEmpty { msg += "\n" + last }
                self.state = .failed(msg)
            }
        }
        do {
            try p.run()
        } catch {
            state = .failed("Could not start the background server: \(error.localizedDescription)")
            return
        }
        process = p
        waitForReady(ready, deadline: Date().addingTimeInterval(90), proc: p)
    }

    /// Splits server output into lines, drops HTTP request lines (they contain paths and
    /// thread ids), and appends the rest to the log.
    private func consume(_ data: Data) {
        pendingLine.append(data)
        while let nl = pendingLine.firstIndex(of: 0x0A) {
            let lineData = pendingLine[pendingLine.startIndex...nl]
            pendingLine.removeSubrange(pendingLine.startIndex...nl)
            let line = String(decoding: lineData, as: UTF8.self)
            if Self.isRequestLine(line) { continue }
            logFile?.write(Data(line.utf8))
            stderrTail = String((stderrTail + line).suffix(600))
        }
        if pendingLine.count > 64_000 { pendingLine.removeAll() }
    }

    static func isRequestLine(_ line: String) -> Bool {
        for m in ["\"GET ", "\"POST ", "\"HEAD ", "\"PUT ", "\"DELETE ", "\"OPTIONS ", "GET /", "POST /"] where line.contains(m) {
            return true
        }
        return false
    }

    private func waitForReady(_ file: URL, deadline: Date, proc: Process) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) { [weak self] in
            guard let self = self, self.process === proc, proc.isRunning else { return }
            if let data = try? Data(contentsOf: file), let r = try? JSONDecoder().decode(ReadyFile.self, from: data) {
                guard let url = Loopback.validate("http://127.0.0.1:\(r.port)") else {
                    self.state = .failed("The background server reported an invalid port (\(r.port)).")
                    self.stop()
                    return
                }
                self.version = r.version
                self.state = .ready(url)
                return
            }
            if Date() > deadline {
                self.state = .failed("The background server did not start within 90 seconds.")
                self.stop()
                return
            }
            self.waitForReady(file, deadline: deadline, proc: proc)
        }
    }

    private func removeTempDir() {
        if let d = tempDir { try? FileManager.default.removeItem(at: d) }
        tempDir = nil
    }

    func stop() {
        stopping = true
        removeTempDir()
        guard let p = process else { return }
        process = nil
        if p.isRunning {
            p.terminate()
            // Give it a moment to exit cleanly, then make sure.
            let pid = p.processIdentifier
            DispatchQueue.global().asyncAfter(deadline: .now() + 2) {
                if p.isRunning { kill(pid, SIGKILL) }
            }
        }
    }

    /// Synchronous stop for app termination.
    func stopAndWait() {
        stopping = true
        defer { removeTempDir(); try? logFile?.close(); logFile = nil }
        guard let p = process else { return }
        process = nil
        guard p.isRunning else { return }
        p.terminate()
        let until = Date().addingTimeInterval(1.5)
        while p.isRunning && Date() < until { usleep(20_000) }
        if p.isRunning { kill(p.processIdentifier, SIGKILL) }
    }

    func restart() {
        stop()
        start()
    }

    /// ~/Library/Logs/Agent Tree/server.log, trimmed to its last 200 KB at each launch.
    private func openLog() -> FileHandle? {
        let fm = FileManager.default
        let dir = fm.homeDirectoryForCurrentUser.appendingPathComponent("Library/Logs/Agent Tree")
        try? fm.createDirectory(at: dir, withIntermediateDirectories: true)
        let file = dir.appendingPathComponent("server.log")
        let keep = 200_000
        if let data = try? Data(contentsOf: file), data.count > keep {
            var tail = data.suffix(keep)
            // Start at a line boundary.
            if let nl = tail.firstIndex(of: 0x0A) { tail = tail[tail.index(after: nl)...] }
            try? Data(tail).write(to: file, options: .atomic)
        }
        if !fm.fileExists(atPath: file.path) { fm.createFile(atPath: file.path, contents: nil) }
        guard let h = try? FileHandle(forWritingTo: file) else { return nil }
        h.seekToEndOfFile()
        return h
    }
}
