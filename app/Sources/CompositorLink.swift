import AppKit
import ApplicationServices
import Observation

/// What Compositor has open, so the project on screen there can be retouched with one click.
///
/// Compositor's windows are read through the Accessibility API, which needs the user's permission once (System
/// Settings › Privacy & Security › Accessibility). A window may tell its document's file (AXDocument), but Compositor
/// doesn't: its windows carry only a title, the project's name. So the title is matched against the projects
/// Compositor holds files of open (the kernel lists a process's open files to anyone of the same user: no permission,
/// under a millisecond) and against the projects it opened lately (macOS keeps that list, the same as its Open Recent
/// menu). A photo dropped into Compositor is an untitled document with no file until it is saved as a .comp, so
/// there is nothing to find then. Without the permission, the one project it holds open is still found; otherwise,
/// and with Compositor closed, the recent projects stand in.
@MainActor @Observable final class CompositorLink {
    enum Front: Equatable {
        case notInstalled
        case notRunning
        case noAccess           // running, but this app may not read its windows yet
        case noWindow           // running with no window open
        case untitled           // its front window holds a document not saved as a project
        case unlocated(String)  // its front window is named like a saved document, but no project of that name was found
        case project(URL)       // its front window's project
    }

    /// One of Compositor's windows: whether it is a document window, its document's file as the window tells it
    /// (Compositor tells none), and its title (the document's name).
    typealias Window = (isEditor: Bool, document: URL?, title: String?)

    private(set) var front: Front = .notRunning
    private(set) var recent: [URL] = []   // projects it opened lately, newest first, those still on disk
    /// Whether this app may read Compositor's windows. A project can be found without that (see `front(...)`), so
    /// `front` may show one while this is false; then switching tabs isn't followed and nothing can be saved there.
    private(set) var trusted = true
    /// Keep looking while this app isn't in front too (the floating ball sits over Compositor all the while).
    @ObservationIgnored var pollAlways = false
    /// Told when Compositor's front project changes (the queue's "add on switching").
    @ObservationIgnored var onFrontChange: ((Front) -> Void)?
    @ObservationIgnored private var reading = false
    @ObservationIgnored private var observers: [NSObjectProtocol] = []
    @ObservationIgnored private var timer: Timer?
    @ObservationIgnored private var recentStamp: Date?

