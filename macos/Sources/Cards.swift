import SwiftUI
import AppKit

// MARK: - Building blocks

struct Card<Content: View, Accessory: View>: View {
    let title: String
    let icon: String
    var iconColor: Color = Theme.secondary
    @ViewBuilder var accessory: () -> Accessory
    @ViewBuilder var content: () -> Content

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 5) {
                Image(systemName: icon)
                    .font(.system(size: 9.5, weight: .semibold))
                    .foregroundStyle(iconColor)
                    .frame(width: 12)
                Text(title)
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(Theme.cardTitle)
                Spacer(minLength: 4)
                accessory()
            }
            .accessibilityElement(children: .combine)
            content()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .background(RoundedRectangle(cornerRadius: 18, style: .continuous).fill(Theme.cardFill))
        .overlay(RoundedRectangle(cornerRadius: 18, style: .continuous).strokeBorder(Theme.cardStroke, lineWidth: 0.5))
    }
}

extension Card where Accessory == EmptyView {
    init(title: String, icon: String, iconColor: Color = Theme.secondary, @ViewBuilder content: @escaping () -> Content) {
        self.title = title
        self.icon = icon
        self.iconColor = iconColor
        self.accessory = { EmptyView() }
        self.content = content
    }
}

struct Chip: View {
    let text: String
    var color: Color = Theme.secondary
    var body: some View {
        Text(text)
            .font(.system(size: 9.5, weight: .semibold))
            .foregroundStyle(color)
            .padding(.horizontal, 5)
            .padding(.vertical, 1.5)
            .background(Capsule().fill(color.opacity(0.16)))
            .lineLimit(1)
            .fixedSize()
    }
}

/// Thin rounded progress bar with an optional pace tick (where usage would be if it
/// were spread evenly over the window).
struct UsageBar: View {
    let fraction: Double
    let color: Color
    var pace: Double? = nil
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        GeometryReader { geo in
            ZStack(alignment: .leading) {
                Capsule().fill(Theme.track)
                Capsule()
                    .fill(color)
                    .frame(width: max(fraction > 0 ? 5 : 0, geo.size.width * min(max(fraction, 0), 1)))
                if let p = pace, p > 0.02, p < 0.98 {
                    RoundedRectangle(cornerRadius: 0.75)
                        .fill(Color.white.opacity(0.85))
                        .frame(width: 1.5, height: 8)
                        .offset(x: geo.size.width * p - 0.75)
                }
            }
        }
        .frame(height: 4)
        .animation(reduceMotion ? nil : .easeOut(duration: 0.35), value: fraction)
    }
}

/// Horizontal bar split into coloured segments, with small gaps between them.
struct SegmentBar: View {
    let segments: [(Double, Color)]
    var body: some View {
        GeometryReader { geo in
            let total = segments.map { $0.0 }.reduce(0, +)
            let gaps = CGFloat(max(segments.count - 1, 0)) * 3
            HStack(spacing: 3) {
                if total <= 0 {
                    Capsule().fill(Theme.track)
                } else {
                    ForEach(Array(segments.enumerated()), id: \.offset) { _, seg in
                        Capsule()
                            .fill(seg.1)
                            .frame(width: max(3, (geo.size.width - gaps) * seg.0 / total))
                    }
                }
            }
        }
        .frame(height: 4)
    }
}

// MARK: - Limits

struct LimitsCard: View {
    let title: String
    let provider: String
    let family: String
    let icon: String
    let data: ProviderLimits?
    let loaded: Bool
    var openSettings: () -> Void

    var color: Color { Theme.family(family) }

