import Foundation

enum Fmt {
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

    private static let wholeDollars: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.locale = Locale(identifier: "en_US")
        f.maximumFractionDigits = 0
        return f
    }()

    private static let centDollars: NumberFormatter = {
        let f = NumberFormatter()
        f.numberStyle = .currency
        f.currencyCode = "USD"
        f.locale = Locale(identifier: "en_US")
        f.minimumFractionDigits = 2
        f.maximumFractionDigits = 2
        return f
    }()

    /// "$4,521" for big numbers, "$1.42" for small ones, "?" when unknown.
    static func dollars(_ value: Double?) -> String {
        guard let v = value else { return "?" }
        if v > 0 && v < 0.005 { return "<$0.01" }
        if v >= 100 { return wholeDollars.string(from: NSNumber(value: v.rounded())) ?? "$\(Int(v))" }
        return centDollars.string(from: NSNumber(value: v)) ?? String(format: "$%.2f", v)
    }

    /// Money with the API's "partly unpriced" rule: "≥ $x" when some usage has no price.
    static func money(_ m: Money?) -> String {
        guard let m = m else { return "?" }
        guard let c = m.cost else { return "?" }
        if m.costKnown == false { return c > 0 ? "≥ " + dollars(c) : "?" }
        return dollars(c)
    }

    static func percent(_ p: Double?) -> String {
        guard let p = p else { return "–" }
        return "\(Int(p.rounded()))%"
    }

    /// "12h 47m", "6d 11h", "42m", "now"
    static func countdown(to date: Date?, from now: Date = Date()) -> String? {
        guard let date = date else { return nil }
        let s = Int(date.timeIntervalSince(now))
        if s <= 0 { return "now" }
        let d = s / 86400, h = (s % 86400) / 3600, m = (s % 3600) / 60
        if d > 0 { return "\(d)d \(h)h" }
        if h > 0 { return "\(h)h \(m)m" }
        return "\(max(m, 1))m"
    }

    /// Live elapsed clock: "2:28", "1:04:09"
    static func elapsed(since date: Date?, now: Date = Date()) -> String {
        guard let date = date else { return "–" }
        let s = max(0, Int(now.timeIntervalSince(date)))
        let h = s / 3600, m = (s % 3600) / 60, sec = s % 60
        if h >= 24 { return "\(h / 24)d \(h % 24)h" }
        if h > 0 { return String(format: "%d:%02d:%02d", h, m, sec) }
        return String(format: "%d:%02d", m, sec)
    }

    /// "just now", "2m ago", "3h ago", "2d ago"
    static func ago(_ date: Date?, now: Date = Date()) -> String {
        guard let date = date else { return "never" }
        let s = Int(now.timeIntervalSince(date))
        if s < 45 { return "just now" }
        if s < 3600 { return "\(max(1, s / 60))m ago" }
        if s < 86400 { return "\(s / 3600)h ago" }
        return "\(s / 86400)d ago"
    }

    /// "claude-opus-5-5" -> "Opus 5.5", "gpt-6.1-sol" -> "GPT-6.1 Sol"
    static func modelName(_ raw: String?) -> String {
        guard var m = raw?.lowercased(), !m.isEmpty else { return "Unknown model" }
        if m.hasPrefix("claude-") {
            m.removeFirst("claude-".count)
            var parts = m.split(separator: "-").map(String.init)
            // drop a trailing date stamp like 20250514
            if let last = parts.last, last.count == 8, Int(last) != nil { parts.removeLast() }
            guard let name = parts.first else { return raw! }
            let version = parts.dropFirst().joined(separator: ".")
            return name.capitalized + (version.isEmpty ? "" : " " + version)
        }
        if m.hasPrefix("gpt-") {
            let parts = m.split(separator: "-").map(String.init)
            // gpt, 6.1, sol -> GPT-6.1 Sol
            var out = "GPT"
            if parts.count > 1 { out += "-" + parts[1] }
            if parts.count > 2 { out += " " + parts[2...].map { $0.capitalized }.joined(separator: " ") }
            return out
        }
        return raw!
    }

    /// "max_20x" -> "Max 20x", "prolite" -> "Prolite"
    static func plan(_ raw: String?) -> String? {
        guard let r = raw?.replacingOccurrences(of: "_", with: " "), !r.isEmpty else { return nil }
        return r.prefix(1).uppercased() + r.dropFirst()
    }
}

/// encodeURIComponent, for ids in paths and hash routes.
func uriComponent(_ s: String) -> String {
    var allowed = CharacterSet.alphanumerics
    allowed.insert(charactersIn: "-_.!~*'()")
    return s.addingPercentEncoding(withAllowedCharacters: allowed) ?? s
}

enum ISODate {
    private static let fractional: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
    private static let plain: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        return f
    }()

    static func parse(_ s: String) -> Date? {
        fractional.date(from: s) ?? plain.date(from: s)
    }
}
