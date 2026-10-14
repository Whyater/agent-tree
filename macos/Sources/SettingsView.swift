import SwiftUI
import AppKit

struct SettingsView: View {
    @ObservedObject var prefs: Preferences
    @ObservedObject var store: Store
    let backend: Backend
    var openInBrowser: () -> Void

    @State private var confirmConnect = ProcessInfo.processInfo.environment["AGENT_TREE_OPEN_CONNECT"] == "1"
    @State private var claudeError: String?
    @State private var working = false
    @State private var loginError: String?
    @State private var launchAtLogin = Preferences.shared.launchAtLogin
    @State private var vorssaintHasCard = Preferences.vorssaintHasCard

    @Environment(\.colorSchemeContrast) private var contrast

    var body: some View {
        ScrollView(.vertical) {
            VStack(alignment: .leading, spacing: Style.Space.betweenCards) {
                Text("Settings")
                    .font(.title2.weight(.bold))
                    .accessibilityAddTraits(.isHeader)
                    .padding(.bottom, -6)

                generalCard
                menuBarCard
                integrationsCard
                checksCard
                dataCard
            }
            .padding(.horizontal, Style.Space.page)
            .padding(.top, 6)
            .padding(.bottom, Style.Space.page)
            .frame(maxWidth: Style.maxContentWidth)
            .frame(maxWidth: .infinity)
        }
        .frame(minWidth: 600, idealWidth: 680, minHeight: 420, idealHeight: 720)
        .toggleStyle(.switch)
        .controlSize(.regular)
        .task {
            await store.loadChecks()
            await store.loadIntegrations()
        }
        .sheet(isPresented: $confirmConnect) { connectSheet }
    }

    var generalCard: some View {
        group("General") {
            modeRow
            if prefs.mode == .desktop {
                divider
                SettingRow(symbol: "menubar.rectangle", title: "Also show the menu bar panel",
                           caption: "Drops down from the top edge, as in Menu bar panel mode.") {
                    Toggle("Also show the menu bar panel", isOn: $prefs.notchPanelInDesktop).controlSize(.small)
                }
            }
            divider
            SettingRow(symbol: "power", title: "Launch at login",
                       caption: "Starts Agent Tree when you log in to this Mac.",
                       error: loginError) {
                Toggle("Launch at login", isOn: Binding(
                    get: { launchAtLogin },
                    set: { on in
                        do {
                            try prefs.setLaunchAtLogin(on)
                            loginError = nil
                        } catch {
                            loginError = "Could not change this: \(error.localizedDescription)"
                        }
                        launchAtLogin = prefs.launchAtLogin
                    }))
                .controlSize(.small)
            }
        }
    }

    var menuBarCard: some View {
        group("Menu bar", footer: prefs.mode == .insideVorssaint ? zoneFooter : nil) {
            if prefs.mode != .insideVorssaint {
                SettingRow(symbol: "cursorarrow.rays", title: "Hover zone", caption: hoverCaption,
                           reason: prefs.hoverPanelEnabled ? nil : "Turn on the menu bar panel above to use it.",
                           captionBelow: true) {
                    Picker("Hover zone", selection: $prefs.hoverZone) {
                        ForEach(HoverZone.allCases) { Text($0.label).tag($0) }
                    }
                    .pickerStyle(.segmented)
                    .fixedSize()
                }
                .disabled(!prefs.hoverPanelEnabled)
                divider
            }
            SettingRow(symbol: "menubar.arrow.up.rectangle", title: "Show menu bar icon",
                       caption: "The tree icon at the right of the menu bar.") {
                Toggle("Show menu bar icon", isOn: $prefs.showMenuBarIcon).controlSize(.small)
            }
            divider
            SettingRow(symbol: "percent", title: "Show highest limit % next to the icon",
                       caption: "The busiest plan limit, as a number.",
                       reason: prefs.showMenuBarIcon ? nil : "Needs the menu bar icon.") {
                Toggle("Show highest limit % next to the icon", isOn: $prefs.showPercentInMenuBar).controlSize(.small)
            }
            .disabled(!prefs.showMenuBarIcon)
        }
    }

