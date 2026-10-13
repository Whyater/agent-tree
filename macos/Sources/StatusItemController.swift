import AppKit
import Combine

/// Optional menu bar icon. A click drops the notch panel down; it never owns a panel itself.
@MainActor
final class StatusItemController: NSObject {
    private let store: Store
    private let prefs = Preferences.shared
    private var item: NSStatusItem?
    private var cancellables = Set<AnyCancellable>()
    var onClick: () -> Void = {}

    init(store: Store) {
        self.store = store
        super.init()
    }

    var isInstalled: Bool { item != nil }

    func install() {
        guard item == nil else { return }
        let it = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        it.button?.image = TreeGlyph.menuBarImage()
        it.button?.imagePosition = .imageLeading
        it.button?.target = self
        it.button?.action = #selector(clicked(_:))
        it.button?.setAccessibilityLabel("Agent Tree")
        item = it
        updateTitle()
        store.$limits.receive(on: RunLoop.main).sink { [weak self] _ in
            DispatchQueue.main.async { self?.updateTitle() }
        }.store(in: &cancellables)
        prefs.$showPercentInMenuBar.receive(on: RunLoop.main).sink { [weak self] _ in
            DispatchQueue.main.async { self?.updateTitle() }
        }.store(in: &cancellables)
    }

    func uninstall() {
        cancellables.removeAll()
        if let it = item { NSStatusBar.system.removeStatusItem(it) }
        item = nil
    }

    private func updateTitle() {
        guard let button = item?.button else { return }
        if prefs.showPercentInMenuBar, let p = store.highestPercent {
            let font = NSFont.monospacedDigitSystemFont(ofSize: 12, weight: .medium)
            button.attributedTitle = NSAttributedString(string: " " + Fmt.percent(p), attributes: [.font: font, .baselineOffset: 0.5])
            button.toolTip = "Agent Tree: highest plan limit \(Fmt.percent(p)) used"
        } else {
            button.title = ""
            button.toolTip = "Agent Tree"
        }
    }

    @objc private func clicked(_ sender: Any?) { onClick() }

    /// With a menu set, a click shows the menu instead of calling onClick.
    func setMenu(_ menu: NSMenu?) {
        item?.menu = menu
    }
}
