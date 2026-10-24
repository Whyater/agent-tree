import AppIntents
import SwiftUI
import WidgetKit

/// Desktop and Notification Center widget. It never polls: macOS asks for a timeline
/// when the widget is added, when the app reports that the running agents changed, and
/// otherwise at the refresh date below. The widget process only runs to read
/// ~/.agent-tree/live.json (a few KB) and lay out one snapshot.
@main
struct AgentTreeWidgets: WidgetBundle {
    var body: some Widget { AgentTreeWidget() }
}

struct AgentTreeWidget: Widget {
    static let kind = "AgentTreeWidget"

    var body: some WidgetConfiguration {
        StaticConfiguration(kind: Self.kind, provider: LiveProvider()) { entry in
            AgentTreeWidgetView(entry: entry)
                .containerBackground(.fill.tertiary, for: .widget)
        }
        .configurationDisplayName("Agent Tree")
        .description("Working agents, today's spend and the live tree.")
        .supportedFamilies([.systemSmall, .systemMedium, .systemLarge, .systemExtraLarge])
    }
}

struct LiveEntry: TimelineEntry {
    let date: Date
    let snapshot: LiveSnapshot?
    /// Which page of the tree to show (large sizes only).
    var treePage = 0

    /// Running parts, or none once the snapshot is too old to vouch for them.
    var running: [LiveSnapshot.Running] {
        guard let s = snapshot, s.isFresh(at: date) else { return [] }
        return s.running
    }

    var isLive: Bool { !running.isEmpty }
}

struct LiveProvider: TimelineProvider {
    func placeholder(in context: Context) -> LiveEntry { LiveEntry(date: Date(), snapshot: nil) }

    func getSnapshot(in context: Context, completion: @escaping (LiveEntry) -> Void) {
        completion(Self.entry(at: Date(), snapshot: LiveSnapshot.load(), family: context.family))
    }

    func getTimeline(in context: Context, completion: @escaping (Timeline<LiveEntry>) -> Void) {
        let now = Date()
        let snapshot = LiveSnapshot.load()
        var entries = [Self.entry(at: now, snapshot: snapshot, family: context.family)]
        // If Agent Tree quits, the same file stops vouching for running agents at this
        // moment, so a second entry shows that without waking the widget again.
        if let g = snapshot?.generated, snapshot?.isFresh(at: now) == true {
            entries.append(Self.entry(at: g.addingTimeInterval(LiveSnapshot.freshFor), snapshot: snapshot, family: context.family))
        }
        // Token and cost totals drift while agents work; the app only asks for a reload
        // when the set of running agents changes, so look again now and then.
        let busy = entries[0].isLive
        let next = now.addingTimeInterval(busy ? 10 * 60 : 60 * 60)
        completion(Timeline(entries: entries, policy: .after(next)))
    }

    static func entry(at date: Date, snapshot: LiveSnapshot?, family: WidgetFamily) -> LiveEntry {
        var e = LiveEntry(date: date, snapshot: snapshot)
        if let t = snapshot?.tree, let rows = TreeSection.rows(for: family) {
            e.treePage = TreePager.page(for: t, rows: rows, live: t.live && e.isLive)
        }
        return e
    }
}

// MARK: views

struct AgentTreeWidgetView: View {
    let entry: LiveEntry
    @Environment(\.widgetFamily) private var family

    var body: some View {
        if entry.snapshot == nil {
            EmptyStateView()
        } else {
            switch family {
            case .systemSmall: SmallView(entry: entry)
            case .systemMedium: MediumView(entry: entry)
            case .systemExtraLarge: ExtraLargeView(entry: entry)
            default: LargeView(entry: entry)
            }
        }
    }
}

private enum Palette {
    static let anthropic = Color(red: 217 / 255, green: 120 / 255, blue: 87 / 255)   // #D97857
    static let openai = Color(red: 125 / 255, green: 153 / 255, blue: 255 / 255)     // #7D99FF

    static func family(_ f: String?) -> Color {
        switch f {
        case "anthropic": return anthropic
        case "openai": return openai
        default: return .secondary
        }
    }
}