    var integrationsCard: some View {
        group("Integrations", footer: "Agent Tree reads these tools' local files. Connect is the only thing that changes another tool's settings, and it shows you the change first.") {
            claudeRow
            divider
            toolRow("Codex", icon: "chevron.left.forwardslash.chevron.right", color: Theme.openai,
                    status: store.integrations?.codex?.installed == false ? "Not installed" : "Live via session logs",
                    live: store.integrations?.codex?.installed != false)
            divider
            toolRow("T3 Code", icon: "rectangle.3.group", color: .accentColor,
                    status: store.integrations?.t3?.installed == false ? "Not installed" : "Live via its local database",
                    live: store.integrations?.t3?.installed != false)
            divider
            toolRow("Vorssaint", icon: "rectangle.topthird.inset.filled", color: .accentColor,
                    status: !vorssaintInstalled ? "Not installed"
                        : vorssaintHasCard ? "Shows the Agent Tree card" : "Installed, without the Agent Tree card",
                    live: vorssaintInstalled && vorssaintHasCard)
        }
    }

    var checksCard: some View {
        group("Cross-checks", footer: "Compares Agent Tree's numbers with other tools installed on this Mac. Nothing leaves your computer.") {
            if let checks = store.checks {
                if checks.isEmpty {
                    Text("No cross-checks available on this Mac.")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }
                ForEach(Array(checks.enumerated()), id: \.element.id) { i, c in
                    if i > 0 { divider }
                    SettingRow(symbol: checkSymbol(c.status), tint: checkColor(c.status),
                               title: c.label ?? c.id, caption: c.detail.flatMap { $0.isEmpty ? nil : $0 }) {
                        StatusPill(text: statusLabel(c.status), color: checkColor(c.status))
                    }
                    .accessibilityElement(children: .combine)
                }
            } else {
                HStack(spacing: 8) {
                    ProgressView().controlSize(.small)
                    Text("Checking…").font(.callout).foregroundStyle(.secondary)
                }
            }
        }
    }