    var body: some View {
        Card(title: title, icon: icon, iconColor: color) {
            HStack(spacing: 5) {
                if let d = data, d.available, d.stale == true, let asOf = d.asOf {
                    Text(Fmt.ago(asOf))
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.tertiary)
                        .help("Last recorded \(Fmt.ago(asOf)). Limits update when you use \(title).")
                }
                if let plan = Fmt.plan(data?.plan) { Chip(text: plan, color: color) }
            }
        } content: {
            if let d = data, d.available, let windows = d.windows, !windows.isEmpty {
                VStack(alignment: .leading, spacing: 7) {
                    ForEach(windows.prefix(2)) { w in
                        LimitRow(window: w, color: color)
                    }
                }
            } else {
                VStack(alignment: .leading, spacing: 4) {
                    Text(loaded ? "Not recorded" : "Loading…")
                        .font(.system(size: 10.5, weight: .medium))
                        .foregroundStyle(Theme.secondary)
                    if loaded {
                        if provider == "claude" {
                            Button(action: openSettings) {
                                Text("Connect Claude Code in Settings")
                                    .font(.system(size: 11))
                                    .foregroundStyle(Theme.accent)
                            }
                            .buttonStyle(.plain)
                            .help(data?.reason ?? "Agent Tree can read Claude limits once Claude Code is connected.")
                        } else {
                            Text(data?.reason ?? "No limit data found yet.")
                                .font(.system(size: 10))
                                .foregroundStyle(Theme.tertiary)
                                .lineLimit(2)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
    }
}

struct LimitRow: View {
    let window: LimitWindow
    let color: Color

    var pace: Double? {
        guard let mins = window.windowMinutes, mins > 0, let r = window.resetsAt else { return nil }
        let start = r.addingTimeInterval(-Double(mins) * 60)
        return Date().timeIntervalSince(start) / (Double(mins) * 60)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(spacing: 5) {
                Text(window.label ?? window.id)
                    .font(.system(size: 10.5, weight: .semibold))
                    .foregroundStyle(Theme.primary.opacity(0.9))
                if let c0 = Fmt.countdown(to: window.resetsAt) {
                    let c = (window.resetsEstimated == true ? "≈" : "") + c0
                    HStack(spacing: 2) {
                        Image(systemName: "arrow.clockwise").font(.system(size: 7.5, weight: .semibold))
                        Text(c).font(.system(size: 9.5).monospacedDigit())
                    }
                    .foregroundStyle(Theme.tertiary)
                    .help(window.resetsEstimated == true ? "Estimated reset in about \(c0)" : "Resets in \(c0)")
                }
                Spacer(minLength: 4)
                Text(Fmt.percent(window.usedPercent))
                    .font(.system(size: 12, weight: .semibold, design: .rounded).monospacedDigit())
                    .foregroundStyle((window.usedPercent ?? 0) >= 100 ? Theme.warn : Theme.primary)
            }
            UsageBar(fraction: (window.usedPercent ?? 0) / 100, color: color, pace: pace)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(window.label ?? window.id) limit \(Fmt.percent(window.usedPercent)) used" +
                            (Fmt.countdown(to: window.resetsAt).map { ", resets in \($0)" } ?? ""))
    }
}

// MARK: - Spending

struct SpendingCard: View {
    @ObservedObject var store: Store

    var period: Period? { store.summary?.periods?[store.spendPeriod.rawValue] }

    var body: some View {
        Card(title: "Spending", icon: "dollarsign.circle") {
            PeriodButton(selection: $store.spendPeriod)
        } content: {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 5) {
                    Text(Fmt.money(period?.money))
                        .font(.system(size: 20, weight: .bold, design: .rounded).monospacedDigit())
                        .foregroundStyle(Theme.primary)
                        .lineLimit(1)
                        .minimumScaleFactor(0.6)
                    Text("est.")
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.tertiary)
                    Spacer(minLength: 2)
                    if let covered = period?.money?.coveredCost, covered >= 0.5 {
                        Chip(text: "+\(Fmt.dollars(covered)) plan", color: Theme.secondary)
                            .help("Usage covered by your plan, estimated at API prices")
                    }
                }
                SegmentBar(segments: familySegments)
                Text(tokenLine)
                    .font(.system(size: 10).monospacedDigit())
                    .foregroundStyle(Theme.secondary)
                    .lineLimit(1)
            }
            .accessibilityElement(children: .combine)
        }
    }

