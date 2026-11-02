import AppKit
import SwiftUI

/// The floating ball: for working in Compositor, the app shrinks to a small circle that floats over every window.
/// Clicking it opens a compact panel beside it to pick what to do and retouch the project Compositor is editing.
/// Both are non-activating panels: clicking them never takes the focus from Compositor.
@MainActor final class FloatingMode {
    static let shared = FloatingMode()
    static let defaultsKey = "floatingMode"  // "floatingBall" was the first version's, which could crash at launch
    static let ballSize: CGFloat = 64
    static let pickerSize = NSSize(width: 340, height: 660)

    weak var mainWindow: NSWindow?
    private(set) var studio: Studio?
    private var ball: NSPanel?
    private var picker: NSPanel?
    private var dragStart: (mouse: NSPoint, origin: NSPoint)?

    var isOn: Bool { ball?.isVisible == true }
    var isPickerOpen: Bool { picker?.isVisible == true }

    /// Shrinks to the ball: the ball appears where it was left last time, then the main window goes away.
    func enter(_ studio: Studio) {
        guard !isOn else { return }
        self.studio = studio
        studio.compositor.pollAlways = true  // the app isn't in front now, so keep looking at Compositor
        studio.compositor.refresh()
        let ball = self.ball ?? makeBall(studio)
        self.ball = ball
        ball.orderFrontRegardless()
        Task { @MainActor in
            // once the click that asked for this has been handled: hiding the window under its own toolbar
            // button while the button is still at work is asking for trouble
            self.mainWindow?.orderOut(nil)
            // remembered for the next launch only once the ball has been up a while, so that a ball that can't
            // stay up never makes every launch fail
            try? await Task.sleep(for: .seconds(5))
            if self.isOn { UserDefaults.standard.set(true, forKey: Self.defaultsKey) }
        }
    }

    /// Back to the full window.
    func leave() {
        picker?.orderOut(nil)
        ball?.orderOut(nil)
        studio?.compositor.pollAlways = false
        UserDefaults.standard.set(false, forKey: Self.defaultsKey)
        NSApp.activate()
        mainWindow?.makeKeyAndOrderFront(nil)
    }

    func togglePicker() {
        guard let studio else { return }
        if isPickerOpen { picker?.orderOut(nil); return }
        let picker = self.picker ?? makePanel(size: Self.pickerSize, content: FloatingPicker(studio: studio))
        self.picker = picker
        studio.compositor.refresh()
        place(picker)
        picker.orderFrontRegardless()
    }

    func closePicker() { picker?.orderOut(nil) }

    // MARK: dragging the ball (a click is a drag that didn't move)

    func drag() {
        guard let ball else { return }
        let mouse = NSEvent.mouseLocation
        if dragStart == nil { dragStart = (mouse, ball.frame.origin) }
        guard let start = dragStart else { return }
        ball.setFrameOrigin(NSPoint(x: start.origin.x + mouse.x - start.mouse.x, y: start.origin.y + mouse.y - start.mouse.y))
        if isPickerOpen, let picker { place(picker) }
    }

    func endDrag() {
        defer { dragStart = nil }
        guard let ball, let start = dragStart else { togglePicker(); return }
        let moved = hypot(ball.frame.origin.x - start.origin.x, ball.frame.origin.y - start.origin.y)
        if moved < 4 {
            ball.setFrameOrigin(start.origin)
            togglePicker()
        } else {
            UserDefaults.standard.set(NSStringFromPoint(ball.frame.origin), forKey: Self.defaultsKey + ".origin")
        }
    }

    // MARK: windows

    private func makeBall(_ studio: Studio) -> NSPanel {
        let size = NSSize(width: Self.ballSize + 24, height: Self.ballSize + 24)  // room for the shadow and the ring
        let panel = makePanel(size: size, content: BallView(studio: studio))
        let saved = UserDefaults.standard.string(forKey: Self.defaultsKey + ".origin").map(NSPointFromString)
        let screen = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        var origin = saved ?? NSPoint(x: screen.maxX - size.width - 24, y: screen.minY + 120)
        if !NSScreen.screens.contains(where: { $0.visibleFrame.insetBy(dx: -20, dy: -20).contains(origin) }) {
            origin = NSPoint(x: screen.maxX - size.width - 24, y: screen.minY + 120)  // its screen is gone
        }
        panel.setFrameOrigin(origin)
        return panel
    }

    private func makePanel<Content: View>(size: NSSize, content: Content) -> NSPanel {
        let panel = NSPanel(contentRect: NSRect(origin: .zero, size: size), styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.level = .floating
        panel.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
        panel.isFloatingPanel = true
        panel.hidesOnDeactivate = false
        panel.becomesKeyOnlyIfNeeded = true
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.hasShadow = false  // the views draw their own, shaped to them
        panel.isReleasedWhenClosed = false
        panel.contentView = FirstClickHostingView(rootView: content)
        return panel
    }

    /// The panel beside the ball: to its left if there's room, else to its right, kept on the screen.
    private func place(_ picker: NSPanel) {
        guard let ball else { return }
        let screen = (ball.screen ?? NSScreen.main)?.visibleFrame ?? .zero
        var x = ball.frame.minX - Self.pickerSize.width + 6
        if x < screen.minX + 8 { x = ball.frame.maxX - 6 }
        var y = ball.frame.midY - Self.pickerSize.height / 2
        y = min(max(y, screen.minY + 8), screen.maxY - Self.pickerSize.height - 8)
        picker.setFrame(NSRect(origin: NSPoint(x: x, y: y), size: Self.pickerSize), display: true)
    }
}

/// A hosting view that takes the first click: the panels never become the active app, and a click on a control
/// there must act at once rather than first bring the window forward.
final class FirstClickHostingView<Content: View>: NSHostingView<Content> {
    override func acceptsFirstMouse(for event: NSEvent?) -> Bool { true }
}

/// Lets the main window be found again after the ball has hidden it.
struct MainWindowFinder: NSViewRepresentable {
    func makeNSView(context: Context) -> NSView {
        let view = NSView()
        DispatchQueue.main.async { FloatingMode.shared.mainWindow = view.window }
        return view
    }
    func updateNSView(_ view: NSView, context: Context) {
        if let window = view.window { FloatingMode.shared.mainWindow = window }
    }
}