    var dataCard: some View {
        group("Data") {
            SettingRow(symbol: "folder", title: "Covered-by-plan and prices",
                       caption: "Kept in ~/.agent-tree on this Mac.") {
                Button("Open settings folder") {
                    let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".agent-tree")
                    try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                    NSWorkspace.shared.activateFileViewerSelecting([dir])
                }
            }
            divider
            SettingRow(symbol: "server.rack", title: "Server", caption: serverText, selectableCaption: true) {
                if backend.webURL != nil {
                    Button("Open in browser", action: openInBrowser)
                }
            }
        }
    }

    // MARK: layout pieces

    /// A titled card: small uppercase label, rounded block of rows, optional grey note under it.
    func group<Content: View>(_ title: String, footer: String? = nil, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            SectionLabel(text: title)
                .padding(.leading, 4)
            VStack(alignment: .leading, spacing: 12) {
                content()
            }
            .padding(Style.Space.cardPadding)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(RoundedRectangle(cornerRadius: Style.Radius.settingsCard, style: .continuous)
                .fill(.quaternary.opacity(0.35)))
            .overlay(RoundedRectangle(cornerRadius: Style.Radius.settingsCard, style: .continuous)
                .strokeBorder(Style.settingsBorder(contrast), lineWidth: Style.hairline))
            if let footer = footer {
                Text(footer)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .padding(.horizontal, 4)
            }
        }
    }

    /// Hairline between rows, starting at the text column.
    var divider: some View {
        Rectangle()
            .fill(Style.settingsBorder(contrast))
            .frame(height: Style.hairline)
            .padding(.leading, Style.Space.textColumn)
    }

    var modeRow: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: Style.Space.tileGap) {
                IconTile(symbol: "square.on.square")
                Text("Mode").font(.system(size: 13, weight: .medium))
                Spacer(minLength: 12)
                Picker("Mode", selection: $prefs.mode) {
                    ForEach(AppMode.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
            }
            Text(modeNote)
                .font(.system(size: 11))
                .foregroundStyle(modeWarning ? Style.orange : .secondary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(.leading, Style.Space.textColumn)
        }
    }

    /// Inside Vorssaint with nothing to show it in: say so in the failure colour.
    var modeWarning: Bool {
        prefs.mode == .insideVorssaint && (!vorssaintInstalled || !vorssaintHasCard)
    }

    var modeNote: String {
        switch prefs.mode {
        case .compact:
            return "Lives in the menu bar with no Dock icon. Point at the hover zone at the top of the screen and the panel drops down."
        case .insideVorssaint:
            if !vorssaintInstalled {
                return "Vorssaint isn't installed, so nothing will show. Pick Menu bar panel instead."
            }
            if !vorssaintHasCard {
                return "This Vorssaint doesn't have the Agent Tree card yet, so nothing will show. The guide explains how to add it; until then, Menu bar panel works on its own."
            }
            return "Shows as a card in Vorssaint's AI Agents page."
        case .desktop:
            return "A regular app with a Dock icon and a main window."
        }
    }

    var zoneFooter: String {
        if prefs.mode == .insideVorssaint {
            return "Agent Tree keeps running in the background so Vorssaint's card stays up to date. The icon's menu opens the full view."
        }
        var s = "Point at this part of the top edge and the panel drops down. It closes when the pointer leaves it."
        if Preferences.vorssaintInstalled {
            s += " Vorssaint is installed and uses the middle of the notch, so Right of notch keeps the two panels apart."
        }
        if !prefs.hoverPanelEnabled { s += " Turn on the menu bar panel above to use it." }
        return s
    }

    /// Shorter explanation for the Hover zone row; the "why it is off" part is its own line.
    var hoverCaption: String {
        var s = "Point at this part of the top edge and the panel drops down."
        if Preferences.vorssaintInstalled {
            s += " Right of notch keeps it clear of Vorssaint's panel in the middle."
        }
        return s
    }

    var vorssaintInstalled: Bool {
        store.integrations?.vorssaint?.installed ?? Preferences.vorssaintInstalled
    }

    var claude: ClaudeIntegration? { store.integrations?.claude }

    var claudeStatus: String {
        guard let c = claude else { return "Checking…" }
        if c.installed == false { return "Not installed" }
        if c.connected == true { return c.hooks == "partial" ? "Connected (some hooks missing)" : "Connected" }
        if c.statusline == "other" { return "Another status line is set" }
        return "Not connected"
    }

    var claudeRow: some View {
        SettingRow(symbol: "asterisk", tint: Theme.anthropic, title: "Claude Code", caption: claudeStatus,
                   error: claudeError, selectableError: true) {
            HStack(spacing: 8) {
                if working { ProgressView().controlSize(.small) }
                if claude?.connected == true {
                    Button("Disconnect") { run(connect: false) }.disabled(working)
                } else {
                    Button("Connect…") { claudeError = nil; confirmConnect = true }
                        .disabled(working || claude == nil || claude?.installed == false)
                }
            }
        }
        .accessibilityElement(children: .contain)
    }

    func toolRow(_ name: String, icon: String, color: Color, status: String, live: Bool) -> some View {
        SettingRow(symbol: icon, tint: color, title: name, caption: status) {
            StatusPill(text: live ? "Live" : "Off", color: live ? Style.green : Color.secondary)
        }
        .accessibilityElement(children: .combine)
    }

    var connectSheet: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Connect Claude Code").font(.headline)
            Text("Agent Tree will edit \(claude?.settingsPath ?? "~/.claude/settings.json"). A backup of the current file is saved first, and Disconnect undoes it.")
                .font(.callout)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            ScrollView {
                Text(claude?.preview ?? "No preview available.")
                    .font(.system(size: 11, design: .monospaced))
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .padding(10)
            }
            .frame(height: 220)
            .background(RoundedRectangle(cornerRadius: Style.Radius.note, style: .continuous).fill(Color.primary.opacity(0.05)))
            .overlay(RoundedRectangle(cornerRadius: Style.Radius.note, style: .continuous)
                .strokeBorder(Style.settingsBorder(contrast), lineWidth: Style.hairline))
            HStack {
                Spacer()
                Button("Cancel") { confirmConnect = false }.keyboardShortcut(.cancelAction)
                // Deliberately no Return shortcut: connecting edits another app's settings, so it
                // takes a real click. Cancel is the default and answers Esc.
                Button("Connect") { confirmConnect = false; run(connect: true) }
                    .focusable(false)
            }
        }
        .padding(20)
        .frame(width: 460)
    }

    func run(connect: Bool) {
        working = true
        claudeError = nil
        Task {
            claudeError = await store.setClaudeConnected(connect)
            working = false
        }
    }

    var serverText: String {
        switch backend.state {
        case .mock: return "Sample data (mock mode)"
        case .ready(let u): return u.host.map { "\($0):\(u.port ?? 0)" + (backend.version.map { " · v\($0)" } ?? "") } ?? u.absoluteString
        case .starting: return "Starting…"
        case .failed: return "Stopped"
        case .idle: return "Not started"
        }
    }

    func checkSymbol(_ status: String) -> String {
        switch status {
        case "match": return "checkmark"
        case "mismatch": return "exclamationmark.triangle"
        default: return "minus"
        }
    }

    func checkColor(_ status: String) -> Color {
        switch status {
        case "match": return Style.green
        case "mismatch": return Style.orange
        default: return .secondary
        }
    }

    func statusLabel(_ s: String) -> String {
        switch s {
        case "match": return "Match"
        case "mismatch": return "Mismatch"
        default: return "Unavailable"
        }
    }
}