    var familySegments: [(Double, Color)] {
        let fams = (period?.byFamily ?? []).sorted { a, b in
            func rank(_ f: String) -> Int { f == "anthropic" ? 0 : f == "openai" ? 1 : 2 }
            return rank(a.family) < rank(b.family)
        }
        let useCost = fams.contains { ($0.money?.cost ?? 0) > 0 }
        return fams.map { f in
            (useCost ? (f.money?.cost ?? 0) : Double(f.tokens?.total ?? 0), Theme.family(f.family))
        }.filter { $0.0 > 0 }
    }

    var tokenLine: String {
        guard let t = period?.tokens else { return "No usage yet" }
        var s = "\(Fmt.tokens(t.total)) tokens"
        if t.total > 0 {
            let share = Double(t.cachedInput) / Double(t.total) * 100
            s += " · \(Int(share.rounded()))% from cache"
        }
        return s
    }
}

// MARK: - Now

struct NowCard: View {
    let running: [RunningTree]
    var open: (String) -> Void
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pulse = false

    var body: some View {
        Card(title: "Now", icon: "waveform.path.ecg", iconColor: running.isEmpty ? Theme.secondary : Theme.anthropic) {
            if !running.isEmpty {
                Circle()
                    .fill(Theme.family(running.first?.family))
                    .frame(width: 6, height: 6)
                    .opacity(pulse ? 0.35 : 1)
                    .onAppear {
                        guard !reduceMotion else { return }
                        withAnimation(.easeInOut(duration: 1.1).repeatForever(autoreverses: true)) { pulse = true }
                    }
                    .accessibilityLabel("Running")
            }
        } content: {
            if running.isEmpty {
                VStack(alignment: .leading, spacing: 3) {
                    Text("Nothing running")
                        .font(.system(size: 10.5, weight: .medium))
                        .foregroundStyle(Theme.secondary)
                    Text("Agents active in the last 10 minutes show up here.")
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            } else {
                TimelineView(.periodic(from: .now, by: 1)) { ctx in
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(running.prefix(2)) { r in
                            Button { open(r.threadId) } label: { row(r, now: ctx.date) }
                                .buttonStyle(.plain)
                        }
                        if running.count > 2 {
                            Text("+\(running.count - 2) more")
                                .font(.system(size: 10))
                                .foregroundStyle(Theme.tertiary)
                        }
                    }
                }
            }
        }
    }

    func row(_ r: RunningTree, now: Date) -> some View {
        let color = Theme.family(r.family)
        var bits = [Fmt.modelName(r.model)]
        if let t = r.tokens { bits.append(Fmt.tokens(t.total)) }
        bits.append(Fmt.money(r.money))
        return VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 5) {
                Image(systemName: "asterisk")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(color)
                Text(r.projectName ?? r.title ?? "Untitled")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(Theme.primary)
                    .lineLimit(1)
                Spacer(minLength: 4)
                Text(Fmt.elapsed(since: r.activeSince ?? r.started, now: now))
                    .font(.system(size: 11, weight: .semibold).monospacedDigit())
                    .foregroundStyle(color)
            }
            Text(bits.joined(separator: " · "))
                .font(.system(size: 10).monospacedDigit())
                .foregroundStyle(Theme.secondary)
                .lineLimit(1)
                .padding(.leading, 13)
        }
        .contentShape(Rectangle())
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(r.projectName ?? "Thread")\(r.agentsRunning.map { ", \($0) agents running" } ?? "") active for \(Fmt.elapsed(since: r.activeSince ?? r.started, now: now)), \(bits.joined(separator: ", "))")
        .accessibilityHint("Opens the thread in the full view")
    }
}

// MARK: - Trend

struct TrendCard: View {
    let daily: [DailyPoint]
    let monthMoney: Money?

    var body: some View {
        Card(title: "Trend", icon: "chart.bar.fill") {
            Text("30 days · \(Fmt.money(monthMoney))")
                .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                .foregroundStyle(Theme.secondary)
        } content: {
            VStack(spacing: 5) {
                bars.frame(height: 34)
                HStack {
                    Text(dayLabel(daily.first?.day))
                    Spacer()
                    HStack(spacing: 10) {
                        legend("Claude", Theme.anthropic)
                        legend("OpenAI", Theme.openai)
                    }
                    Spacer()
                    Text("Today")
                }
                .font(.system(size: 9.5))
                .foregroundStyle(Theme.tertiary)
            }
        }
    }

