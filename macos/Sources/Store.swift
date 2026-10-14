import Foundation
import Combine

enum SpendPeriod: String, CaseIterable, Identifiable {
    case today, week, month
    var id: String { rawValue }
    var label: String {
        switch self {
        case .today: return "Today"
        case .week: return "7 days"
        case .month: return "30 days"
        }
    }
}

/// Everything the panel shows. Refreshes on a timer and whenever the panel opens.
@MainActor
final class Store: ObservableObject {
    enum Connection: Equatable {
        case starting
        case live
        case mock
        case failed(String)
    }

    @Published var connection: Connection = .starting
    @Published var meta: Meta?
    @Published var summary: Summary?
    @Published var limits: [ProviderLimits]?        // nil = endpoint missing or not loaded yet
    @Published var running: [RunningTree] = []
    @Published var recent: [ThreadSummary] = []
    @Published var checks: [VerifyCheck]?
    @Published var tree: ThreadDetail?          // live tree for the Tree card
    @Published var integrations: Integrations?
    private var scanQueued = false
    /// When the last scan ended and how long it took, to space out automatic scans.
    private var lastScanEnd = Date.distantPast
    private var lastScanSeconds: TimeInterval = 0
    @Published var scanning = false
    @Published var manualScanning = false     // only user-started scans show a spinner
    @Published var lastError: String?
    @Published var spendPeriod: SpendPeriod {
        didSet { UserDefaults.standard.set(spendPeriod.rawValue, forKey: "spendPeriod") }
    }

    let api: APIClient
    private var timer: Timer?
    private var refreshing = false

    init(api: APIClient) {
        self.api = api
        spendPeriod = SpendPeriod(rawValue: UserDefaults.standard.string(forKey: "spendPeriod") ?? "") ?? .month
    }

    func limits(for provider: String) -> ProviderLimits? {
        limits?.first { $0.provider == provider }
    }

    /// Highest used percentage across all available windows, for the menu bar text.
    var highestPercent: Double? {
        limits?.filter { $0.available }.flatMap { $0.windows ?? [] }.compactMap { $0.usedPercent }.max()
    }

    func startPolling(every seconds: TimeInterval = 30) {
        timer?.invalidate()
        timer = Timer.scheduledTimer(withTimeInterval: seconds, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.refresh() }
        }
        Task { await refresh() }
    }

    func stopPolling() {
        timer?.invalidate()
        timer = nil
    }

    func refresh() async {
        if case .failed = connection { return }
        if case .starting = connection { return }
        if refreshing { return }
        refreshing = true
        defer { refreshing = false }

        async let metaR = try? api.get("/api/meta", as: Meta.self)
        async let summaryR = fetchSummary()
        async let limitsR = try? api.get("/api/limits", as: LimitsResponse.self)
        async let nowR = try? api.get("/api/now", as: NowResponse.self)
        async let threadsR = try? api.get("/api/threads", query: ["limit": "100", "since": "7d", "sort": "started"], as: ThreadList.self)

        let (m, s, l, n, t) = await (metaR, summaryR, limitsR, nowR, threadsR)
        if let m = m { meta = m }
        if let v = s.0 { summary = v; lastError = nil } else { lastError = s.1 }
        limits = l?.providers
        running = n?.running ?? []
        if let t = t {
            // "Recent" means last activity, not start time: a long thread started this
            // morning and still going belongs above a short one started later.
            let live = Dictionary((n?.running ?? []).map { ($0.threadId, $0.lastActivity ?? Date()) }, uniquingKeysWith: max)
            func last(_ t: ThreadSummary) -> Date { live[t.id] ?? t.ended ?? t.started ?? .distantPast }
            recent = Array(t.threads.sorted { last($0) > last($1) }.prefix(3))
        }
        await loadTree(preferring: running.first?.threadId ?? recent.first?.id)
    }

    private func loadTree(preferring id: String?) async {
        guard let id = id else { tree = nil; return }
        if let d = try? await api.get("/api/threads/" + uriComponent(id), as: ThreadDetail.self) {
            tree = d
        } else if tree?.thread.id != id {
            tree = nil
        }
    }

    private func fetchSummary() async -> (Summary?, String?) {
        do { return (try await api.get("/api/summary", as: Summary.self), nil) } catch {
            return (nil, (error as? LocalizedError)?.errorDescription ?? error.localizedDescription)
        }
    }

    func loadChecks() async {
        checks = (try? await api.get("/api/verify", as: VerifyResponse.self))?.checks ?? []
    }

    func loadIntegrations() async {
        if let i = try? await api.get("/api/integrations", as: Integrations.self) { integrations = i }
    }

    /// Connect or disconnect Claude Code. Returns the server's error text, if any.
    func setClaudeConnected(_ on: Bool) async -> String? {
        do {
            let r = try await api.integration("claude", action: on ? "connect" : "disconnect")
            if let i = r.integrations { integrations = i }
            if !r.ok { return r.error ?? "The server could not change the Claude Code settings." }
            await refresh()
            return nil
        } catch {
            return (error as? LocalizedError)?.errorDescription ?? error.localizedDescription
        }
    }

    /// Manual or file-watcher scan. If one is already running (ours or the server's timer),
    /// wait for it to finish and run once more so the newest writes are picked up.
    func scan(manual: Bool = false) async {
        if manual { manualScanning = true }
        defer { if manual { manualScanning = false } }
        if scanning {
            scanQueued = true
            if manual { while scanning { try? await Task.sleep(nanoseconds: 200_000_000) } }
            return
        }
        scanning = true
        defer { scanning = false }
        // Busy agents write logs nonstop, and every write asks for a scan. Leave a gap
        // after each automatic scan so the app doesn't scan back to back all day.
        if !manual { await pause(until: lastScanEnd.addingTimeInterval(scanCooldown)) }
        var retried = false
        while true {
            let started = Date()
            defer { lastScanSeconds = Date().timeIntervalSince(started); lastScanEnd = Date() }
            do {
                try await api.scan()
            } catch APIError.busy where !retried {
                retried = true
                await waitForServerScan()
                continue
            } catch APIError.busy {
                // Still busy after waiting: the running scan will cover it.
            } catch {
                lastError = "Scan failed: \((error as? LocalizedError)?.errorDescription ?? error.localizedDescription)"
            }
            if scanQueued {
                scanQueued = false
                retried = false
                await pause(until: Date().addingTimeInterval(scanCooldown))
                continue
            }
            break
        }
        await refresh()
    }

    /// At least 10 s between automatic scans, more when scans are slow, never over a minute.
    private var scanCooldown: TimeInterval { min(60, max(10, lastScanSeconds * 3)) }

    private func pause(until date: Date) async {
        let wait = date.timeIntervalSinceNow
        if wait > 0 { try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000)) }
    }

    private func waitForServerScan() async {
        for _ in 0..<180 {
            try? await Task.sleep(nanoseconds: 1_000_000_000)
            guard let m = try? await api.get("/api/meta", as: Meta.self) else { return }
            if m.scanRunning != true { return }
        }
    }
}