/// One settings row: icon tile, title, one grey caption, control at the right. An error,
/// if any, shows in red under the caption.
struct SettingRow<Control: View>: View {
    let symbol: String
    var tint: Color = .accentColor
    let title: String
    var caption: String? = nil
    var selectableCaption = false
    var error: String? = nil
    var selectableError = false
    /// Why the row is disabled. Shown at full strength with an info symbol.
    var reason: String? = nil
    /// Long captions go under the whole row instead of beside a wide control.
    var captionBelow = false
    @ViewBuilder var control: () -> Control
    @Environment(\.isEnabled) private var enabled

    var body: some View {
        if captionBelow {
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .center, spacing: Style.Space.tileGap) {
                    tile
                    Text(title)
                        .font(.system(size: 13, weight: .medium))
                        .opacity(enabled ? 1 : 0.55)
                    Spacer(minLength: 12)
                    dimmedControl
                }
                VStack(alignment: .leading, spacing: 3) {
                    if let c = caption { captionText(c).opacity(enabled ? 1 : 0.55) }
                    if let r = reason { reasonText(r) }
                    if let e = error { errorText(e) }
                }
                .padding(.leading, Style.Space.textColumn)
            }
        } else {
            HStack(alignment: .center, spacing: Style.Space.tileGap) {
                tile
                VStack(alignment: .leading, spacing: 2) {
                    Group {
                        Text(title)
                            .font(.system(size: 13, weight: .medium))
                            .fixedSize(horizontal: false, vertical: true)
                        if let c = caption { captionText(c) }
                    }
                    .opacity(enabled ? 1 : 0.55)
                    if let r = reason { reasonText(r).padding(.top, 1) }
                    if let e = error { errorText(e) }
                }
                Spacer(minLength: 12)
                dimmedControl
            }
            .frame(minHeight: Style.Space.tile)
        }
    }

    /// A disabled control keeps its real state but loses its colour, so an "on" switch
    /// does not read as a live accent.
    var dimmedControl: some View {
        control()
            .labelsHidden()
            .saturation(enabled ? 1 : 0)
            .opacity(enabled ? 1 : 0.5)
    }

    func reasonText(_ r: String) -> some View {
        Label {
            Text(r).fixedSize(horizontal: false, vertical: true)
        } icon: {
            Image(systemName: "info.circle")
        }
        .labelStyle(.titleAndIcon)
        .font(.system(size: 11))
        .foregroundStyle(.secondary)
    }

    var tile: some View {
        IconTile(symbol: symbol, tint: tint).saturation(enabled ? 1 : 0)
    }

    @ViewBuilder func captionText(_ c: String) -> some View {
        let t = Text(c)
            .font(.system(size: 11))
            .foregroundStyle(.secondary)
            .fixedSize(horizontal: false, vertical: true)
        if selectableCaption { t.textSelection(.enabled) } else { t }
    }

    @ViewBuilder func errorText(_ e: String) -> some View {
        let t = Text(e)
            .font(.system(size: 11))
            .foregroundStyle(Style.red)
            .fixedSize(horizontal: false, vertical: true)
        if selectableError { t.textSelection(.enabled) } else { t }
    }
}