    func legend(_ name: String, _ c: Color) -> some View {
        HStack(spacing: 4) {
            RoundedRectangle(cornerRadius: 1.5).fill(c).frame(width: 6, height: 6)
            Text(name)
        }
    }

    var bars: some View {
        let maxTotal = max(1, daily.map { total($0) }.max() ?? 1)
        return GeometryReader { geo in
            let n = max(daily.count, 1)
            let gap: CGFloat = 2
            let w = max(2, (geo.size.width - gap * CGFloat(n - 1)) / CGFloat(n))
            HStack(alignment: .bottom, spacing: gap) {
                ForEach(daily) { d in
                    let a = Double(d.byFamily?["anthropic"] ?? 0)
                    let o = Double(d.byFamily?["openai"] ?? 0)
                    let rest = max(0, Double(total(d)) - a - o)
                    let h = geo.size.height
                    VStack(spacing: 0) {
                        Spacer(minLength: 0)
                        if total(d) == 0 {
                            RoundedRectangle(cornerRadius: 1).fill(Theme.track).frame(height: 2)
                        } else {
                            Rectangle().fill(Theme.other).frame(height: h * rest / Double(maxTotal))
                            Rectangle().fill(Theme.openai).frame(height: h * o / Double(maxTotal))
                            Rectangle().fill(Theme.anthropic).frame(height: h * a / Double(maxTotal))
                        }
                    }
                    .frame(width: w)
                    .clipShape(RoundedRectangle(cornerRadius: 2, style: .continuous))
                    .help("\(d.day): \(Fmt.tokens(total(d))) tokens, \(Fmt.money(d.money))")
                }
            }
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Daily tokens for the last 30 days, total \(Fmt.money(monthMoney))")
    }

    func total(_ d: DailyPoint) -> Int {
        let fams = (d.byFamily ?? [:]).values.reduce(0, +)
        return max(fams, d.tokens?.total ?? 0)
    }

    func dayLabel(_ day: String?) -> String {
        guard let day = day else { return "" }
        let inF = DateFormatter(); inF.dateFormat = "yyyy-MM-dd"; inF.locale = Locale(identifier: "en_US_POSIX")
        let outF = DateFormatter(); outF.dateFormat = "MMM d"
        return inF.date(from: day).map { outF.string(from: $0) } ?? day
    }
}

// MARK: - Recent threads

struct RecentCard: View {
    let threads: [ThreadSummary]
    var open: (String) -> Void
    var openAll: () -> Void

    var body: some View {
        Card(title: "Recent threads", icon: "list.bullet") {
            Button(action: openAll) {
                HStack(spacing: 2) {
                    Text("All")
                    Image(systemName: "chevron.right").font(.system(size: 7.5, weight: .bold))
                }
                .font(.system(size: 10.5, weight: .medium))
                .foregroundStyle(Theme.secondary)
            }
            .buttonStyle(.plain)
            .accessibilityLabel("Open all threads")
        } content: {
            if threads.isEmpty {
                Text("No threads yet. Run a scan after using Claude Code or Codex.")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.tertiary)
            } else {
                VStack(spacing: 1) {
                    ForEach(threads) { t in
                        ThreadRow(thread: t) { open(t.id) }
                    }
                }
                .padding(.horizontal, -6)
            }
        }
    }
}

struct ThreadRow: View {
    let thread: ThreadSummary
    var action: () -> Void
    @State private var hover = false

    var subtitle: String {
        var bits: [String] = []
        if let p = thread.projectName, thread.hasTitle { bits.append(p) }
        let models = (thread.models ?? []).prefix(2).map { Fmt.modelName($0) }
        if !models.isEmpty { bits.append(models.joined(separator: ", ") + ((thread.models?.count ?? 0) > 2 ? " +\(thread.models!.count - 2)" : "")) }
        bits.append(Fmt.ago(thread.ended ?? thread.started))
        return bits.joined(separator: " · ")
    }

