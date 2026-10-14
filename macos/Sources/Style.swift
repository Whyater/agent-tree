import SwiftUI
import AppKit

/// Shared layout and colour tokens for the native windows and the compact panel.
/// `Theme` keeps the brand and panel palette; this file holds the shapes, spacing,
/// meters, and the small building blocks that sit on top of it.
enum Style {
    // MARK: radii (continuous corners everywhere)
    enum Radius {
        static let tile: CGFloat = 7          // icon tiles, hover washes in the panel
        static let note: CGFloat = 8          // info boxes, code previews
        static let control: CGFloat = 7       // small footer buttons
        static let panelCard: CGFloat = 16    // cards inside the dark panel
        static let settingsCard: CGFloat = 16
    }

    // MARK: spacing
    enum Space {
        static let page: CGFloat = 22         // settings page padding
        static let cardPadding: CGFloat = 16
        static let betweenCards: CGFloat = 20
        static let tile: CGFloat = 26         // icon tile size
        static let tileGap: CGFloat = 12
        /// Where a row's text column starts, so follow-up lines and dividers line up.
        static var textColumn: CGFloat { tile + tileGap }
    }

    static let maxContentWidth: CGFloat = 760
    static let meterHeight: CGFloat = 5
    static let hairline: CGFloat = 0.7

    // MARK: status hues. Light mode uses darker versions so text stays readable.
    static let yellow = adaptive(dark: NSColor.systemYellow,
                                 light: NSColor(red: 0x8F / 255, green: 0x5C / 255, blue: 0, alpha: 1))
    static let red = adaptive(dark: NSColor.systemRed,
                              light: NSColor(red: 0xAD / 255, green: 0x14 / 255, blue: 0x1A / 255, alpha: 1))
    static let green = adaptive(dark: NSColor.systemGreen,
                                light: NSColor(red: 0, green: 0x70 / 255, blue: 0x30 / 255, alpha: 1))
    static let orange = adaptive(dark: NSColor.systemOrange,
                                 light: NSColor(red: 0xAD / 255, green: 0x4D / 255, blue: 0, alpha: 1))

    // The panel is always dark, so it uses the system hues directly.
    static let panelYellow = Color(red: 0.95, green: 0.76, blue: 0.30)   // soft amber, calmer than system yellow
    static let panelRed = Color(red: 1.0, green: 0.36, blue: 0.36)

    static func adaptive(dark: NSColor, light: NSColor) -> Color {
        Color(nsColor: NSColor(name: nil) { appearance in
            appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
        })
    }

    /// Meter colour: the family colour while there is room, yellow from 60%, red above 85%.
    static func meterColor(fraction: Double, base: Color) -> Color {
        if fraction > 0.85 { return panelRed }
        if fraction >= 0.6 { return panelYellow }
        return base
    }

    // MARK: panel surfaces (always dark)
    static let panelCardFill = Color.white.opacity(0.07)
    static func panelCardStroke(_ contrast: ColorSchemeContrast) -> Color {
        Color.white.opacity(contrast == .increased ? 0.28 : 0.09)
    }
    static let panelHover = Color.white.opacity(0.08)
    static let panelTrack = Color.white.opacity(0.13)

    // MARK: settings surfaces (follow the system appearance)
    static func settingsBorder(_ contrast: ColorSchemeContrast) -> Color {
        Color.primary.opacity(contrast == .increased ? 0.26 : 0.09)
    }

    // MARK: motion
    static let hoverSpring = Animation.spring(response: 0.3, dampingFraction: 0.75)
    static let quick = Animation.easeOut(duration: 0.14)
}

// MARK: - Small shared views

/// Rounded square holding an SF Symbol, tinted with a low-opacity wash of its colour.
struct IconTile: View {
    let symbol: String
    var tint: Color = .accentColor
    var size: CGFloat = Style.Space.tile

    var body: some View {
        Image(systemName: symbol)
            .font(.system(size: size * 0.46, weight: .semibold))
            .foregroundStyle(tint)
            .frame(width: size, height: size)
            .background(RoundedRectangle(cornerRadius: Style.Radius.tile, style: .continuous).fill(tint.opacity(0.13)))
            .accessibilityHidden(true)
    }
}

/// Small uppercase label above a group of settings or a panel section.
struct SectionLabel: View {
    let text: String
    var body: some View {
        Text(text.uppercased())
            .font(.system(size: 10.5, weight: .semibold))
            .kerning(0.5)
            .foregroundStyle(.secondary)
            .accessibilityAddTraits(.isHeader)
    }
}

/// Coloured dot plus a short label on a soft tint of the same colour.
struct StatusPill: View {
    let text: String
    let color: Color
    var body: some View {
        HStack(spacing: 5) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(text).font(.system(size: 11, weight: .medium))
        }
        .foregroundStyle(color)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(Capsule().fill(color.opacity(0.13)))
        .fixedSize()
    }
}

/// Plain button that dims and shrinks a little while pressed. Respects Reduce Motion.
struct PressableStyle: ButtonStyle {
    var pressedScale: CGFloat = 0.965
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .scaleEffect(configuration.isPressed && !reduceMotion ? pressedScale : 1)
            .opacity(configuration.isPressed ? 0.8 : 1)
            .animation(reduceMotion ? nil : Style.hoverSpring, value: configuration.isPressed)
    }
}
