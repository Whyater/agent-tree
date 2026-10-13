import AppKit
import SwiftUI
import Combine

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private let prefs = Preferences.shared
    private let backend = Backend()
    private var store: Store!
    private let actions = PanelActions()
    private var statusItem: StatusItemController!
    private var notch: NotchPanelController!
    private let watcher = LogWatcher(debounce: 2)
    private var fullView: FullViewWindowController?
    private var settingsWindow: NSWindow?
    private var cancellables = Set<AnyCancellable>()
    private let env = ProcessInfo.processInfo.environment

    func applicationDidFinishLaunching(_ notification: Notification) {
        let fixtures: URL? = backend.isMock ? fixturesDir() : nil
        store = Store(api: APIClient(mockFixtures: fixtures))
        statusItem = StatusItemController(store: store)
        notch = NotchPanelController(store: store, actions: actions)
        statusItem.onClick = { [weak self] in self?.notch.toggleFromClick() }

        actions.openFullView = { [weak self] hash in self?.openFullView(hash: hash) }
        actions.openSettings = { [weak self] in self?.openSettings() }
        actions.restart = { [weak self] in self?.backend.restart() }
        actions.quit = { [weak self] in self?.quit() }

        backend.onChange = { [weak self] state in self?.backendChanged(state) }
        buildMainMenu()
        applyMode(initial: true, modeChanged: true)
        backend.start()
        store.startPolling()

        prefs.$mode.dropFirst().removeDuplicates().receive(on: RunLoop.main)
            .sink { [weak self] _ in DispatchQueue.main.async { self?.applyMode(initial: false, modeChanged: true) } }
            .store(in: &cancellables)
        for pub in [prefs.$showMenuBarIcon.dropFirst().eraseToAnyPublisher(),
                    prefs.$notchPanelInDesktop.dropFirst().eraseToAnyPublisher()] {
            pub.receive(on: RunLoop.main)
                .sink { [weak self] _ in DispatchQueue.main.async { self?.applyMode(initial: false, modeChanged: false) } }
                .store(in: &cancellables)
        }
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { [weak self] _ in
            Task { @MainActor in self?.notch.screensChanged() }
        }

        // Debug hooks for screenshots and testing.
        if env["AGENT_TREE_OPEN_PANEL"] == "1" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in
                self?.notch.open(byClick: true)
            }
        }
        if env["AGENT_TREE_OPEN_FULL"] == "1" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in self?.openFullView(hash: nil) }
        }
        if env["AGENT_TREE_OPEN_SETTINGS"] == "1" {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.8) { [weak self] in self?.openSettings() }
        }
    }

    func applicationWillTerminate(_ notification: Notification) { shutdown() }

    /// Stops polling, the file watcher and the server, and removes temp files. Safe to call twice.
    func shutdown() {
        store?.stopPolling()
        watcher.stop()
        backend.stopAndWait()
    }

    @objc func quitMenu(_ sender: Any?) { quit() }

    /// Quit even when a sheet is open (AppKit postpones terminate while one is attached).
    func quit() {
        for w in NSApp.windows { if let sheet = w.attachedSheet { w.endSheet(sheet) } }
        NSApp.terminate(nil)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        // Re-opening a running app (Finder, Spotlight, or Vorssaint's card opening us by
        // bundle id) always means "show me the full view".
        openFullView(hash: nil)
        return true
    }

    private func fixturesDir() -> URL? {
        if let p = env["AGENT_TREE_FIXTURES"] { return URL(fileURLWithPath: p) }
        return Bundle.main.resourceURL?.appendingPathComponent("Fixtures")
    }

    private func backendChanged(_ state: Backend.State) {
        switch state {
        case .idle, .starting: store.connection = .starting
        case .mock: store.connection = .mock
        case .ready(let url):
            store.api.baseURL = url
            store.connection = .live
            Task { await store.refresh() }
            // New log writes trigger a scan within a few seconds; the server's own
            // 10-minute rescan is only a safety net.
            watcher.onChange = { [weak self] in
                guard let self = self else { return }
                Task { await self.store.scan() }
            }
            watcher.start()
            if prefs.mode == .desktop, fullView?.window?.isVisible == true || fullView == nil {
                openFullView(hash: nil)
            }
        case .failed(let msg):
            watcher.stop()
            store.connection = .failed(msg)
        }
    }

    // MARK: modes

    private func applyMode(initial: Bool, modeChanged: Bool = true) {
        if prefs.showMenuBarIcon { statusItem.install() } else { statusItem.uninstall() }
        statusItem.setMenu(prefs.mode == .insideVorssaint ? backgroundMenu() : nil)
        notch.hoverEnabled = prefs.hoverPanelEnabled
        if !prefs.hoverPanelEnabled { notch.close() }
        guard initial || modeChanged else { return }
        switch prefs.mode {
        case .compact, .insideVorssaint:
            NSApp.setActivationPolicy(.accessory)
        case .desktop:
            NSApp.setActivationPolicy(.regular)
            if !initial || backend.isMock {
                openFullView(hash: nil)
            }
            NSApp.activate(ignoringOtherApps: true)
        }
        // Switching policy can hide our windows; bring settings back if it was open.
        if let s = settingsWindow, s.isVisible {
            DispatchQueue.main.async { s.makeKeyAndOrderFront(nil); NSApp.activate(ignoringOtherApps: true) }
        }
    }

    // MARK: windows

    func openFullView(hash: String?) {
        notch.close()
        let fv = fullView ?? FullViewWindowController()
        fullView = fv
        fv.show(base: backend.webURL, hash: hash ?? "#/")
    }

    @objc func showFullViewMenu(_ sender: Any?) { openFullView(hash: nil) }

    @objc func reloadFullView(_ sender: Any?) { fullView?.reload() }

    @objc func openSettings(_ sender: Any? = nil) {
        notch.close()
        if settingsWindow == nil {
            let view = SettingsView(prefs: prefs, store: store, backend: backend, openInBrowser: { [weak self] in
                if let u = self?.backend.webURL { NSWorkspace.shared.open(u) }
            })
            let host = NSHostingController(rootView: view)
            let w = NSWindow(contentViewController: host)
            w.title = "Agent Tree Settings"
            w.styleMask = [.titled, .closable]
            w.isReleasedWhenClosed = false
            w.center()
            w.setFrameAutosaveName("AgentTreeSettings")
            settingsWindow = w
        }
        settingsWindow?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        Task { await store.loadChecks() }
    }

    /// Menu for the icon in "Inside Vorssaint" mode, where there is no panel to drop.
    private func backgroundMenu() -> NSMenu {
        let m = NSMenu()
        let full = NSMenuItem(title: "Open Full View", action: #selector(showFullViewMenu(_:)), keyEquivalent: "")
        full.target = self
        m.addItem(full)
        let scan = NSMenuItem(title: "Scan Now", action: #selector(scanNow(_:)), keyEquivalent: "")
        scan.target = self
        m.addItem(scan)
        m.addItem(.separator())
        let settings = NSMenuItem(title: "Settings…", action: #selector(openSettings(_:)), keyEquivalent: ",")
        settings.target = self
        m.addItem(settings)
        m.addItem(.separator())
        let quitItem = NSMenuItem(title: "Quit Agent Tree", action: #selector(quitMenu(_:)), keyEquivalent: "q")
        quitItem.target = self
        m.addItem(quitItem)
        return m
    }

    @objc func scanNow(_ sender: Any?) { Task { await store.scan(manual: true) } }

    private func buildMainMenu() {
        let main = NSMenu()

        let appItem = NSMenuItem()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "About Agent Tree", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        let settings = NSMenuItem(title: "Settings…", action: #selector(openSettings(_:)), keyEquivalent: ",")
        settings.target = self
        appMenu.addItem(settings)
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide Agent Tree", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        let hideOthers = NSMenuItem(title: "Hide Others", action: #selector(NSApplication.hideOtherApplications(_:)), keyEquivalent: "h")
        hideOthers.keyEquivalentModifierMask = [.command, .option]
        appMenu.addItem(hideOthers)
        appMenu.addItem(withTitle: "Show All", action: #selector(NSApplication.unhideAllApplications(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        let quitItem = NSMenuItem(title: "Quit Agent Tree", action: #selector(quitMenu(_:)), keyEquivalent: "q")
        quitItem.target = self
        appMenu.addItem(quitItem)
        appItem.submenu = appMenu
        main.addItem(appItem)

        let editItem = NSMenuItem()
        let edit = NSMenu(title: "Edit")
        edit.addItem(withTitle: "Undo", action: Selector(("undo:")), keyEquivalent: "z")
        edit.addItem(withTitle: "Redo", action: Selector(("redo:")), keyEquivalent: "Z")
        edit.addItem(.separator())
        edit.addItem(withTitle: "Cut", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        edit.addItem(withTitle: "Copy", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        edit.addItem(withTitle: "Paste", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        edit.addItem(withTitle: "Select All", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        editItem.submenu = edit
        main.addItem(editItem)

        let viewItem = NSMenuItem()
        let view = NSMenu(title: "View")
        let dash = NSMenuItem(title: "Show Dashboard", action: #selector(showFullViewMenu(_:)), keyEquivalent: "0")
        dash.target = self
        view.addItem(dash)
        let reload = NSMenuItem(title: "Reload", action: #selector(reloadFullView(_:)), keyEquivalent: "r")
        reload.target = self
        view.addItem(reload)
        let scan = NSMenuItem(title: "Scan Now", action: #selector(scanNow(_:)), keyEquivalent: "r")
        scan.keyEquivalentModifierMask = [.command, .shift]
        scan.target = self
        view.addItem(scan)
        viewItem.submenu = view
        main.addItem(viewItem)

        let windowItem = NSMenuItem()
        let window = NSMenu(title: "Window")
        window.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        window.addItem(withTitle: "Zoom", action: #selector(NSWindow.performZoom(_:)), keyEquivalent: "")
        window.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        windowItem.submenu = window
        main.addItem(windowItem)
        NSApp.windowsMenu = window

        NSApp.mainMenu = main
    }
}