/// The app's mark (main agent, subagent, its subagent) in colours that work on any
/// desktop, and that the system can tint when the widget is shown monochrome.
private struct Mark: View {
    var size: CGFloat = 14

    var body: some View {
        Canvas { ctx, sz in
            let s = sz.width / 20
            ctx.stroke(Path(TreeGlyph.links(s)), with: .style(.secondary),
                       style: StrokeStyle(lineWidth: TreeGlyph.linkWidth * s, lineCap: .round))
            ctx.fill(Path(roundedRect: TreeGlyph.scaled(TreeGlyph.root, s), cornerRadius: TreeGlyph.rootRadius * s), with: .style(.primary))
            ctx.fill(Path(roundedRect: TreeGlyph.scaled(TreeGlyph.child, s), cornerRadius: TreeGlyph.child.height / 2 * s), with: .color(Palette.anthropic))
            ctx.fill(Path(roundedRect: TreeGlyph.scaled(TreeGlyph.grandchild, s), cornerRadius: TreeGlyph.grandchild.height / 2 * s), with: .color(Palette.openai))
        }
        .frame(width: size, height: size)
        .widgetAccentable()
        .accessibilityHidden(true)
    }
}

private struct Header: View {
    let entry: LiveEntry
    /// Small and medium widgets have no room for the badge next to the name.
    var compact = false

    var body: some View {
        HStack(spacing: 6) {
            Mark()
            Text("Agent Tree")
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(.secondary)
                .lineLimit(1)
            Spacer(minLength: 4)
            if entry.isLive {
                if compact {
                    Circle().fill(Palette.anthropic).frame(width: 7, height: 7)
                        .widgetAccentable()
                        .accessibilityLabel("Agents working")
                } else {
                    LiveBadge()
                }
            }
        }
    }
}

private struct LiveBadge: View {
    var body: some View {
        HStack(spacing: 4) {
            Circle().fill(Palette.anthropic).frame(width: 6, height: 6).widgetAccentable()
            Text("Live").font(.system(size: 10, weight: .semibold))
        }
        .padding(.horizontal, 6)
        .padding(.vertical, 2)
        .background(Capsule().fill(Palette.anthropic.opacity(0.16)))
        .foregroundStyle(Palette.anthropic)
        .accessibilityLabel("Agents working")
    }
}

/// "3 working" or "Idle", with when the numbers were taken.
private struct WorkingCount: View {
    let entry: LiveEntry
    var size: CGFloat = 34

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            if entry.isLive {
                Text("\(entry.running.count)")
                    .font(.system(size: size, weight: .semibold, design: .rounded).monospacedDigit())
                    .contentTransition(.numericText())
                Text(entry.running.count == 1 ? "thread working" : "threads working")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
            } else {
                Text("Idle")
                    .font(.system(size: size * 0.7, weight: .semibold, design: .rounded))
                Updated(entry: entry)
            }
        }
    }
}

private struct Updated: View {
    let entry: LiveEntry

    var body: some View {
        if let g = entry.snapshot?.generated {
            // A fixed time rather than a ticking "ago", so the widget isn't redrawn every second.
            (Text("Updated ") + Text(g, format: .dateTime.hour().minute()))
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }
}

private struct Spend: View {
    let label: String
    let period: LiveSnapshot.Period?

    var body: some View {
        VStack(alignment: .leading, spacing: 1) {
            Text(label)
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(.secondary)
            Text(WidgetFormat.dollars(period?.cost))
                .font(.system(size: 13, weight: .semibold).monospacedDigit())
                .lineLimit(1)
                .minimumScaleFactor(0.8)
            Text("\(WidgetFormat.tokens(period?.tokens ?? 0)) tokens")
                .font(.system(size: 10).monospacedDigit())
                .foregroundStyle(.tertiary)
                .lineLimit(1)
        }
    }
}

private struct SmallView: View {
    let entry: LiveEntry

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            Header(entry: entry, compact: true)
            Spacer(minLength: 6)
            WorkingCount(entry: entry)
            Spacer(minLength: 6)
            Spend(label: "Today", period: entry.snapshot?.totals["today"])
        }
    }
}

private struct ThreadRow: View {
    let run: LiveSnapshot.Running

