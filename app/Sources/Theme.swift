import AppKit
import SwiftUI

// The look: macOS 26's Liquid Glass for cards and buttons over neutral graphite, so the photos carry the color. What
// is picked or pressed is drawn in ink (black on light, white on dark); the one color is a darkroom safelight orange,
// kept for what is under way and what needs a look. Generous rounding throughout. Liquid Glass is composited by the window server, so an offscreen snapshot (how the views are reviewed without opening a
// window) can't show it: in snapshot mode the same shapes use a translucent material instead.

private struct SnapshotModeKey: EnvironmentKey { static let defaultValue = false }

extension EnvironmentValues {
    var snapshotMode: Bool {
        get { self[SnapshotModeKey.self] }
        set { self[SnapshotModeKey.self] = newValue }
    }
}

enum Theme {
    /// Selected and primary things: black on light, white on dark.
    static let ink = Color.primary
    /// What reads on ink: the window's own background.
    static let onInk = Color(nsColor: .windowBackgroundColor)
    /// The safelight: progress, the photo being worked on, the drop highlight.
    static let accentColor = Color(red: 0.91, green: 0.42, blue: 0.20)
    static let corner: CGFloat = 18
    static let success = Color(red: 0.20, green: 0.74, blue: 0.48)
    static let warning = Color(red: 0.98, green: 0.66, blue: 0.20)
    static let failure = Color(red: 0.95, green: 0.33, blue: 0.36)
}

/// A rounded glass panel (a material one in snapshots).
struct GlassCard: ViewModifier {
    @Environment(\.snapshotMode) private var snapshot
    var corner: CGFloat = Theme.corner
    var padding: CGFloat = 16
    func body(content: Content) -> some View {
        let shape = RoundedRectangle(cornerRadius: corner, style: .continuous)
        if snapshot {
            content.padding(padding)
                .background(.regularMaterial, in: shape)
                .overlay(shape.strokeBorder(.white.opacity(0.35), lineWidth: 0.5))
                .shadow(color: .black.opacity(0.06), radius: 8, y: 2)
        } else {
            content.padding(padding).glassEffect(.regular, in: shape)
        }
    }
}

extension View {
    func glassCard(corner: CGFloat = Theme.corner, padding: CGFloat = 16) -> some View {
        modifier(GlassCard(corner: corner, padding: padding))
    }
}

/// The big call to action: an ink capsule.
struct PrimaryButtonStyle: ButtonStyle {
    @Environment(\.isEnabled) private var enabled
    var tint: AnyShapeStyle = AnyShapeStyle(Theme.ink)
    var text: AnyShapeStyle = AnyShapeStyle(Theme.onInk)
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(size: 14, weight: .semibold))
            .foregroundStyle(text)
            .frame(maxWidth: .infinity, minHeight: 40)
            .background(tint, in: Capsule())
            .shadow(color: .black.opacity(enabled ? 0.18 : 0), radius: 8, y: 3)
            .opacity(enabled ? (configuration.isPressed ? 0.85 : 1) : 0.45)
            .scaleEffect(configuration.isPressed ? 0.98 : 1)
            .animation(.snappy(duration: 0.15), value: configuration.isPressed)
    }
}

/// A selectable chip, for presets and options.
struct Chip: View {
    let title: String
    var symbol: String? = nil
    let selected: Bool
    var action: () -> Void
    var body: some View {
        Button(action: action) {
            HStack(spacing: 6) {
                if let symbol { Image(systemName: symbol).font(.system(size: 11, weight: .semibold)) }
                Text(title).font(.system(size: 12.5, weight: selected ? .semibold : .regular))
            }
            .padding(.horizontal, 12).padding(.vertical, 7)
            .frame(maxWidth: .infinity)
            .foregroundStyle(selected ? Theme.onInk : .primary)
            .background {
                if selected { Capsule().fill(Theme.ink) } else { Capsule().fill(.primary.opacity(0.06)) }
            }
            .overlay(Capsule().strokeBorder(.primary.opacity(selected ? 0 : 0.08), lineWidth: 0.6))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .animation(.snappy(duration: 0.2), value: selected)
    }
}

/// A slim progress bar in the safelight color.
struct ProgressBar: View {
    let fraction: Double
    var height: CGFloat = 6
    var body: some View {
        GeometryReader { geometry in
            ZStack(alignment: .leading) {
                Capsule().fill(.primary.opacity(0.08))
                Capsule().fill(Theme.accentColor)
                    .frame(width: max(height, geometry.size.width * min(1, max(0, fraction))))
            }
        }
        .frame(height: height)
        .animation(.smooth, value: fraction)
    }
}

/// A section heading inside the inspector.
struct SectionTitle: View {
    let title: String
    var symbol: String
    var trailing: String? = nil
    var body: some View {
        HStack(spacing: 7) {
            Image(systemName: symbol).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            Text(title).font(.system(size: 13, weight: .semibold))
            Spacer()
            if let trailing { Text(trailing).font(.system(size: 11)).foregroundStyle(.secondary) }
        }
    }
}

/// A status capsule: waiting, running, done, failed.
struct StatusBadge: View {
    let status: JobStatus
    var body: some View {
        let (text, color, symbol): (String, Color, String) = switch status {
        case .waiting: ("等待", .secondary, "clock")
        case .running: ("处理中", Theme.accentColor, "paintbrush.pointed.fill")
        case .done: ("完成", Theme.success, "checkmark")
        case .failed: ("失败", Theme.failure, "exclamationmark")
        case .cancelled: ("已取消", .secondary, "stop")
        }
        Label(text, systemImage: symbol)
            .font(.system(size: 10.5, weight: .semibold))
            .labelStyle(.titleAndIcon)
            .padding(.horizontal, 7).padding(.vertical, 3)
            .foregroundStyle(color)
            .background(color.opacity(0.12), in: Capsule())
    }
}

extension Color {
    init(_ rgb: RGB) { self.init(.sRGB, red: rgb.red, green: rgb.green, blue: rgb.blue) }
}

extension RGB {
    init(_ color: Color) {
        let ns = NSColor(color).usingColorSpace(.sRGB) ?? .white
        self.init(red: Double(ns.redComponent), green: Double(ns.greenComponent), blue: Double(ns.blueComponent))
    }
}
