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

    var body: some View {
        Form {
            Section {
                Picker("Mode", selection: $prefs.mode) {
                    ForEach(AppMode.allCases) { Text($0.label).tag($0) }
                }
                .pickerStyle(.radioGroup)
                Text(modeNote)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                if prefs.mode == .desktop {
                    Toggle("Also show the menu bar panel", isOn: $prefs.notchPanelInDesktop)
                }
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
                if let e = loginError {
                    Text(e).font(.callout).foregroundStyle(.red)
                }
            } header: {
                Text("App")
            }

            Section {
                if prefs.mode != .insideVorssaint {
                    Picker("Hover zone", selection: $prefs.hoverZone) {
                        ForEach(HoverZone.allCases) { Text($0.label).tag($0) }
                    }
                    .disabled(!prefs.hoverPanelEnabled)
                }
                Toggle("Show menu bar icon", isOn: $prefs.showMenuBarIcon)
                Toggle("Show highest limit % next to the icon", isOn: $prefs.showPercentInMenuBar)
                    .disabled(!prefs.showMenuBarIcon)
            } header: {
                Text("Menu bar")
            } footer: {
                Text(zoneFooter)
                    .font(.footnote).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Section {
                claudeRow
                toolRow("Codex", icon: "chevron.left.forwardslash.chevron.right", color: Theme.openai,
                        status: store.integrations?.codex?.installed == false ? "Not installed" : "Live via session logs",
                        live: store.integrations?.codex?.installed != false)
                toolRow("T3 Code", icon: "rectangle.3.group", color: .secondary,
                        status: store.integrations?.t3?.installed == false ? "Not installed" : "Live via its local database",
                        live: store.integrations?.t3?.installed != false)
                toolRow("Vorssaint", icon: "rectangle.topthird.inset.filled", color: .secondary,
                        status: !vorssaintInstalled ? "Not installed"
                            : vorssaintHasCard ? "Shows the Agent Tree card" : "Installed, without the Agent Tree card",
                        live: vorssaintInstalled && vorssaintHasCard)
            } header: {
                Text("Integrations")
            } footer: {
                Text("Agent Tree reads these tools' local files. Connect is the only thing that changes another tool's settings, and it shows you the change first.")
                    .font(.footnote).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }

            Section {
                if let checks = store.checks {
                    if checks.isEmpty {
                        Text("No cross-checks available on this Mac.")
                            .foregroundStyle(.secondary)
                    }
                    ForEach(checks) { c in
                        HStack(alignment: .firstTextBaseline, spacing: 8) {
                            checkIcon(c.status)
                            VStack(alignment: .leading, spacing: 2) {
                                Text(c.label ?? c.id)
                                if let d = c.detail, !d.isEmpty {
                                    Text(d).font(.callout).foregroundStyle(.secondary)
                                }
                            }
                            Spacer()
                            Text(statusLabel(c.status)).font(.callout).foregroundStyle(.secondary)
                        }
                        .accessibilityElement(children: .combine)
                    }
                } else {
                    HStack { ProgressView().controlSize(.small); Text("Checking…").foregroundStyle(.secondary) }
                }
            } header: {
                Text("Cross-checks")
            } footer: {
                Text("Compares Agent Tree's numbers with other tools installed on this Mac. Nothing leaves your computer.")
                    .font(.footnote).foregroundStyle(.secondary)
            }

            Section {
                LabeledContent("Covered-by-plan and prices") {
                    Button("Open settings folder") {
                        let dir = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".agent-tree")
                        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
                        NSWorkspace.shared.activateFileViewerSelecting([dir])
                    }
                }
                LabeledContent("Server") {
                    HStack(spacing: 8) {
                        Text(serverText).foregroundStyle(.secondary).textSelection(.enabled)
                        if backend.webURL != nil {
                            Button("Open in browser", action: openInBrowser)
                        }
                    }
                }
            } header: {
                Text("Data")
            }
        }
        .formStyle(.grouped)
        .frame(width: 480)
        .frame(height: 720)
        .task {
            await store.loadChecks()
            await store.loadIntegrations()
        }
        .sheet(isPresented: $confirmConnect) { connectSheet }
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
        VStack(alignment: .leading, spacing: 6) {
            HStack(spacing: 10) {
                Image(systemName: "asterisk").foregroundStyle(Theme.anthropic).frame(width: 18)
                VStack(alignment: .leading, spacing: 1) {
                    Text("Claude Code")
                    Text(claudeStatus).font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                if working { ProgressView().controlSize(.small) }
                if claude?.connected == true {
                    Button("Disconnect") { run(connect: false) }.disabled(working)
                } else {
                    Button("Connect…") { claudeError = nil; confirmConnect = true }
                        .disabled(working || claude == nil || claude?.installed == false)
                }
            }
            if let e = claudeError {
                Text(e)
                    .font(.callout)
                    .foregroundStyle(.red)
                    .textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .accessibilityElement(children: .contain)
    }

    func toolRow(_ name: String, icon: String, color: Color, status: String, live: Bool) -> some View {
        HStack(spacing: 10) {
            Image(systemName: icon).foregroundStyle(color).frame(width: 18)
            Text(name)
            Spacer()
            Label(status, systemImage: live ? "checkmark.circle.fill" : "minus.circle")
                .labelStyle(.titleAndIcon)
                .font(.callout)
                .foregroundStyle(live ? Color.secondary : Color.secondary.opacity(0.7))
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
            .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.06)))
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

    @ViewBuilder func checkIcon(_ status: String) -> some View {
        switch status {
        case "match": Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
        case "mismatch": Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
        default: Image(systemName: "minus.circle").foregroundStyle(.secondary)
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
