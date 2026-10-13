import SwiftUI

/// One visible row of the compact tree, with the connector pattern precomputed.
struct TreeRowModel: Identifiable {
    let node: TreeNode
    let depth: Int            // 0 = root
    let guides: [Bool]        // for ancestor columns 1..<depth: draw a vertical line
    let isLast: Bool          // last sibling: └ instead of ├
    var id: String { node.id }
}

enum TreeLayout {
    /// Pick at most `limit` nodes: running ones first, then the most recently started,
    /// always pulling in their ancestors so the shape stays a real tree. Rows keep the
    /// API's depth-first order.
    static func rows(_ nodes: [TreeNode], limit: Int = 8) -> (rows: [TreeRowModel], hidden: Int) {
        guard !nodes.isEmpty else { return ([], 0) }
        let byId = Dictionary(nodes.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        var chosen = Set<String>()
        func addWithAncestors(_ n: TreeNode) -> Bool {
            var chain: [String] = []
            var cur: TreeNode? = n
            while let c = cur, !chosen.contains(c.id) {
                chain.append(c.id)
                cur = c.parentId.flatMap { byId[$0] }
            }
            if chosen.count + chain.count > limit { return false }
            chain.forEach { chosen.insert($0) }
            return true
        }
        _ = addWithAncestors(nodes[0])
        let priority = nodes.dropFirst().sorted { a, b in
            if a.isRunning != b.isRunning { return a.isRunning }
            return (a.started ?? .distantPast) > (b.started ?? .distantPast)
        }
        for n in priority where chosen.count < limit { _ = addWithAncestors(n) }

        let visible = nodes.filter { chosen.contains($0.id) }
        var depthOf: [String: Int] = [:]
        var lastOf: [String: Bool] = [:]
        for (i, n) in visible.enumerated() {
            depthOf[n.id] = n.parentId.flatMap { depthOf[$0] }.map { $0 + 1 } ?? 0
            lastOf[n.id] = !visible[(i + 1)...].contains { $0.parentId == n.parentId }
        }
        let rows = visible.map { n -> TreeRowModel in
            let d = depthOf[n.id] ?? 0
            // Ancestors from depth 1 up to depth d-1.
            var chain: [String] = []
            var p = n.parentId
            while let pid = p, let pn = byId[pid], chosen.contains(pid) { chain.insert(pid, at: 0); p = pn.parentId }
            let guides = chain.dropFirst().map { !(lastOf[$0] ?? true) }
            return TreeRowModel(node: n, depth: d, guides: Array(guides.prefix(max(d - 1, 0))), isLast: lastOf[n.id] ?? true)
        }
        return (rows, nodes.count - visible.count)
    }
}

struct TreeCard: View {
    let detail: ThreadDetail?
    var open: (String) -> Void
    @State private var hover = false
    @State private var pulse = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let indent: CGFloat = 11

