import SwiftUI

/// Callbacks from the panel into AppKit land.
final class PanelActions: ObservableObject {
    var openFullView: (String) -> Void = { _ in }
    var openSettings: () -> Void = {}
    var restart: () -> Void = {}
    var quit: () -> Void = {}
}

struct PanelView: View {
    @ObservedObject var store: Store
    @ObservedObject var actions: PanelActions
    var topInset: CGFloat = 0
    /// Height of the part below the menu bar. Content scrolls inside it; header and
    /// footer stay put.
    var bodyHeight: CGFloat = 500

    static let width: CGFloat = 440

    var body: some View {
        VStack(spacing: 8) {
            header
            switch store.connection {
            case .starting:
                StatusMessage(icon: nil, title: "Starting Agent Tree…",
                              detail: "Reading your agent logs. The first start can take a moment.")
                    .frame(maxHeight: .infinity, alignment: .top)
            case .failed(let msg):
                StatusMessage(icon: "exclamationmark.circle", title: "The background server stopped",
                              detail: msg, buttonTitle: "Restart", action: actions.restart)
                    .frame(maxHeight: .infinity, alignment: .top)
            case .live, .mock:
                ScrollView(.vertical) {
                    content.padding(.bottom, 2)
                }
                .scrollIndicators(.hidden)
                .frame(maxHeight: .infinity)
                .mask(
                    // Soft fade at the bottom edge so cut-off cards read as "scroll for more".
                    VStack(spacing: 0) {
                        Color.black
                        LinearGradient(colors: [.black, .black.opacity(0)], startPoint: .top, endPoint: .bottom).frame(height: 14)
                    }
                )
            }
            footer
        }
        .padding(.horizontal, 10)
        .padding(.bottom, 10)
        .padding(.top, topInset + 4)
        .frame(width: PanelView.width, height: topInset + bodyHeight)
        .environment(\.colorScheme, .dark)
    }

    // MARK: header

    var header: some View {
        HStack(spacing: 8) {
            TreeMark(size: 14)
            Text("Agent Tree")
                .font(.system(size: 13.5, weight: .bold))
                .foregroundStyle(Theme.primary)
            if store.connection == .mock {
                Chip(text: "Sample data", color: Theme.tertiary)
            }
            Spacer()
            if let flags = store.summary?.flagCount, flags > 0 {
                Button { actions.openFullView("#/flags") } label: {
                    HStack(spacing: 3) {
                        Image(systemName: (store.summary?.flagCounts?["alert"] ?? 0) > 0 ? "exclamationmark.octagon.fill" : "exclamationmark.triangle.fill")
                            .font(.system(size: 9))
                        Text("\(flags) flag\(flags == 1 ? "" : "s")")
                            .font(.system(size: 10.5, weight: .semibold).monospacedDigit())
                    }
                    .foregroundStyle(Theme.warn)
                    .padding(.horizontal, 7)
                    .padding(.vertical, 3)
                    .background(Capsule().fill(Theme.warn.opacity(0.14)))
                }
                .buttonStyle(.plain)
                .help("Open flags in the full view")
                .accessibilityLabel("\(flags) flags. Open in the full view")
            }
        }
        .padding(.horizontal, 4)
        .padding(.top, 2)
    }

    // MARK: cards

