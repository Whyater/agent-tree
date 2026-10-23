import Foundation

/// The part of ~/.agent-tree/live.json the widget shows. The file is written by the
/// Agent Tree server after every scan (schema in agent_tree/live.py); unknown fields
/// are ignored and a different schema number reads as no file.
struct LiveSnapshot: Decodable {
    struct Period: Decodable {
        let tokens: Int
        let cost: Double?
    }

    struct Running: Decodable {
        let threadId: String
        let title: String?
        let projectName: String?
        let model: String?
        let family: String?
        let activeSince: String?
        let agentsRunning: Int?
    }

    struct Node: Decodable {
        let id: String
        let parentId: String?
        let depthValue: Int?
        var depth: Int { depthValue ?? 0 }

        enum CodingKeys: String, CodingKey {
            case id, parentId, depthValue = "depth", label, family, running, tokens
        }
        let label: String?
        let family: String?
        let running: Bool
        let tokens: Int?
    }

    struct Tree: Decodable {
        let threadId: String
        let title: String?
        let projectName: String?
        let live: Bool
        let nodeCount: Int?
        let nodes: [Node]
    }

    let schema: Int
    let generatedAt: String
    let totals: [String: Period]
    let running: [Running]
    let tree: Tree?

    static let schemaVersion = 1
    /// The server rewrites the file at least every ten minutes while it runs; anything
    /// older means Agent Tree was quit, so nothing in it is still working.
    static let freshFor: TimeInterval = 15 * 60
    static let clockSlack: TimeInterval = 120
    /// Bigger files are not ours to show.
    static let maximumSize = 1 << 20

    var generated: Date? { Self.parseDate(generatedAt) }

    /// Whether the running parts can still be trusted at `date`. A file dated ahead of the
    /// clock (the clock was set back since) can't vouch for anything either.
    func isFresh(at date: Date) -> Bool {
        guard let g = generated else { return false }
        return (-Self.clockSlack..<Self.freshFor).contains(date.timeIntervalSince(g))
    }

    /// The real home folder. Inside the widget's sandbox NSHomeDirectory() points at the
    /// container, so ask the user database instead.
    static var home: URL {
        if let pw = getpwuid(getuid()), let dir = pw.pointee.pw_dir {
            return URL(fileURLWithPath: String(cString: dir), isDirectory: true)
        }
        return URL(fileURLWithPath: NSHomeDirectory(), isDirectory: true)
    }

    static var fileURL: URL {
        let env = ProcessInfo.processInfo.environment["AGENT_TREE_HOME"]
        let dir = env.map { URL(fileURLWithPath: $0, isDirectory: true) } ?? home.appendingPathComponent(".agent-tree", isDirectory: true)
        return dir.appendingPathComponent("live.json")
    }

    /// Nil for a missing, oversized or unreadable file, or one with another schema.
    static func load(from url: URL = fileURL) -> LiveSnapshot? {
        guard let attrs = try? FileManager.default.attributesOfItem(atPath: url.path),
              attrs[.type] as? FileAttributeType == .typeRegular,
              let size = attrs[.size] as? Int, size <= maximumSize,
              let data = try? Data(contentsOf: url) else { return nil }
        return decode(data)
    }

    static func decode(_ data: Data) -> LiveSnapshot? {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        guard let s = try? d.decode(LiveSnapshot.self, from: data), s.schema == schemaVersion else { return nil }
        return s
    }

    static func parseDate(_ s: String?) -> Date? {
        guard let s = s else { return nil }
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if let d = f.date(from: s) { return d }
        f.formatOptions = [.withInternetDateTime]
        return f.date(from: s)
    }
}

enum WidgetFormat {
    /// 1.2M, 340K, 11B, 950
    static func tokens(_ n: Int) -> String {
        let v = Double(n)
        func trim(_ x: Double, _ unit: String) -> String {
            let s = x >= 100 ? String(format: "%.0f", x) : String(format: "%.1f", x)
            return (s.hasSuffix(".0") ? String(s.dropLast(2)) : s) + unit
        }
        switch abs(v) {
        case 1e9...: return trim(v / 1e9, "B")
        case 1e6...: return trim(v / 1e6, "M")
        case 1e3...: return trim(v / 1e3, "K")
        default: return "\(n)"
        }
    }

    /// "$4,521" for big numbers, "$1.42" for small ones, "?" when unknown.
    static func dollars(_ value: Double?) -> String {
        guard let v = value else { return "?" }
        if v > 0 && v < 0.005 { return "<$0.01" }
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.locale = Locale(identifier: "en_US")
        f.maximumFractionDigits = v >= 100 ? 0 : 2
        f.minimumFractionDigits = v >= 100 ? 0 : 2
        return f.string(from: NSNumber(value: v >= 100 ? v.rounded() : v)) ?? String(format: "$%.2f", v)
    }
}
