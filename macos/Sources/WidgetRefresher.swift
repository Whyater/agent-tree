import Foundation
import WidgetKit

/// Tells macOS to redraw the desktop widget, but only when the working agents change and
/// at most once a minute. Token and cost totals are left to the widget's own slow refresh,
/// so a busy agent doesn't wake the widget on every scan.
@MainActor
final class WidgetRefresher {
    static let minimumGap: TimeInterval = 60
    private var lastSignature: String?
    private var lastReload = Date.distantPast
    private var pending: DispatchWorkItem?

    /// `signature` names what the widget shows as working; nothing happens while it stays the same.
    func update(_ signature: String) {
        guard signature != lastSignature else { return }
        let first = lastSignature == nil
        lastSignature = signature
        // On launch the widget already read the file itself.
        if first { return }
        pending?.cancel()
        let work = DispatchWorkItem { [weak self] in self?.reloadIfInstalled() }
        pending = work
        let wait = max(0, Self.minimumGap - Date().timeIntervalSince(lastReload))
        DispatchQueue.main.asyncAfter(deadline: .now() + wait, execute: work)
    }

    private func reloadIfInstalled() {
        lastReload = Date()
        // Skip the reload entirely for people who never added the widget.
        WidgetCenter.shared.getCurrentConfigurations { result in
            guard case .success(let widgets) = result, !widgets.isEmpty else { return }
            WidgetCenter.shared.reloadTimelines(ofKind: "AgentTreeWidget")
        }
    }
}
