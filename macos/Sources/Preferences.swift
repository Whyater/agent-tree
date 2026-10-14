import Foundation
import ServiceManagement

enum AppMode: String, CaseIterable, Identifiable {
    case compact            // own drop-down panel from the top bar ("Menu bar panel")
    case insideVorssaint    // no panel of its own; Vorssaint shows a card fed by ~/.agent-tree/live.json
    case desktop
    var id: String { rawValue }
    var label: String {
        switch self {
        case .compact: return "Menu bar panel"
        case .insideVorssaint: return "Inside Vorssaint"
        case .desktop: return "Desktop app"
        }
    }
}

/// Where the invisible hover strip sits along the top edge of the screen.
enum HoverZone: String, CaseIterable, Identifiable {
    case center, leftOfNotch, rightOfNotch
    var id: String { rawValue }
    var label: String {
        switch self {
        case .center: return "Center"
        case .leftOfNotch: return "Left of notch"
        case .rightOfNotch: return "Right of notch"
        }
    }
}

/// Settings persisted in UserDefaults. Changes are applied live by AppDelegate.
final class Preferences: ObservableObject {
    static let shared = Preferences()
    private let d = UserDefaults.standard

    @Published var mode: AppMode { didSet { d.set(mode.rawValue, forKey: "mode") } }
    @Published var showPercentInMenuBar: Bool { didSet { d.set(showPercentInMenuBar, forKey: "showPercentInMenuBar") } }
    @Published var showMenuBarIcon: Bool { didSet { d.set(showMenuBarIcon, forKey: "showMenuBarIcon") } }
    @Published var notchPanelInDesktop: Bool { didSet { d.set(notchPanelInDesktop, forKey: "notchPanelInDesktop") } }
    @Published var hoverZone: HoverZone { didSet { d.set(hoverZone.rawValue, forKey: "hoverZone") } }

    /// The hover panel is always on in Compact mode and opt-in in Desktop mode.
    var hoverPanelEnabled: Bool { mode == .compact || (mode == .desktop && notchPanelInDesktop) }

    static var vorssaintApp: URL? {
        ["/Applications/Vorssaint.app", NSHomeDirectory() + "/Applications/Vorssaint.app"]
            .map { URL(fileURLWithPath: $0) }
            .first { FileManager.default.fileExists(atPath: $0.path) }
    }

    static var vorssaintInstalled: Bool { vorssaintApp != nil }

    /// Whether the installed Vorssaint is a build with the Agent Tree card. The official
    /// download doesn't have it, and Inside Vorssaint mode would then show nothing. The
    /// card names this app's bundle id to open it, so its binary contains that string.
    static var vorssaintHasCard: Bool {
        guard let app = vorssaintApp, let bundle = Bundle(url: app), let exe = bundle.executableURL,
              let data = try? Data(contentsOf: exe, options: .alwaysMapped) else { return false }
        return data.range(of: Data("io.github.whyater.agent-tree".utf8)) != nil
    }

    private init() {
        if let raw = d.string(forKey: "mode"), let m = AppMode(rawValue: raw) {
            mode = m
        } else {
            mode = Preferences.vorssaintHasCard ? .insideVorssaint : .compact
        }
        showPercentInMenuBar = d.object(forKey: "showPercentInMenuBar") as? Bool ?? true
        showMenuBarIcon = d.object(forKey: "showMenuBarIcon") as? Bool ?? true
        notchPanelInDesktop = d.object(forKey: "notchPanelInDesktop") as? Bool ?? false
        // Vorssaint keeps its own notch panel at the top middle, so stay out of its way by default.
        hoverZone = HoverZone(rawValue: d.string(forKey: "hoverZone") ?? "")
            ?? (Preferences.vorssaintInstalled ? .rightOfNotch : .center)
    }

    // Launch at login goes through SMAppService so it shows up in System Settings > Login Items.
    var launchAtLogin: Bool {
        SMAppService.mainApp.status == .enabled
    }

    func setLaunchAtLogin(_ on: Bool) throws {
        if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        objectWillChange.send()
    }
}