    var body: some View {
        HStack(spacing: 7) {
            Circle().fill(Palette.family(run.family)).frame(width: 7, height: 7).widgetAccentable()
            VStack(alignment: .leading, spacing: 0) {
                Text(run.title ?? run.projectName ?? "Untitled thread")
                    .font(.system(size: 12, weight: .medium))
                    .lineLimit(1)
                Text(detail)
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 4)
            if let since = LiveSnapshot.parseDate(run.activeSince) {
                Text(since, format: .dateTime.hour(.defaultDigits(amPM: .omitted)).minute())
                    .font(.system(size: 10).monospacedDigit())
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .fixedSize()
            }
        }
    }

    private var detail: String {
        var parts: [String] = []
        if run.title != nil, let p = run.projectName { parts.append(p) }
        if let m = run.model { parts.append(m) }
        if let n = run.agentsRunning, n > 0 { parts.append(n == 1 ? "1 agent" : "\(n) agents") }
        return parts.joined(separator: " · ")
    }
}

private struct MediumView: View {
    let entry: LiveEntry

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            VStack(alignment: .leading, spacing: 0) {
                Header(entry: entry, compact: true)
                Spacer(minLength: 6)
                WorkingCount(entry: entry, size: 30)
                Spacer(minLength: 6)
                Spend(label: "Today", period: entry.snapshot?.totals["today"])
            }
            .frame(maxWidth: 120, alignment: .leading)