    /// `watch`: follow Compositor as it launches, quits and changes projects (off for tests and snapshots).
    init(watch: Bool = true) {
        guard watch else { return }
        refresh()
        let center = NSWorkspace.shared.notificationCenter
        for name in [NSWorkspace.didLaunchApplicationNotification, NSWorkspace.didTerminateApplicationNotification,
                     NSWorkspace.didActivateApplicationNotification] {
            observers.append(center.addObserver(forName: name, object: nil, queue: .main) { [weak self] _ in
                Task { @MainActor in self?.refresh() }
            })
        }
        // Switching tabs or saving inside Compositor sends no notification: look again every two seconds, while
        // this app is in front (switching to it is itself a notification, so nothing is missed in between) or the
        // floating ball is out.
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in if NSApp?.isActive == true || self?.pollAlways == true { self?.refresh() } }
        }
    }

    /// For snapshots and tests: a given state, nothing read from the system.
    init(front: Front, recent: [URL], trusted: Bool = true) {
        self.front = front
        self.recent = recent
        self.trusted = trusted
    }

    var currentProject: URL? {
        if case .project(let url) = front { return url }
        return nil
    }

    /// The recent projects other than the one being edited (paths compared as files: one name comes from a window,
    /// the other from a bookmark, and /tmp and /private/tmp spell the same folder).
    var otherRecent: [URL] {
        let current = currentProject.map(Self.key)
        return recent.filter { Self.key($0) != current }
    }

    nonisolated static func key(_ url: URL) -> String { url.resolvingSymlinksInPath().standardizedFileURL.path }

    /// Looks at Compositor again. Its windows and open files are read off the main thread, every question to its
    /// windows with a short timeout: a Compositor busy reloading a big project must never freeze this app's window
    /// or its progress.
    func refresh() {
        refreshRecent()
        guard !reading else { return }
        let pid = NSRunningApplication.runningApplications(withBundleIdentifier: Compositor.bundleIdentifier).first?.processIdentifier
        let trusted = AXIsProcessTrusted()
        if trusted != self.trusted { self.trusted = trusted }
        let installed = Compositor.app != nil
        let recent = self.recent
        guard let pid else {
            apply(Self.front(installed: installed, running: false, trusted: trusted, windows: nil))
            return
        }
        reading = true
        Task { [weak self] in
            let (windows, open) = await Task.detached(priority: .utility) {
                (trusted ? Self.windowDocuments(of: pid) : nil, Self.openProjects(of: pid))
            }.value
            self?.reading = false
            // No answer within the timeouts means Compositor is busy (reloading a big project, say), not that the
            // permission is gone: keep what was known rather than flash the "allow access" state at the user.
            if trusted && windows == nil { return }
            self?.apply(Self.front(installed: installed, running: true, trusted: trusted, windows: windows, open: open, recent: recent))
        }
    }

    private func apply(_ next: Front) {
        guard next != front else { return }
        front = next
        onFrontChange?(next)
    }

    /// Asks macOS for the permission to read Compositor's windows: the system's own prompt, which offers to open
    /// the Accessibility settings. Only ever on the user's click.
    func requestAccess() { Self.requestAccess() }

    static func requestAccess() {
        let prompt = kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String
        _ = AXIsProcessTrustedWithOptions([prompt: true] as CFDictionary)
    }

    // MARK: saving before a retouch

    /// How saving Compositor's front project went (see `saveFront`).
    enum SaveOutcome: Equatable {
        case saved            // pressed, and the package was written
        case nothingToSave    // pressed, but nothing was written in the time a save takes: taken as nothing to save
        case otherProject     // Compositor's front window holds another project by now, so nothing was pressed
        case noMenu           // File › Save wasn't found in its menus
        case unreadable       // Compositor didn't answer (busy reloading, say), even on a second try
        case noAccess         // Compositor's windows can't be read (no permission, or not running)
    }

    /// Saves the project Compositor is editing, as ⌘S there would, so a retouch works from what is on screen and
    /// Compositor has no unsaved changes to weigh against the layers coming in. File › Save is found by its
    /// shortcut in Compositor's menu bar and pressed, only if the front window still shows `project` (it may have
    /// switched since the click); then the project's package is watched, for as long as a save of its size can
    /// take, until the manifest is rewritten and the package has gone quiet. The caller goes on whatever the
    /// outcome, with a line for the log; the script refuses to overwrite a save that lands later anyway.
    func saveFront(_ project: URL) async -> SaveOutcome {
        guard let pid = NSRunningApplication.runningApplications(withBundleIdentifier: Compositor.bundleIdentifier).first?.processIdentifier,
              AXIsProcessTrusted() else { return .noAccess }
        let manifest = project.appendingPathComponent("manifest.json")
        let before = Self.modificationDate(manifest)
        let recent = self.recent
        var pressed: SaveOutcome? = .unreadable
        for attempt in 0..<2 where pressed == .unreadable {  // busy for a moment (reloading a big project): once more
            if attempt > 0 { try? await Task.sleep(for: .milliseconds(400)) }
            pressed = await Task.detached(priority: .userInitiated) { () -> SaveOutcome? in
                guard let windows = Self.windowDocuments(of: pid) else { return .unreadable }
                let open = Self.openProjects(of: pid)
                guard case .project(let url) = Self.front(installed: true, running: true, trusted: true, windows: windows, open: open, recent: recent),
                      Self.key(url) == Self.key(project) else { return .otherProject }
                guard let (item, enabled) = Self.saveMenuItem(of: pid) else { return .noMenu }
                guard enabled else { return .nothingToSave }
                return AXUIElementPerformAction(item, kAXPressAction as CFString) == .success ? nil : .noMenu
            }.value
        }
        if let pressed { return pressed }
        // Compositor rewrites every layer's PNG on Save, about a second per 24 MP layer: wait in proportion.
        let limit = min(30.0, 4.0 + Double(Self.packageBytes(project)) / 50_000_000)
        let started = Date()
        while Date().timeIntervalSince(started) < limit {
            try? await Task.sleep(for: .milliseconds(100))
            if Self.modificationDate(manifest) != before {
                var last = Self.newestChange(in: project)  // written in one go, but let the package go quiet
                while true {
                    try? await Task.sleep(for: .milliseconds(500))
                    let now = Self.newestChange(in: project)
                    if now == last { break }
                    last = now
                }
                return .saved
            }
        }
        return .nothingToSave
    }

    nonisolated private static func modificationDate(_ url: URL) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }

    /// The size of a project's images, for how long its save may take.
    nonisolated private static func packageBytes(_ project: URL) -> Int {
        let files = (try? FileManager.default.contentsOfDirectory(at: project.appendingPathComponent("images"),
                                                                  includingPropertiesForKeys: [.fileSizeKey])) ?? []
        return files.compactMap { try? $0.resourceValues(forKeys: [.fileSizeKey]).fileSize }.reduce(0, +)
    }

    /// The latest modification date among a project's manifest and images.
    nonisolated private static func newestChange(in project: URL) -> Date? {
        let files = [project.appendingPathComponent("manifest.json")]
            + ((try? FileManager.default.contentsOfDirectory(at: project.appendingPathComponent("images"),
                                                             includingPropertiesForKeys: [.contentModificationDateKey])) ?? [])
        return files.compactMap { try? $0.resourceValues(forKeys: [.contentModificationDateKey]).contentModificationDate }.max()
    }

    /// File › Save in Compositor's menu bar, by its shortcut, and whether it is enabled.
    nonisolated private static func saveMenuItem(of pid: pid_t) -> (AXUIElement, Bool)? {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.3)
        guard let bar = element(app, kAXMenuBarAttribute), let menus: [AXUIElement] = attribute(bar, kAXChildrenAttribute) else { return nil }
        for menu in menus {  // Compositor, File, Edit...: each holds one AXMenu of items
            guard let submenus: [AXUIElement] = attribute(menu, kAXChildrenAttribute) else { continue }
            for submenu in submenus {
                guard let items: [AXUIElement] = attribute(submenu, kAXChildrenAttribute) else { continue }
                for item in items {
                    let char: String? = attribute(item, kAXMenuItemCmdCharAttribute)
                    let modifiers: Int? = attribute(item, kAXMenuItemCmdModifiersAttribute)
                    if isSaveItem(char: char, modifiers: modifiers) {
                        let enabled: Bool? = attribute(item, kAXEnabledAttribute)
                        return (item, enabled ?? true)
                    }
                }
            }
        }
        return nil
    }

    /// File › Save is the menu item with ⌘S and no other modifier (⇧⌘S is Save As, ⌥⌘S Save a Copy, ⌃⌘S
    /// something else), whatever language the menu is in.
    nonisolated static func isSaveItem(char: String?, modifiers: Int?) -> Bool {
        char?.uppercased() == "S" && (modifiers ?? 0) == 0
    }

    // MARK: reading

    /// The state from what was found. `windows` lists Compositor's windows front first (nil when they couldn't be
    /// read), `open` the projects it holds files of, `recent` the ones it opened lately, newest first.
    ///
    /// The front document window's file, when the window tells it, settles it. Otherwise the window's title (the
    /// document's name) is matched against `open`, then `recent`; a title an untitled document would have (or a
    /// photo's name: a photo dropped in, not saved as a project yet) means an untitled document; failing that, the
    /// one project held open is taken to be the one in front, and with several or none, the title is reported as
    /// unlocated. Without permission to read the windows, the one project held open is still the answer.
    nonisolated static func front(installed: Bool, running: Bool, trusted: Bool, windows: [Window]?,
                                  open: [URL] = [], recent: [URL] = []) -> Front {
        guard running else { return installed ? .notRunning : .notInstalled }
        guard trusted, let windows else { return open.count == 1 ? .project(open[0]) : .noAccess }
        guard let editor = windows.first(where: \.isEditor) else {
            // no ordinary window found (an unusual subrole): any window showing a project will do
            return windows.first { isProject($0.document) }?.document.map(Front.project) ?? .noWindow
        }
        if let document = editor.document { return isProject(document) ? .project(document) : .untitled }
        return locate(title: editor.title, open: open, recent: recent)
    }

    nonisolated private static func isProject(_ url: URL?) -> Bool { url?.pathExtension.lowercased() == "comp" }

    /// The project a window without a document file shows, from its title (see `front`).
    nonisolated static func locate(title: String?, open: [URL], recent: [URL]) -> Front {
        guard let title, !title.isEmpty else { return open.count == 1 ? .project(open[0]) : .untitled }
        for name in titleCandidates(title) {
            if let found = open.first(where: { named(name, $0) }) ?? recent.first(where: { named(name, $0) }) { return .project(found) }
        }
        if looksUntitled(title) { return .untitled }
        return open.count == 1 ? .project(open[0]) : .unlocated(title)
    }

    /// A window's title as it is, then without a trailing " — Edited"-style part (the last one first).
    nonisolated static func titleCandidates(_ title: String) -> [String] {
        var candidates = [title.trimmingCharacters(in: .whitespaces)]
        for separator in [" — ", " – ", " - "] {
            if let range = title.range(of: separator, options: .backwards) {
                let head = title[..<range.lowerBound].trimmingCharacters(in: .whitespaces)
                if !head.isEmpty, !candidates.contains(head) { candidates.append(head) }
            }
        }
        return candidates
    }

    /// Whether a project is called `name`: its file name with or without the extension, case and composed/decomposed
    /// spelling aside (a title comes composed, a file name may not).
    nonisolated static func named(_ name: String, _ project: URL) -> Bool {
        [project.deletingPathExtension().lastPathComponent, project.lastPathComponent].contains {
            $0.compare(name, options: [.caseInsensitive, .widthInsensitive]) == .orderedSame
        }
    }

    /// Whether a window title is an untitled document's ("未命名 2", "Untitled") or a photo's name (a photo dropped
    /// in, not saved as a project yet).
    nonisolated static func looksUntitled(_ title: String?) -> Bool {
        guard let title else { return true }
        let trimmed = title.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { return true }
        if photoExtensions.contains((trimmed as NSString).pathExtension.lowercased()) { return true }
        let stem = trimmed.lowercased().trimmingCharacters(in: CharacterSet.decimalDigits.union(.whitespaces).union(CharacterSet(charactersIn: "-_()")))
        return untitledNames.contains(stem)
    }

    nonisolated private static let untitledNames: Set<String> = [
        "untitled", "untitled document", "未命名", "未命名文稿", "未命名文档", "无标题", "無標題", "sans titre", "ohne titel",
        "senza titolo", "sin título", "sem título", "名称未設定", "無題", "제목 없음", "без названия", "naamloos",
    ]
    nonisolated private static let photoExtensions: Set<String> = [
        "jpg", "jpeg", "png", "tif", "tiff", "heic", "heif", "dng", "cr2", "cr3", "nef", "arw", "raf", "orf", "rw2", "psd", "webp", "avif", "exr", "bmp", "gif",
    ]

    /// A window's document as a file URL: a file URL as it is (percent-encoded or not), a plain path as a file.
    nonisolated static func documentURL(_ value: String?) -> URL? {
        guard let value = value?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        if value.hasPrefix("/") { return URL(fileURLWithPath: value) }
        if value.hasPrefix("~") { return URL(fileURLWithPath: (value as NSString).expandingTildeInPath) }
        if let url = URL(string: value) { return url.isFileURL ? url.standardizedFileURL : nil }
        guard value.lowercased().hasPrefix("file:") else { return nil }
        var path = String(value.dropFirst(5))  // "///a b.comp" or "//localhost/a b.comp": spaces unescaped
        if path.hasPrefix("//") {
            path.removeFirst(2)
            if !path.hasPrefix("/") { path = String(path.drop { $0 != "/" }) }
        }
        return URL(fileURLWithPath: path.removingPercentEncoding ?? path)
    }

    /// Compositor's windows, front (main) first.
    nonisolated private static func windowDocuments(of pid: pid_t) -> [Window]? {
        let app = AXUIElementCreateApplication(pid)
        AXUIElementSetMessagingTimeout(app, 0.3)  // a timeout applies to the element it is set on: each gets one
        guard var windows: [AXUIElement] = attribute(app, kAXWindowsAttribute) else { return nil }
        if let main: AXUIElement = element(app, kAXMainWindowAttribute) ?? element(app, kAXFocusedWindowAttribute),
           let index = windows.firstIndex(where: { CFEqual($0, main) }) {
            windows.insert(windows.remove(at: index), at: 0)
        }
        return windows.map { window in
            AXUIElementSetMessagingTimeout(window, 0.3)
            let subrole: String? = attribute(window, kAXSubroleAttribute)
            let title: String? = attribute(window, kAXTitleAttribute)
            let document: CFTypeRef? = attribute(window, kAXDocumentAttribute)  // a string by the book, a URL from some apps
            let url = (document as? URL) ?? documentURL(document as? String)
            return (subrole == kAXStandardWindowSubrole as String, url, title)
        }
    }

    // MARK: open files

    /// The projects Compositor holds files of open (it watches each open project's package for changes), from the
    /// kernel's list of its file descriptors. Empty when it holds none, or when the list can't be read.
    nonisolated static func openProjects(of pid: pid_t) -> [URL] { projects(among: openFiles(of: pid)) }

    /// The paths of the files a process has open.
    nonisolated static func openFiles(of pid: pid_t) -> [String] {
        let stride = MemoryLayout<proc_fdinfo>.stride
        let bytes = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, nil, 0)
        guard bytes > 0 else { return [] }  // not running, or another user's
        var list = [proc_fdinfo](repeating: proc_fdinfo(), count: Int(bytes) / stride + 64)  // files open and close in between
        let got = proc_pidinfo(pid, PROC_PIDLISTFDS, 0, &list, Int32(list.count * stride))
        guard got > 0 else { return [] }
        var paths: [String] = []
        for info in list.prefix(Int(got) / stride) where info.proc_fdtype == UInt32(PROX_FDTYPE_VNODE) {
            var vnode = vnode_fdinfowithpath()
            let size = Int32(MemoryLayout<vnode_fdinfowithpath>.size)
            guard proc_pidfdinfo(pid, info.proc_fd, PROC_PIDFDVNODEPATHINFO, &vnode, size) == size else { continue }
            let path = withUnsafePointer(to: &vnode.pvip.vip_path) {
                $0.withMemoryRebound(to: CChar.self, capacity: Int(MAXPATHLEN)) { String(cString: $0) }
            }
            if !path.isEmpty { paths.append(path) }
        }
        return paths
    }

    /// The projects among file paths (a file inside a package stands for the package), each once, those on disk.
    nonisolated static func projects(among paths: [String]) -> [URL] {
        var seen = Set<String>()
        var projects: [URL] = []
        for path in paths {
            let components = URL(fileURLWithPath: path).pathComponents
            guard let index = components.firstIndex(where: { ($0 as NSString).pathExtension.lowercased() == "comp" }) else { continue }
            let project = URL(fileURLWithPath: NSString.path(withComponents: Array(components[...index])), isDirectory: true)
            guard FileManager.default.fileExists(atPath: project.appendingPathComponent("manifest.json").path),
                  seen.insert(key(project)).inserted else { continue }
            projects.append(project)
        }
        return projects
    }

    nonisolated private static func attribute<T>(_ element: AXUIElement, _ name: String) -> T? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success else { return nil }
        return value as? T
    }

    nonisolated private static func element(_ parent: AXUIElement, _ name: String) -> AXUIElement? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(parent, name as CFString, &value) == .success, let value,
              CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return (value as! AXUIElement)
    }

    // MARK: recent projects

    /// Where macOS keeps Compositor's recent documents (File › Open Recent): .sfl4 on macOS 26, older names before.
    nonisolated static var recentFiles: [URL] {
        let folder = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(
            "Library/Application Support/com.apple.sharedfilelist/com.apple.LSSharedFileList.ApplicationRecentDocuments")
        return ["sfl4", "sfl3", "sfl2"].map { folder.appendingPathComponent("\(Compositor.bundleIdentifier).\($0)") }
    }

    private func refreshRecent() {
        let file = Self.recentFiles.first { FileManager.default.fileExists(atPath: $0.path) }
        let stamp = file.flatMap { (try? FileManager.default.attributesOfItem(atPath: $0.path))?[.modificationDate] as? Date }
        guard stamp != recentStamp || file == nil else { return }
        recentStamp = stamp
        let projects = file.map(Self.recentProjects) ?? []
        if projects != recent { recent = projects }
    }

    /// The .comp projects in a recent-documents list, newest first, those still on disk.
    nonisolated static func recentProjects(in file: URL) -> [URL] {
        guard let data = try? Data(contentsOf: file), let unarchiver = try? NSKeyedUnarchiver(forReadingFrom: data) else { return [] }
        let root = unarchiver.decodeObject(of: [NSDictionary.self, NSArray.self, NSString.self, NSData.self, NSUUID.self,
                                                NSNumber.self, NSDate.self], forKey: NSKeyedArchiveRootObjectKey)
        let items = ((root as? [String: Any])?["items"] as? [[String: Any]]) ?? []
        var seen = Set<String>()
        return items.compactMap { item -> URL? in
            guard let bookmark = item["Bookmark"] as? Data else { return nil }
            var stale = false
            guard let url = try? URL(resolvingBookmarkData: bookmark, options: [.withoutUI, .withoutMounting],
                                     relativeTo: nil, bookmarkDataIsStale: &stale),
                  url.pathExtension.lowercased() == "comp", FileManager.default.fileExists(atPath: url.path),
                  seen.insert(key(url)).inserted else { return nil }
            return url
        }
    }
}