    var body: some View {
        Button(action: action) {
            HStack(alignment: .center, spacing: 8) {
                HStack(spacing: 2) {
                    ForEach((thread.families ?? []).prefix(2), id: \.self) { f in
                        Capsule().fill(Theme.family(f)).frame(width: 3, height: 22)
                    }
                }
                .frame(width: 8, alignment: .leading)
                VStack(alignment: .leading, spacing: 1) {
                    Text(thread.displayTitle)
                        .font(.system(size: 11, weight: .medium))
                        .foregroundStyle(Theme.primary)
                        .lineLimit(1)
                        .truncationMode(.tail)
                    Text(subtitle)
                        .font(.system(size: 10))
                        .foregroundStyle(Theme.tertiary)
                        .lineLimit(1)
                }
                Spacer(minLength: 6)
                if let f = thread.flagCount, f > 0 {
                    HStack(spacing: 2) {
                        Image(systemName: "exclamationmark.triangle.fill").font(.system(size: 8.5))
                        Text("\(f)").font(.system(size: 10, weight: .semibold).monospacedDigit())
                    }
                    .foregroundStyle(Theme.warn)
                    .help("\(f) flag\(f == 1 ? "" : "s")")
                }
                Text(Fmt.money(thread.money))
                    .font(.system(size: 11, weight: .semibold).monospacedDigit())
                    .foregroundStyle(Theme.primary)
            }
            .padding(.horizontal, 6)
            .padding(.vertical, 5)
            .background(RoundedRectangle(cornerRadius: 7, style: .continuous).fill(Color.white.opacity(hover ? 0.06 : 0)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("\(thread.displayTitle), \(subtitle), \(Fmt.money(thread.money))")
        .accessibilityHint("Opens the thread in the full view")
        .accessibilityAddTraits(.isButton)
    }
}

/// Small "30 days ⌄" control that pops a native menu. SwiftUI's Menu ignores custom
/// label styling in a borderless panel, so this drives NSMenu directly.
struct PeriodButton: View {
    @Binding var selection: SpendPeriod
    @State private var hover = false

    var body: some View {
        Button(action: popUp) {
            HStack(spacing: 3) {
                Text(selection.label)
                Image(systemName: "chevron.down").font(.system(size: 7.5, weight: .bold))
            }
            .font(.system(size: 10.5, weight: .medium))
            .foregroundStyle(hover ? Theme.primary : Theme.secondary)
            .padding(.horizontal, 5)
            .padding(.vertical, 2)
            .background(RoundedRectangle(cornerRadius: 5).fill(Color.white.opacity(hover ? 0.08 : 0)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .accessibilityLabel("Spending period, \(selection.label)")
        .accessibilityHint("Choose today, 7 days, or 30 days")
    }

    private func popUp() {
        let menu = NSMenu()
        for p in SpendPeriod.allCases {
            let item = NSMenuItem(title: p.label, action: nil, keyEquivalent: "")
            item.state = p == selection ? .on : .off
            item.representedObject = p.rawValue
            menu.addItem(item)
        }
        let target = MenuTarget { raw in
            if let p = SpendPeriod(rawValue: raw) { selection = p }
        }
        for item in menu.items { item.target = target; item.action = #selector(MenuTarget.pick(_:)) }
        menu.appearance = NSAppearance(named: .darkAqua)
        objc_setAssociatedObject(menu, &MenuTarget.key, target, .OBJC_ASSOCIATION_RETAIN)
        menu.popUp(positioning: menu.items.first { $0.state == .on }, at: NSEvent.mouseLocation, in: nil)
    }
}

final class MenuTarget: NSObject {
    static var key = 0
    let onPick: (String) -> Void
    init(_ onPick: @escaping (String) -> Void) { self.onPick = onPick }
    @objc func pick(_ sender: NSMenuItem) { onPick(sender.representedObject as? String ?? "") }
}