            VStack(alignment: .leading, spacing: 7) {
                if entry.isLive {
                    ForEach(entry.running.prefix(3), id: \.threadId) { ThreadRow(run: $0) }
                    if entry.running.count > 3 {
                        Text("+\(entry.running.count - 3) more")
                            .font(.system(size: 10))
                            .foregroundStyle(.tertiary)
                    }
                } else if let t = entry.snapshot?.tree {
                    LastThread(tree: t)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

private struct LastThread: View {
    let tree: LiveSnapshot.Tree

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("Last thread")
                .font(.system(size: 10, weight: .medium))
                .foregroundStyle(.secondary)
            Text(tree.title ?? tree.projectName ?? "Untitled thread")
                .font(.system(size: 12, weight: .medium))
                .lineLimit(2)
            Text(summary)
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
    }

    private var summary: String {
        var parts: [String] = []
        if tree.title != nil, let p = tree.projectName { parts.append(p) }
        let n = tree.nodeCount ?? tree.nodes.count
        parts.append(n == 1 ? "1 agent" : "\(n) agents")
        return parts.joined(separator: " · ")
    }
}

/// One row of the tree: indented by depth, a filled dot while it works.
private struct NodeRow: View {
    let node: LiveSnapshot.Node
    let working: Bool

    var body: some View {
        HStack(spacing: 6) {
            Color.clear.frame(width: CGFloat(min(node.depth, 6)) * 12, height: 1)
            Group {
                if working {
                    Circle().fill(Palette.family(node.family))
                } else {
                    Circle().strokeBorder(Palette.family(node.family), lineWidth: 1.5)
                }
            }
            .frame(width: 7, height: 7)
            .widgetAccentable()
            Text(node.label ?? "Agent")
                .font(.system(size: 11, weight: node.depth == 0 ? .semibold : .regular))
                .foregroundStyle(working || node.depth == 0 ? .primary : .secondary)
                .lineLimit(1)
            Spacer(minLength: 4)
            if let t = node.tokens, t > 0 {
                Text(WidgetFormat.tokens(t))
                    .font(.system(size: 10).monospacedDigit())
                    .foregroundStyle(.tertiary)
            }
        }
    }
}

private struct LargeView: View {
    let entry: LiveEntry

    var body: some View {
        let s = entry.snapshot
        VStack(alignment: .leading, spacing: 10) {
            Header(entry: entry)
            HStack(alignment: .top) {
                WorkingCount(entry: entry, size: 28)
                Spacer(minLength: 8)
                Spend(label: "Today", period: s?.totals["today"])
                Spacer(minLength: 8)
                Spend(label: "7 days", period: s?.totals["week"])
            }
            Divider()
            if let t = s?.tree {
                TreeSection(entry: entry, tree: t, rows: TreeSection.largeRows)
            }
            Spacer(minLength: 0)
        }
    }
}

/// Twice as wide as large: the summary and working threads on the left, a taller page
/// of the tree on the right.
private struct ExtraLargeView: View {
    let entry: LiveEntry

    var body: some View {
        let s = entry.snapshot
        HStack(alignment: .top, spacing: 16) {
            VStack(alignment: .leading, spacing: 10) {
                Header(entry: entry)
                WorkingCount(entry: entry, size: 28)
                HStack(alignment: .top, spacing: 18) {
                    Spend(label: "Today", period: s?.totals["today"])
                    Spend(label: "7 days", period: s?.totals["week"])
                }
                Divider()
                if entry.isLive {
                    VStack(alignment: .leading, spacing: 7) {
                        ForEach(entry.running.prefix(4), id: \.threadId) { ThreadRow(run: $0) }
                        if entry.running.count > 4 {
                            Text("+\(entry.running.count - 4) more")
                                .font(.system(size: 10))
                                .foregroundStyle(.tertiary)
                        }
                    }
                } else if let t = s?.tree {
                    LastThread(tree: t)
                }
                Spacer(minLength: 0)
            }
            .frame(width: 250, alignment: .leading)

            Divider()

            VStack(alignment: .leading, spacing: 0) {
                if let t = s?.tree {
                    TreeSection(entry: entry, tree: t, rows: TreeSection.extraLargeRows)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }
}

/// One page of the tree in file (depth-first) order, with buttons to page through the
/// rest when it doesn't fit.
private struct TreeSection: View {
    let entry: LiveEntry
    let tree: LiveSnapshot.Tree
    let rows: Int

    static let largeRows = 9
    static let extraLargeRows = 15

    static func rows(for family: WidgetFamily) -> Int? {
        switch family {
        case .systemLarge: return largeRows
        case .systemExtraLarge: return extraLargeRows
        default: return nil
        }
    }

    var body: some View {
        let live = tree.live && entry.isLive
        let total = tree.nodes.count
        let pages = TreePager.pageCount(total, rows: rows)
        let page = min(entry.treePage, pages - 1)
        let shown = tree.nodes.dropFirst(page * rows).prefix(rows)
        VStack(alignment: .leading, spacing: 5) {
            HStack(spacing: 4) {
                Text(live ? "Live tree" : "Last tree")
                    .font(.system(size: 10, weight: .medium))
                    .foregroundStyle(.secondary)
                Text("· " + (tree.title ?? tree.projectName ?? "Untitled thread"))
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                Spacer(minLength: 4)
                if pages > 1 {
                    Pager(page: page, pages: pages, rows: rows, total: total)
                }
            }
            ForEach(Array(shown), id: \.id) { NodeRow(node: $0, working: live && $0.running) }
        }
    }
}

private struct Pager: View {
    let page: Int
    let pages: Int
    let rows: Int
    let total: Int

    var body: some View {
        HStack(spacing: 2) {
            Text("\(page * rows + 1)–\(min(total, (page + 1) * rows)) of \(total)")
                .font(.system(size: 10).monospacedDigit())
                .foregroundStyle(.tertiary)
                .lineLimit(1)
                .fixedSize()
                .padding(.trailing, 2)
            step(-1, "chevron.up", "Previous page", enabled: page > 0)
            step(1, "chevron.down", "Next page", enabled: page < pages - 1)
        }
    }

    private func step(_ delta: Int, _ symbol: String, _ label: String, enabled: Bool) -> some View {
        Button(intent: PageTreeIntent(step: delta, rows: rows)) {
            Image(systemName: symbol)
                .font(.system(size: 9, weight: .bold))
                .frame(width: 18, height: 16)
                .background(RoundedRectangle(cornerRadius: 4).fill(.quaternary.opacity(enabled ? 1 : 0.4)))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(enabled ? .secondary : .quaternary)
        .disabled(!enabled)
        .accessibilityLabel(label)
    }
}

private struct EmptyStateView: View {
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 6) {
                Mark()
                Text("Agent Tree")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
            Text("No data yet")
                .font(.system(size: 15, weight: .semibold))
            Text("Open Agent Tree once so it can index your agent logs.")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .leading)
    }
}
