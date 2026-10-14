import SwiftUI
import AppKit

/// Colours for the compact panel. The panel is always dark (Vorssaint style), so these
/// are fixed values rather than semantic system colours.
enum Theme {
    // Vorssaint's palette, so the two panels read as one family.
    static let anthropic = Color(red: 217 / 255, green: 120 / 255, blue: 87 / 255)   // #D97857
    static let openai = Color(red: 125 / 255, green: 153 / 255, blue: 255 / 255)     // #7D99FF
    static let other = Color(red: 0.54, green: 0.56, blue: 0.60)       // #8a8f99
    static let accent = Color(red: 0.55, green: 0.53, blue: 1.0)       // #8d86ff

    static let cardFill = Color.white.opacity(0.065)
    static let cardStroke = Color.white.opacity(0.05)
    static let track = Color.white.opacity(0.13)
    static let cardTitle = Color.white.opacity(0.9)
    static let primary = Color.white.opacity(0.94)
    static let secondary = Color.white.opacity(0.58)
    static let tertiary = Color.white.opacity(0.38)
    static let warn = Color(red: 0.96, green: 0.70, blue: 0.30)
    static let good = Color(red: 0.40, green: 0.80, blue: 0.52)

    static func family(_ f: String?) -> Color {
        switch f {
        case "anthropic": return anthropic
        case "openai": return openai
        default: return other
        }
    }

    static func familyName(_ f: String) -> String {
        switch f {
        case "anthropic": return "Claude"
        case "openai": return "OpenAI"
        default: return f.capitalized
        }
    }
}

/// The Agent Tree mark: the main agent (a rounded square), a subagent row under it, and
/// that subagent's own subagent one level deeper. Drawn as a monochrome template image so
/// the menu bar tints it correctly in light and dark menu bars.
enum TreeGlyph {
    // Geometry on a 20 x 20 grid (y grows downward), shared with `TreeMark`.
    static let root = CGRect(x: 1.5, y: 1.5, width: 5, height: 5)
    static let child = CGRect(x: 7.2, y: 8.2, width: 11.3, height: 3.6)
    static let grandchild = CGRect(x: 12.6, y: 14.9, width: 5.9, height: 3.6)
    static let rootRadius: CGFloat = 1.4
    static let linkWidth: CGFloat = 1.5

    /// The two elbow links (root to subagent, subagent to its subagent), scaled by `s`.
    static func links(_ s: CGFloat) -> CGPath {
        let p = CGMutablePath()
        p.move(to: CGPoint(x: 4 * s, y: 7.2 * s)); p.addLine(to: CGPoint(x: 4 * s, y: 9 * s))
        p.addQuadCurve(to: CGPoint(x: 5 * s, y: 10 * s), control: CGPoint(x: 4 * s, y: 10 * s))
        p.addLine(to: CGPoint(x: 5.6 * s, y: 10 * s))
        p.move(to: CGPoint(x: 9 * s, y: 12.5 * s)); p.addLine(to: CGPoint(x: 9 * s, y: 15.7 * s))
        p.addQuadCurve(to: CGPoint(x: 10 * s, y: 16.7 * s), control: CGPoint(x: 9 * s, y: 16.7 * s))
        p.addLine(to: CGPoint(x: 11 * s, y: 16.7 * s))
        return p
    }

    static func scaled(_ r: CGRect, _ s: CGFloat) -> CGRect {
        CGRect(x: r.minX * s, y: r.minY * s, width: r.width * s, height: r.height * s)
    }

    static func menuBarImage(size: CGFloat = 18) -> NSImage {
        let img = NSImage(size: NSSize(width: size, height: size), flipped: true) { rect in
            guard let cg = NSGraphicsContext.current?.cgContext else { return false }
            let s = rect.width / 20.0
            cg.setStrokeColor(NSColor.black.cgColor)
            cg.setFillColor(NSColor.black.cgColor)
            cg.setLineWidth(linkWidth * s)
            cg.setLineCap(.round)
            cg.addPath(links(s))
            cg.strokePath()
            for (r, radius) in [(root, rootRadius), (child, child.height / 2), (grandchild, grandchild.height / 2)] {
                cg.addPath(CGPath(roundedRect: scaled(r, s), cornerWidth: radius * s, cornerHeight: radius * s, transform: nil))
            }
            cg.fillPath()
            return true
        }
        img.isTemplate = true
        img.accessibilityDescription = "Agent Tree"
        return img
    }
}

/// Small coloured tree mark for the panel header.
struct TreeMark: View {
    var size: CGFloat = 15
    var body: some View {
        Canvas { ctx, sz in
            let s = sz.width / 20
            ctx.stroke(Path(TreeGlyph.links(s)), with: .color(Theme.tertiary),
                       style: StrokeStyle(lineWidth: TreeGlyph.linkWidth * s, lineCap: .round))
            ctx.fill(Path(roundedRect: TreeGlyph.scaled(TreeGlyph.root, s), cornerRadius: TreeGlyph.rootRadius * s), with: .color(Theme.primary))
            ctx.fill(Path(roundedRect: TreeGlyph.scaled(TreeGlyph.child, s), cornerRadius: TreeGlyph.child.height / 2 * s), with: .color(Theme.anthropic))
            ctx.fill(Path(roundedRect: TreeGlyph.scaled(TreeGlyph.grandchild, s), cornerRadius: TreeGlyph.grandchild.height / 2 * s), with: .color(Theme.openai))
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
