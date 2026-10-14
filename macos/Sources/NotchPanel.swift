import AppKit
import SwiftUI

/// Black slab that hangs from the top edge of the screen: square top with small concave
/// "shoulders" where it meets the menu bar, rounded bottom corners.
struct NotchShape: Shape {
    var shoulder: CGFloat = 10
    var bottomRadius: CGFloat = 18

    func path(in rect: CGRect) -> Path {
        let s = min(shoulder, rect.width / 4)
        let r = min(bottomRadius, (rect.width - 2 * s) / 2, rect.height / 2)
        var p = Path()
        p.move(to: CGPoint(x: rect.minX, y: rect.minY))
        p.addQuadCurve(to: CGPoint(x: rect.minX + s, y: rect.minY + s), control: CGPoint(x: rect.minX + s, y: rect.minY))
        p.addLine(to: CGPoint(x: rect.minX + s, y: rect.maxY - r))
        p.addQuadCurve(to: CGPoint(x: rect.minX + s + r, y: rect.maxY), control: CGPoint(x: rect.minX + s, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.maxX - s - r, y: rect.maxY))
        p.addQuadCurve(to: CGPoint(x: rect.maxX - s, y: rect.maxY - r), control: CGPoint(x: rect.maxX - s, y: rect.maxY))
        p.addLine(to: CGPoint(x: rect.maxX - s, y: rect.minY + s))
        p.addQuadCurve(to: CGPoint(x: rect.maxX, y: rect.minY), control: CGPoint(x: rect.maxX - s, y: rect.minY))
        p.closeSubpath()
        return p
    }
}

final class NotchModel: ObservableObject {
    @Published var expanded = false
    @Published var topInset: CGFloat = 32
    @Published var bodyHeight: CGFloat = 500
    var collapsedWidth: CGFloat = 200
}

/// Panel content plus the grow-out-of-the-notch reveal.
struct NotchContainer: View {
    @ObservedObject var model: NotchModel
    @ObservedObject var store: Store
    @ObservedObject var actions: PanelActions
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    static let shoulder: CGFloat = 10

    var body: some View {
        PanelView(store: store, actions: actions, topInset: model.topInset, bodyHeight: model.bodyHeight)
            .padding(.horizontal, NotchContainer.shoulder)
            .background(Color.black)
            .opacity(model.expanded ? 1 : (reduceMotion ? 0 : 0.0001))
            .mask(alignment: .top) {
                GeometryReader { g in
                    let full = reduceMotion || model.expanded
                    NotchShape(shoulder: NotchContainer.shoulder, bottomRadius: full ? 24 : 12)
                        .frame(width: full ? g.size.width : model.collapsedWidth + 2 * NotchContainer.shoulder,
                               height: full ? g.size.height : model.topInset)
                        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
                }
            }
            .environment(\.colorScheme, .dark)
    }
}

/// Borderless panel that can take clicks without activating the app.
final class NotchWindow: NSPanel {
    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
    override func cancelOperation(_ sender: Any?) { onEscape?() }
    var onEscape: (() -> Void)?
}

/// Watches an invisible strip along the top edge of each screen. Hovering it drops the
/// panel down from the menu bar; leaving the panel collapses it after a short grace delay.
/// Uses mouse-moved monitors (no Accessibility permission needed) instead of a window, so
/// the strip never blocks clicks on menu bar items underneath it.
@MainActor
final class NotchPanelController: NSObject, NSWindowDelegate {
    private let store: Store
    private let actions: PanelActions
    private let prefs = Preferences.shared
    private let model = NotchModel()
    private var window: NotchWindow?
    private var hosting: NSHostingView<NotchContainer>?
    private var monitors: [Any] = []
    private var pollTimer: Timer?
    private var lastPoint = NSPoint(x: -1, y: -1)
    private var dwellTimer: Timer?
    private var collapseTimer: Timer?
    private var hideWork: DispatchWorkItem?
    private var currentScreen: NSScreen?
    private var openedByClick = false
    private var pointerEntered = false

    /// Hover detection on/off (the panel can still be opened from the menu bar icon).
    var hoverEnabled = false { didSet { hoverEnabled ? startMonitoring() : stopMonitoring() } }

    private(set) var isOpen = false

    init(store: Store, actions: PanelActions) {
        self.store = store
        self.actions = actions
        super.init()
    }

    // MARK: geometry

    private static let zoneWidth: CGFloat = 200

    private func topInset(for screen: NSScreen) -> CGFloat {
        max(screen.safeAreaInsets.top, NSStatusBar.system.thickness, 24)
    }

    /// The notch's horizontal span in global coordinates, or a virtual 180 pt one centred
    /// on screens without a notch.
    private func notchSpan(for screen: NSScreen) -> ClosedRange<CGFloat> {
        let f = screen.frame
        if screen.safeAreaInsets.top > 0, let l = screen.auxiliaryTopLeftArea, let r = screen.auxiliaryTopRightArea {
            // These rects are in the screen's own space on some systems; normalise to global.
            let dx: CGFloat = (l.minX < f.minX - 1 || (l.minX == 0 && f.minX != 0)) ? f.minX : 0
            let lo = l.maxX + dx, hi = r.minX + dx
            if hi > lo { return lo...hi }
        }
        return (f.midX - 90)...(f.midX + 90)
    }