    var content: some View {
        VStack(spacing: 10) {
            HStack(alignment: .top, spacing: 10) {
                LimitsCard(title: "Claude", provider: "claude", family: "anthropic", icon: "asterisk",
                           data: store.limits(for: "claude"), loaded: store.limits != nil || store.summary != nil,
                           openSettings: actions.openSettings)
                LimitsCard(title: "Codex", provider: "codex", family: "openai", icon: "chevron.left.forwardslash.chevron.right",
                           data: store.limits(for: "codex"), loaded: store.limits != nil || store.summary != nil,
                           openSettings: actions.openSettings)
            }
            .fixedSize(horizontal: false, vertical: true)
            HStack(alignment: .top, spacing: 10) {
                SpendingCard(store: store)
                NowCard(running: store.running) { id in actions.openFullView(threadHash(id)) }
            }
            .fixedSize(horizontal: false, vertical: true)
            TreeCard(detail: store.tree) { id in actions.openFullView(threadHash(id)) }
            TrendCard(daily: store.summary?.daily ?? [], monthMoney: store.summary?.periods?["month"]?.money)
            RecentCard(threads: store.recent,
                       open: { id in actions.openFullView(threadHash(id)) },
                       openAll: { actions.openFullView("#/threads") })
            if let err = store.lastError {
                HStack(spacing: 6) {
                    Image(systemName: "exclamationmark.circle").font(.system(size: 10))
                    Text(err).font(.system(size: 10.5)).lineLimit(2)
                }
                .foregroundStyle(Theme.warn)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.horizontal, 4)
            }
        }
    }

    // MARK: footer

    var footer: some View {
        HStack(spacing: 4) {
            if store.manualScanning {
                ProgressView().controlSize(.mini).scaleEffect(0.8)
                Text("Scanning…")
            } else {
                Text("Updated \(Fmt.ago(store.meta?.lastScan))")
                    .help(store.scanning ? "Reading new log entries" : "Last scan of your agent logs")
            }
            Spacer()
            FooterButton(symbol: "arrow.clockwise", title: "Scan") { Task { await store.scan(manual: true) } }
                .disabled(store.manualScanning || !isConnected)
            FooterButton(symbol: "macwindow", title: "Open full view") { actions.openFullView("#/") }
            IconButton(symbol: "gearshape", label: "Settings", action: actions.openSettings)
            IconButton(symbol: "power", label: "Quit Agent Tree", action: actions.quit)
        }
        .font(.system(size: 10.5))
        .foregroundStyle(Theme.tertiary)
        .padding(.horizontal, 4)
    }

    var isConnected: Bool {
        store.connection == .live || store.connection == .mock
    }

    func threadHash(_ id: String) -> String { "#/thread/" + uriComponent(id) }
}

struct IconButton: View {
    let symbol: String
    let label: String
    var tint: Color = Theme.secondary
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Image(systemName: symbol)
                .font(.system(size: 11.5, weight: .medium))
                .foregroundStyle(tint)
                .frame(width: 24, height: 22)
                .background(RoundedRectangle(cornerRadius: 6).fill(Color.white.opacity(hover ? 0.08 : 0)))
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
        .help(label)
        .accessibilityLabel(label)
    }
}

struct FooterButton: View {
    let symbol: String
    let title: String
    let action: () -> Void
    @State private var hover = false
    @Environment(\.isEnabled) private var enabled

    var body: some View {
        Button(action: action) {
            HStack(spacing: 4) {
                Image(systemName: symbol).font(.system(size: 9.5, weight: .semibold))
                Text(title).font(.system(size: 11, weight: .medium))
            }
            .foregroundStyle(enabled ? Theme.primary.opacity(0.85) : Theme.tertiary)
            .padding(.horizontal, 8)
            .frame(height: 22)
            .background(RoundedRectangle(cornerRadius: 6).fill(Color.white.opacity(hover && enabled ? 0.12 : 0.07)))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

struct StatusMessage: View {
    let icon: String?
    let title: String
    let detail: String
    var buttonTitle: String? = nil
    var action: (() -> Void)? = nil

    var body: some View {
        VStack(spacing: 10) {
            if let icon = icon {
                Image(systemName: icon).font(.system(size: 22, weight: .light)).foregroundStyle(Theme.secondary)
            } else {
                ProgressView().controlSize(.small)
            }
            Text(title).font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.primary)
            Text(detail)
                .font(.system(size: 11))
                .foregroundStyle(Theme.secondary)
                .multilineTextAlignment(.center)
                .lineLimit(5)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if let b = buttonTitle, let action = action {
                Button(b, action: action)
                    .controlSize(.regular)
                    .keyboardShortcut(.defaultAction)
            }
        }
        .padding(.horizontal, 30)
        .padding(.vertical, 34)
        .frame(maxWidth: .infinity)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Theme.cardFill))
    }
}
