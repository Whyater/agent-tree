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

/// The Agent Tree mark (root box, two branches, two leaves), drawn as a monochrome
/// template image so the menu bar tints it correctly in light and dark menu bars.
enum TreeGlyph {
    static func menuBarImage(size: CGFloat = 18) -> NSImage {
        let img = NSImage(size: NSSize(width: size, height: size), flipped: true) { rect in
            let s = rect.width / 20.0
            NSColor.black.setStroke()
            NSColor.black.setFill()
            let trunk = NSBezierPath()
            trunk.lineWidth = 1.6 * s
            trunk.lineCapStyle = .round
            trunk.move(to: NSPoint(x: 6 * s, y: 6 * s)); trunk.line(to: NSPoint(x: 6 * s, y: 15 * s))
            trunk.move(to: NSPoint(x: 6 * s, y: 10 * s)); trunk.line(to: NSPoint(x: 10 * s, y: 10 * s))
            trunk.move(to: NSPoint(x: 6 * s, y: 15 * s)); trunk.line(to: NSPoint(x: 10 * s, y: 15 * s))
            trunk.stroke()
            NSBezierPath(roundedRect: NSRect(x: 3.5 * s, y: 2.5 * s, width: 5 * s, height: 5 * s), xRadius: 1.3 * s, yRadius: 1.3 * s).fill()
            NSBezierPath(roundedRect: NSRect(x: 10.5 * s, y: 8 * s, width: 7 * s, height: 4 * s), xRadius: 1.2 * s, yRadius: 1.2 * s).fill()
            NSBezierPath(roundedRect: NSRect(x: 10.5 * s, y: 13 * s, width: 7 * s, height: 4 * s), xRadius: 1.2 * s, yRadius: 1.2 * s).fill()
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
            var p = Path()
            p.move(to: CGPoint(x: 6 * s, y: 6 * s)); p.addLine(to: CGPoint(x: 6 * s, y: 15 * s))
            p.move(to: CGPoint(x: 6 * s, y: 10 * s)); p.addLine(to: CGPoint(x: 10 * s, y: 10 * s))
            p.move(to: CGPoint(x: 6 * s, y: 15 * s)); p.addLine(to: CGPoint(x: 10 * s, y: 15 * s))
            ctx.stroke(p, with: .color(Theme.primary), style: StrokeStyle(lineWidth: 1.6 * s, lineCap: .round))
            ctx.fill(Path(roundedRect: CGRect(x: 3.5 * s, y: 2.5 * s, width: 5 * s, height: 5 * s), cornerRadius: 1.3 * s), with: .color(Theme.primary))
            ctx.fill(Path(roundedRect: CGRect(x: 10.5 * s, y: 8 * s, width: 7 * s, height: 4 * s), cornerRadius: 1.2 * s), with: .color(Theme.anthropic))
            ctx.fill(Path(roundedRect: CGRect(x: 10.5 * s, y: 13 * s, width: 7 * s, height: 4 * s), cornerRadius: 1.2 * s), with: .color(Theme.openai))
        }
        .frame(width: size, height: size)
        .accessibilityHidden(true)
    }
}