    func zone(for screen: NSScreen) -> NSRect {
        let f = screen.frame
        let h = topInset(for: screen)
        let notch = notchSpan(for: screen)
        let w = Self.zoneWidth
        let minX: CGFloat
        switch prefs.hoverZone {
        case .center: minX = f.midX - w / 2
        case .leftOfNotch: minX = notch.lowerBound - w
        case .rightOfNotch: minX = notch.upperBound
        }
        return NSRect(x: minX, y: f.maxY - h, width: w, height: h)
    }

    // MARK: monitoring

    private func startMonitoring() {
        guard monitors.isEmpty else { return }
        // Mouse-moved monitors miss some cases (pointer resting on the menu bar of a
        // full-screen app, cursor warps), so a light poll backs them up.
        let t = Timer(timeInterval: 0.1, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self = self else { return }
                let p = NSEvent.mouseLocation
                if p != self.lastPoint { self.pointerMoved() }
            }
        }
        t.tolerance = 0.05
        RunLoop.main.add(t, forMode: .common)
        pollTimer = t
        let mask: NSEvent.EventTypeMask = [.mouseMoved, .leftMouseDragged]
        if let g = NSEvent.addGlobalMonitorForEvents(matching: mask, handler: { [weak self] _ in
            Task { @MainActor in self?.pointerMoved() }
        }) { monitors.append(g) }
        if let l = NSEvent.addLocalMonitorForEvents(matching: mask, handler: { [weak self] e in
            Task { @MainActor in self?.pointerMoved() }
            return e
        }) { monitors.append(l) }
        // Clicking anywhere outside closes it, like a menu.
        if let c = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown], handler: { [weak self] _ in
            Task { @MainActor in
                guard let self = self, self.isOpen else { return }
                if !self.panelHitRect.contains(NSEvent.mouseLocation) { self.close() }
            }
        }) { monitors.append(c) }
    }

    private func stopMonitoring() {
        pollTimer?.invalidate()
        pollTimer = nil
        monitors.forEach { NSEvent.removeMonitor($0) }
        monitors.removeAll()
        dwellTimer?.invalidate()
        dwellTimer = nil  // a stale invalidated timer would block hover after monitoring restarts
    }

    private var panelHitRect: NSRect {
        guard let w = window else { return .zero }
        return w.frame.insetBy(dx: NotchContainer.shoulder, dy: 0)
    }

    private func pointerMoved() {
        let p = NSEvent.mouseLocation
        lastPoint = p
        if isOpen {
            let inside = panelHitRect.contains(p) || (currentScreen.map { zone(for: $0).contains(p) } ?? false)
            if inside {
                pointerEntered = true
                collapseTimer?.invalidate()
                collapseTimer = nil
            } else if !openedByClick || pointerEntered {
                guard collapseTimer == nil else { return }
                collapseTimer = Timer.scheduledTimer(withTimeInterval: 0.25, repeats: false) { [weak self] _ in
                    Task { @MainActor in
                        guard let self = self else { return }
                        self.collapseTimer = nil
                        if !self.panelHitRect.contains(NSEvent.mouseLocation) { self.close() }
                    }
                }
            }
            return
        }
        guard hoverEnabled, NSScreen.screens.contains(where: { zone(for: $0).contains(p) }) else {
            dwellTimer?.invalidate()
            dwellTimer = nil
            return
        }
        guard dwellTimer == nil else { return }
        // A short dwell so sweeping past the strip on the way to a menu bar item doesn't open it.
        dwellTimer = Timer.scheduledTimer(withTimeInterval: 0.12, repeats: false) { [weak self] _ in
            Task { @MainActor in
                guard let self = self else { return }
                self.dwellTimer = nil
                // Look the screen up again here rather than capturing it in the closure.
                let now = NSEvent.mouseLocation
                if let screen = NSScreen.screens.first(where: { self.zone(for: $0).contains(now) }) {
                    self.open(on: screen, byClick: false)
                }
            }
        }
    }

    // MARK: open / close

    func toggleFromClick() {
        if isOpen { close() } else { open(on: screenForClick(), byClick: true) }
    }

    private func screenForClick() -> NSScreen? {
        let p = NSEvent.mouseLocation
        return NSScreen.screens.first { $0.frame.contains(p) } ?? NSScreen.main ?? NSScreen.screens.first
    }

    func open(on screen: NSScreen? = nil, byClick: Bool) {
        guard let screen = screen ?? NSScreen.main ?? NSScreen.screens.first else { return }
        hideWork?.cancel()
        collapseTimer?.invalidate(); collapseTimer = nil
        openedByClick = byClick
        pointerEntered = false
        currentScreen = screen
        let w = window ?? makeWindow()
        window = w
        model.topInset = topInset(for: screen)
        model.bodyHeight = max(260, min(500, screen.visibleFrame.height - 40))
        // Debug: preview the whole scroll content without scrolling (screenshots).
        if let h = ProcessInfo.processInfo.environment["AGENT_TREE_PANEL_HEIGHT"], let v = Double(h) { model.bodyHeight = v }
        model.collapsedWidth = Self.zoneWidth
        Task { await store.refresh() }
        layout(w, on: screen)
        // Hit-testable only while open; see close().
        w.ignoresMouseEvents = false
        if !isOpen {
            model.expanded = false
            w.alphaValue = 1
            w.orderFrontRegardless()
        }
        isOpen = true
        startMonitoringIfNeededForClickOpen()
        DispatchQueue.main.async { [weak self] in
            guard let self = self else { return }
            // The window shadow follows opaque pixels but is not re-traced every frame, so
            // it is off while the shape animates and comes back once it has settled.
            self.window?.hasShadow = false
            withAnimation(self.animation(opening: true)) { self.model.expanded = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.45) {
                guard self.isOpen else { return }
                self.window?.hasShadow = true
                self.window?.invalidateShadow()
            }
        }
    }

    func close() {
        guard isOpen else { return }
        isOpen = false
        collapseTimer?.invalidate(); collapseTimer = nil
        // Stop taking clicks before the collapse animation, so nothing under the shrinking
        // panel (menu bar items, windows) is ever blocked.
        window?.ignoresMouseEvents = true
        window?.hasShadow = false
        withAnimation(animation(opening: false)) { model.expanded = false }
        let work = DispatchWorkItem { [weak self] in
            guard let self = self, !self.isOpen else { return }
            self.window?.orderOut(nil)
            if !self.hoverEnabled { self.stopMonitoring() }
        }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + (NSWorkspace.shared.accessibilityDisplayShouldReduceMotion ? 0.15 : 0.32), execute: work)
    }

    /// When opened from the menu bar icon with hover disabled, we still need the pointer and
    /// outside-click monitors while it is open.
    private func startMonitoringIfNeededForClickOpen() {
        if monitors.isEmpty { startMonitoring() }
    }

    private func animation(opening: Bool) -> Animation {
        if NSWorkspace.shared.accessibilityDisplayShouldReduceMotion { return .easeOut(duration: 0.12) }
        return opening ? .spring(response: 0.36, dampingFraction: 0.82) : .spring(response: 0.28, dampingFraction: 0.95)
    }

    private func makeWindow() -> NotchWindow {
        let w = NotchWindow(contentRect: NSRect(x: 0, y: 0, width: PanelView.width + 2 * NotchContainer.shoulder, height: 600),
                            styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        w.isOpaque = false
        w.backgroundColor = .clear
        w.hasShadow = true
        w.level = NSWindow.Level(rawValue: NSWindow.Level.statusBar.rawValue + 1)
        w.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary, .ignoresCycle]
        w.hidesOnDeactivate = false
        w.isMovable = false
        w.acceptsMouseMovedEvents = true
        w.ignoresMouseEvents = true
        w.appearance = NSAppearance(named: .darkAqua)
        w.delegate = self
        w.onEscape = { [weak self] in self?.close() }
        w.setAccessibilityLabel("Agent Tree")
        let host = NSHostingView(rootView: NotchContainer(model: model, store: store, actions: actions))
        w.contentView = host
        hosting = host
        return w
    }

    private func layout(_ w: NSWindow, on screen: NSScreen) {
        let size = hosting?.fittingSize ?? NSSize(width: PanelView.width + 2 * NotchContainer.shoulder, height: 600)
        let f = screen.frame
        let z = zone(for: screen)
        var x = z.midX - size.width / 2
        x = min(max(x, f.minX + 4), f.maxX - size.width - 4)
        w.setFrame(NSRect(x: x, y: f.maxY - size.height, width: size.width, height: size.height), display: true)
    }

    /// Content height changes as data loads; keep the top edge glued to the screen top.
    func windowDidResize(_ notification: Notification) {
        guard let w = window, let s = currentScreen else { return }
        let top = s.frame.maxY
        if abs(w.frame.maxY - top) > 0.5 { w.setFrameOrigin(NSPoint(x: w.frame.minX, y: top - w.frame.height)) }
        if isOpen { w.invalidateShadow() }
    }

    /// Screen changes (displays plugged in, resolution) move the zones. Keep the panel on
    /// the same display if it is still there; otherwise close it.
    func screensChanged() {
        guard isOpen, let w = window else { return }
        let key = NSDeviceDescriptionKey("NSScreenNumber")
        let id = currentScreen?.deviceDescription[key] as? NSNumber
        guard let screen = NSScreen.screens.first(where: { ($0.deviceDescription[key] as? NSNumber) == id }) else {
            close(); return
        }
        currentScreen = screen
        model.topInset = topInset(for: screen)
        model.bodyHeight = max(260, min(500, screen.visibleFrame.height - 40))
        layout(w, on: screen)
    }
}