    var body: some View {
        let layout = TreeLayout.rows(detail?.nodes ?? [])
        let live = detail?.nodes.contains { $0.isRunning } ?? false
        Card(title: "Tree", icon: "point.3.connected.trianglepath.dotted", iconColor: live ? Theme.anthropic : Theme.secondary) {
            HStack(spacing: 5) {
                if let d = detail {
                    Text(summaryLine(d))
                        .font(.system(size: 10).monospacedDigit())
                        .foregroundStyle(Theme.secondary)
                }
                if live {
                    Text("Live")
                        .font(.system(size: 10, weight: .semibold))
                        .foregroundStyle(Theme.anthropic)
                }
            }
        } content: {
            if let d = detail, !layout.rows.isEmpty {
                Button { open(d.thread.id) } label: {
                    VStack(alignment: .leading, spacing: 0) {
                        ForEach(layout.rows) { r in
                            row(r, thread: d.thread)
                        }
                        if layout.hidden > 0 {
                            HStack(spacing: 3) {
                                Text("+\(layout.hidden) more agent\(layout.hidden == 1 ? "" : "s")")
                                Image(systemName: "chevron.right").font(.system(size: 7, weight: .bold))
                            }
                            .font(.system(size: 10))
                            .foregroundStyle(Theme.tertiary)
                            .padding(.top, 3)
                            .padding(.leading, TreeCard.indent + 2)
                        }
                    }
                    .padding(.horizontal, 6)
                    .padding(.vertical, 4)
                    .background(RoundedRectangle(cornerRadius: 8, style: .continuous).fill(Color.white.opacity(hover ? 0.045 : 0)))
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .padding(.horizontal, -6)
                .onHover { hover = $0 }
                .help("Open this tree in the full view")
                .accessibilityLabel("Agent tree for \(d.thread.displayTitle), \(summaryLine(d)). Opens the full view.")
            } else {
                Text("No agent trees yet. They appear after the first scan.")
                    .font(.system(size: 11))
                    .foregroundStyle(Theme.tertiary)
            }
        }
        .onAppear {
            guard !reduceMotion else { return }
            withAnimation(.easeInOut(duration: 1.0).repeatForever(autoreverses: true)) { pulse = true }
        }
    }

    func summaryLine(_ d: ThreadDetail) -> String {
        let n = d.thread.nodeCount ?? d.nodes.count
        var s = "\(n) agent\(n == 1 ? "" : "s")"
        if let depth = d.thread.maxDepth, depth > 0 { s += " · depth \(depth)" }
        return s
    }

    func label(_ n: TreeNode, thread: ThreadSummary) -> String {
        if n.parentId == nil, thread.hasTitle { return thread.displayTitle }
        for c in [n.description, n.agentType, n.role] { if let c = c, !c.isEmpty { return c } }
        switch n.source {
        case "claude-code": return "Claude Code session"
        case "codex": return "Codex session"
        case "t3": return "T3 Code thread"
        default: return (n.kind ?? "agent").capitalized
        }
    }

    func row(_ r: TreeRowModel, thread: ThreadSummary) -> some View {
        let n = r.node
        let color = Theme.family(n.family)
        // The top row carries the whole tree's total; every other row its own usage.
        let totals = r.depth == 0 ? (n.subtree ?? n.selfTotals) : n.selfTotals
        let tokens = totals?.tokens?.total ?? 0
        return HStack(spacing: 6) {
            Connector(depth: r.depth, guides: r.guides, isLast: r.isLast, indent: TreeCard.indent)
                .frame(width: CGFloat(r.depth) * TreeCard.indent, height: 19)
            statusDot(n, color: color)
            Text(label(n, thread: thread))
                .font(.system(size: 10.5, weight: r.depth == 0 ? .semibold : .regular))
                .foregroundStyle(r.depth == 0 ? Theme.primary : Theme.primary.opacity(0.82))
                .lineLimit(1)
                .truncationMode(.tail)
            Spacer(minLength: 6)
            if n.model != nil {
                Text(Fmt.modelName(n.model))
                    .font(.system(size: 9, weight: .semibold))
                    .foregroundStyle(color)
                    .padding(.horizontal, 5)
                    .padding(.vertical, 1.5)
                    .background(Capsule().fill(color.opacity(0.15)))
                    .lineLimit(1)
                    .fixedSize()
            }
            Text(tokens > 0 ? Fmt.tokens(tokens) : "–")
                .font(.system(size: 10).monospacedDigit())
                .foregroundStyle(Theme.tertiary)
                .frame(width: 34, alignment: .trailing)
            Text(Fmt.money(totals?.money))
                .font(.system(size: 10.5, weight: .medium).monospacedDigit())
                .foregroundStyle(Theme.secondary)
                .frame(width: 44, alignment: .trailing)
        }
        .frame(height: 19)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Depth \(r.depth), \(label(n, thread: thread)), \(Fmt.modelName(n.model)), \(n.status ?? "unknown"), \(Fmt.tokens(tokens)) tokens, \(Fmt.money(totals?.money))")
    }

    @ViewBuilder func statusDot(_ n: TreeNode, color: Color) -> some View {
        switch n.status {
        case "running":
            ZStack {
                Circle().fill(color.opacity(0.3)).frame(width: 9, height: 9).scaleEffect(pulse ? 1.25 : 0.8).opacity(pulse ? 0.2 : 0.7)
                Circle().fill(color).frame(width: 5.5, height: 5.5)
            }
            .frame(width: 9, height: 9)
            .help("Running")
        case "failed":
            Circle().fill(Color(red: 0.95, green: 0.42, blue: 0.40)).frame(width: 5.5, height: 5.5).frame(width: 9).help("Failed")
        default:
            Circle().strokeBorder(Theme.tertiary, lineWidth: 1.2).frame(width: 6, height: 6).frame(width: 9).help("Done")
        }
    }
}

/// Thin ├ └ │ connector lines for one row.
struct Connector: View {
    let depth: Int
    let guides: [Bool]
    let isLast: Bool
    let indent: CGFloat

    var body: some View {
        Canvas { ctx, size in
            guard depth > 0 else { return }
            let mid = size.height / 2
            let color = GraphicsContext.Shading.color(Color.white.opacity(0.18))
            var p = Path()
            for (i, g) in guides.enumerated() where g {
                let x = CGFloat(i) * indent + indent / 2
                p.move(to: CGPoint(x: x, y: 0)); p.addLine(to: CGPoint(x: x, y: size.height))
            }
            let x = CGFloat(depth - 1) * indent + indent / 2
            p.move(to: CGPoint(x: x, y: 0))
            p.addLine(to: CGPoint(x: x, y: isLast ? mid - 3 : size.height))
            if isLast {
                p.addQuadCurve(to: CGPoint(x: x + 3, y: mid), control: CGPoint(x: x, y: mid))
            } else {
                p.move(to: CGPoint(x: x, y: mid))
            }
            p.addLine(to: CGPoint(x: CGFloat(depth) * indent, y: mid))
            ctx.stroke(p, with: color, style: StrokeStyle(lineWidth: 1, lineCap: .round))
        }
        .accessibilityHidden(true)
    }
}
