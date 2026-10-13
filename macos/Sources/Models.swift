import Foundation

// Shapes from docs/API.md. Every field is optional or defaulted so a newer or older
// server never breaks the panel; missing data just renders as "unknown".

struct Tokens: Codable, Equatable {
    var input: Int = 0
    var cachedInput: Int = 0
    var cacheWrite: Int = 0
    var output: Int = 0
    var reasoning: Int = 0
    var total: Int = 0

    init() {}
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        input = (try? c.decode(Int.self, forKey: .input)) ?? 0
        cachedInput = (try? c.decode(Int.self, forKey: .cachedInput)) ?? 0
        cacheWrite = (try? c.decode(Int.self, forKey: .cacheWrite)) ?? 0
        output = (try? c.decode(Int.self, forKey: .output)) ?? 0
        reasoning = (try? c.decode(Int.self, forKey: .reasoning)) ?? 0
        total = (try? c.decode(Int.self, forKey: .total)) ?? (input + cachedInput + cacheWrite + output)
    }
}

struct Money: Codable, Equatable {
    var cost: Double?
    var costKnown: Bool?
    var coveredCost: Double?
}

struct FamilyTotal: Codable {
    var family: String
    var tokens: Tokens?
    var money: Money?
}

struct ModelTotal: Codable {
    var model: String?
    var family: String?
    var tokens: Tokens?
    var money: Money?
}

struct Period: Codable {
    var tokens: Tokens?
    var money: Money?
    var nodes: Int?
    var threads: Int?
    var byFamily: [FamilyTotal]?
    var byModel: [ModelTotal]?
}

struct DailyPoint: Codable, Identifiable {
    var day: String
    var tokens: Tokens?
    var money: Money?
    var byFamily: [String: Int]?
    var id: String { day }
}

struct ThreadSummary: Codable, Identifiable {
    var id: String
    var source: String?
    var title: String?
    var projectPath: String?
    var projectName: String?
    var started: Date?
    var ended: Date?
    var wallMs: Int?
    var nodeCount: Int?
    var maxDepth: Int?
    var tokens: Tokens?
    var money: Money?
    var models: [String]?
    var families: [String]?
    var flagCount: Int?
    var sources: [String]?
    var t3Title: String?

    var displayTitle: String {
        let t = (t3Title?.isEmpty == false ? t3Title : title) ?? ""
        if !t.isEmpty { return t }
        return projectName ?? "Untitled thread"
    }

    var hasTitle: Bool {
        !((t3Title?.isEmpty == false ? t3Title : title) ?? "").isEmpty
    }
}

struct Summary: Codable {
    var periods: [String: Period]?
    var daily: [DailyPoint]?
    var topThreads: [ThreadSummary]?
    var flagCount: Int?
    var flagCounts: [String: Int]?
}

struct ThreadList: Codable {
    var total: Int?
    var threads: [ThreadSummary]
}

struct SourceInfo: Codable {
    var source: String
    var present: Bool?
    var root: String?
    var files: Int?
}

struct Meta: Codable {
    var version: String?
    var lastScan: Date?
    var scanRunning: Bool?
    var metadataOnly: Bool?
    var coveredByPlan: [String: Bool]?
    var timezone: String?
    var sources: [SourceInfo]?
}

struct LimitWindow: Codable, Identifiable {
    var id: String
    var label: String?
    var usedPercent: Double?
    var windowMinutes: Int?
    var resetsAt: Date?
    var resetsEstimated: Bool?
}

struct ProviderLimits: Codable, Identifiable {
    var provider: String
    var available: Bool
    var plan: String?
    var asOf: Date?
    var stale: Bool?
    var source: String?
    var reason: String?
    var windows: [LimitWindow]?
    var id: String { provider }
}

struct LimitsResponse: Codable {
    var providers: [ProviderLimits]
}

struct RunningTree: Codable, Identifiable {
    var threadId: String
    var title: String?
    var projectName: String?
    var model: String?
    var family: String?
    var started: Date?
    var lastActivity: Date?
    var activeSince: Date?       // start of the current burst of work
    var agentsRunning: Int?
    var tokens: Tokens?
    var money: Money?
    var id: String { threadId }
}

struct NowResponse: Codable {
    var running: [RunningTree]
}

struct VerifyCheck: Codable, Identifiable {
    var id: String
    var label: String?
    var status: String
    var detail: String?
    var compared: Int?
    var matched: Int?
}

struct VerifyResponse: Codable {
    var checks: [VerifyCheck]
}

struct ReadyFile: Codable {
    var port: Int
    var pid: Int?
    var version: String?
}

struct Totals: Codable {
    var tokens: Tokens?
    var money: Money?
}

struct TreeNode: Codable, Identifiable {
    var id: String
    var parentId: String?
    var depth: Int?
    var source: String?
    var kind: String?
    var agentType: String?
    var role: String?
    var description: String?
    var model: String?
    var family: String?
    var effort: String?
    var status: String?
    var started: Date?
    var ended: Date?
    var wallMs: Int?
    var selfTotals: Totals?
    var subtree: Totals?
    var childCount: Int?
    var flags: [String]?

    enum CodingKeys: String, CodingKey {
        case id, parentId, depth, source, kind, agentType, role, description, model, family, effort, status,
             started, ended, wallMs, subtree, childCount, flags
        case selfTotals = "self"
    }

    var isRunning: Bool { status == "running" }
}

struct ThreadDetail: Codable {
    var thread: ThreadSummary
    var nodes: [TreeNode]
}

struct ClaudeIntegration: Codable {
    var installed: Bool?
    var connected: Bool?
    var statusline: String?      // none | agent-tree | other
    var hooks: String?           // none | agent-tree | partial
    var settingsPath: String?
    var preview: String?
}

struct ToolPresence: Codable {
    var installed: Bool?
}

struct Integrations: Codable {
    var claude: ClaudeIntegration?
    var codex: ToolPresence?
    var t3: ToolPresence?
    var vorssaint: ToolPresence?
}

struct IntegrationResult: Codable {
    var ok: Bool
    var integrations: Integrations?
    var error: String?
}
