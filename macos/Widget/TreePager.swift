import AppIntents
import Foundation

/// Which page of the tree a widget shows. Widgets can't scroll, so the large sizes page
/// through the tree with two buttons. Each button runs PageTreeIntent in the widget
/// process, which stores the new page here; WidgetKit then redraws the widget.
/// Pages are kept per page size and per thread, so a new tree starts fresh.
enum TreePager {
    private static var defaults: UserDefaults { .standard }

    static func pageCount(_ total: Int, rows: Int) -> Int {
        rows > 0 ? max(1, (total + rows - 1) / rows) : 1
    }

    /// The page picked with the buttons for this tree, or else the page with the first
    /// working agent, so a live tree opens where the work is.
    static func page(for tree: LiveSnapshot.Tree, rows: Int, live: Bool) -> Int {
        let last = pageCount(tree.nodes.count, rows: rows) - 1
        if defaults.string(forKey: key("thread", rows)) == tree.threadId {
            return min(max(defaults.integer(forKey: key("page", rows)), 0), last)
        }
        guard live, rows > 0, let i = tree.nodes.firstIndex(where: \.running) else { return 0 }
        return min(i / rows, last)
    }

    static func move(by step: Int, rows: Int) {
        guard rows > 0, let s = LiveSnapshot.load(), let t = s.tree else { return }
        let current = page(for: t, rows: rows, live: t.live && s.isFresh(at: Date()))
        let next = min(max(current + step, 0), pageCount(t.nodes.count, rows: rows) - 1)
        defaults.set(t.threadId, forKey: key("thread", rows))
        defaults.set(next, forKey: key("page", rows))
    }

    private static func key(_ name: String, _ rows: Int) -> String { "tree.\(name).\(rows)" }
}

struct PageTreeIntent: AppIntent {
    static var title: LocalizedStringResource = "Page Through the Tree"
    static var isDiscoverable = false

    @Parameter(title: "Step") var step: Int
    @Parameter(title: "Rows") var rows: Int

    init() {}

    init(step: Int, rows: Int) {
        self.step = step
        self.rows = rows
    }

    func perform() async throws -> some IntentResult {
        TreePager.move(by: step, rows: rows)
        return .result()
    }
}
