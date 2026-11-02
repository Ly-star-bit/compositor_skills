import AppKit
import SwiftUI

@main
struct RetouchStudioApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @State private var studio = Studio(environment: PythonEnvironment(), compositor: CompositorLink(), remembers: true)

    var body: some Scene {
        Window("Compositor 工具", id: "main") {
            RootView(studio: studio)
                .task {
                    delegate.studio = studio
                    await studio.environment.check()
                    // back as the floating ball if that's how the app was left; if the last launch went that way and
                    // didn't last (the flag below is still set), stay in the window this time
                    let defaults = UserDefaults.standard
                    let starting = FloatingMode.defaultsKey + ".starting"
                    if defaults.bool(forKey: starting) {
                        defaults.set(false, forKey: FloatingMode.defaultsKey)
                        defaults.set(false, forKey: starting)
                    } else if studio.environment.state == .ready && defaults.bool(forKey: FloatingMode.defaultsKey) {
                        defaults.set(true, forKey: starting)
                        FloatingMode.shared.enter(studio)
                        try? await Task.sleep(for: .seconds(6))
                        defaults.set(false, forKey: starting)
                    }
                }
        }
        .defaultSize(width: 1360, height: 860)
        .windowToolbarStyle(.unified)
        .commands {
            CommandGroup(replacing: .newItem) {
                Button("添加照片或工程…") { FilePicker.choose(into: studio) }.keyboardShortcut("o")
            }
        }
        Settings {
            SettingsView(environment: studio.environment)
        }
    }
}

/// Photos and projects dropped on the Dock icon or opened with the app join the queue; quitting stops a run the way
/// Ctrl-C does, so the photo being worked on leaves no half-written project.
final class AppDelegate: NSObject, NSApplicationDelegate {
    @MainActor var studio: Studio? {
        didSet { if let studio, !waiting.isEmpty { studio.add(waiting); waiting = [] } }
    }
    @MainActor private var waiting: [URL] = []

    @MainActor func application(_ application: NSApplication, open urls: [URL]) {
        if let studio { studio.add(urls) } else { waiting += urls }
    }

    /// Closing the window quits, except while the app is the floating ball (its window is only hidden then).
    @MainActor func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { !FloatingMode.shared.isOn }

    /// The Dock icon, clicked while the app is the floating ball: the full window again.
    @MainActor func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if FloatingMode.shared.isOn { FloatingMode.shared.leave(); return false }
        return true
    }

    @MainActor func applicationWillTerminate(_ notification: Notification) { studio?.stop() }
}
