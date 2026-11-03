import AppKit
import Foundation
import Observation

// The app's working parts: a private Python environment for retouch_builder.py, and a queue that runs the script on
// each photo or project in turn. No retouching happens in Swift; the script does it all, as from the command line.

/// Runs a command, handing over its output a line at a time and in order; `interrupt` stops it the way Ctrl-C does,
/// so the script can undo what it had half written.
final class CommandRun: @unchecked Sendable {
    private let process = Process()

    init(_ executable: URL, _ arguments: [String], environment: [String: String] = [:]) {
        process.executableURL = executable
        process.arguments = arguments
        var env = ProcessInfo.processInfo.environment
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        for (key, value) in environment { env[key] = value }
        process.environment = env
    }

    /// The exit status, once the command has ended and `line` (called on the main actor) has seen every line it
    /// printed, the last one included: a failure's reason is usually that last line.
    func run(_ line: @escaping @MainActor (String) -> Void) async -> Int32 {
        let pipe = Pipe()
        process.standardOutput = pipe
        process.standardError = pipe
        process.standardInput = FileHandle.nullDevice
        // The exit status comes from the termination handler, set before the launch. (Not waitUntilExit: from a
        // background task's thread it can wait for ever on a command long gone, the photo stuck at 处理中.)
        let exit = AsyncStream<Int32> { continuation in
            process.terminationHandler = { process in
                continuation.yield(process.terminationStatus)
                continuation.finish()
            }
        }
        do { try process.run() } catch {
            await line("[Error] \(error.localizedDescription)")
            return -1
        }
        let reader = pipe.fileHandleForReading
        let lines = AsyncStream<String> { continuation in
            Thread.detachNewThread {
                var buffer = Data()
                while true {
                    let data = reader.availableData  // blocks until there is output; empty once the command is done
                    if data.isEmpty { break }
                    buffer.append(data)
                    while let newline = buffer.firstIndex(of: 0x0A) {
                        continuation.yield(String(decoding: buffer[buffer.startIndex..<newline], as: UTF8.self))
                        buffer.removeSubrange(buffer.startIndex...newline)
                    }
                }
                if !buffer.isEmpty { continuation.yield(String(decoding: buffer, as: UTF8.self)) }
                continuation.finish()
            }
        }
        for await text in lines { await line(text) }
        for await status in exit { return status }
        return process.terminationStatus
    }

    func interrupt() { if process.isRunning { process.interrupt() } }
}

/// Where the app keeps its Python environment, and how it gets one.
@MainActor @Observable final class PythonEnvironment {
    enum State: Equatable {
        case checking
        case missing
        case installing(String, Double)
        case ready
        case failed(String)
    }

    var state: State = .checking
    var log = ""
    var interpreterDescription = ""
    var hasSegmentationModel = false
    let support: URL

    init(support: URL = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("RetouchStudio")) {
        self.support = support
    }

    var venv: URL { support.appendingPathComponent("venv") }
    var python: URL { venv.appendingPathComponent("bin/python3") }
    var logFile: URL { support.appendingPathComponent("install.log") }

    /// The Python to build the environment from: Homebrew's first (newest, and the segmentation runtime installs
    /// there), then the one macOS ships with the developer tools.
    static func interpreter() -> URL? {
        ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
            .map(URL.init(fileURLWithPath:))
            .first { FileManager.default.isExecutableFile(atPath: $0.path) }
    }

    func check() async {
        state = .checking
        guard FileManager.default.isExecutableFile(atPath: python.path) else { state = .missing; return }
        var output = ""
        let status = await CommandRun(python, ["-c", "import sys, numpy, cv2, PIL; print(sys.version.split()[0])"])
            .run { output += $0 + "\n" }
        guard status == 0 else { state = .missing; return }
        interpreterDescription = "Python " + output.trimmingCharacters(in: .whitespacesAndNewlines)
        hasSegmentationModel = await CommandRun(python, ["-c", "import ai_edge_litert"]).run { _ in } == 0
        state = .ready
    }

    /// Builds the environment: a virtual environment, then the script's packages from the internet (once).
    func install(fresh: Bool = false) async {
        log = ""
        try? FileManager.default.createDirectory(at: support, withIntermediateDirectories: true)
        if fresh { try? FileManager.default.removeItem(at: venv) }
        guard let base = Self.interpreter() else {
            state = .failed("没有找到 Python 3。请先安装 Xcode 命令行工具（终端里运行 xcode-select --install）。")
            return
        }
        let steps: [(String, Double, URL, [String], Bool)] = [
            ("创建独立的 Python 环境", 0.1, base, ["-m", "venv", venv.path], true),
            ("更新安装工具", 0.25, python, ["-m", "pip", "install", "--disable-pip-version-check", "-q", "--upgrade", "pip"], false),
            ("下载 NumPy、OpenCV、Pillow", 0.45,
             python, ["-m", "pip", "install", "--disable-pip-version-check", "-q", "numpy", "pillow", "opencv-python-headless<5"], true),
            ("下载人像分割运行库（可选）", 0.8,
             python, ["-m", "pip", "install", "--disable-pip-version-check", "-q", "ai-edge-litert"], false),
        ]
        for (title, fraction, executable, arguments, required) in steps {
            state = .installing(title, fraction)
            append("== \(title)")
            let status = await CommandRun(executable, arguments).run { [weak self] line in self?.append(line) }
            if status != 0 {
                append("(exit \(status))")
                if required {
                    state = .failed("「\(title)」没有完成。请检查网络后重试，详细记录在安装日志里。")
                    return
                }
            }
        }
        await check()
        if state != .ready { state = .failed("环境装好了，但检查没有通过。详细记录在安装日志里。") }
    }

    private func append(_ line: String) {
        log += line + "\n"
        try? log.write(to: logFile, atomically: true, encoding: .utf8)
    }
}

/// The script the app runs: the skill folder's own, so edits to the skill take effect at once, or the copy inside
/// the app when that folder has gone.
enum Script {
    /// The tests' script, ahead of the rest.
    static var override: URL?

    static var url: URL? {
        if let override { return override }
        if let folder = Bundle.main.object(forInfoDictionaryKey: "RetouchSkillScripts") as? String {
            let script = URL(fileURLWithPath: folder).appendingPathComponent("retouch_builder.py")
            if FileManager.default.fileExists(atPath: script.path) { return script }
        }
        return Bundle.main.url(forResource: "retouch_builder", withExtension: "py", subdirectory: "skill/scripts")
    }
    static var isBundledCopy: Bool { url?.path.hasPrefix(Bundle.main.bundlePath) ?? false }

    /// photocraft-retouch's script: in the skill folder next to retouch_builder.py's (photocraft-retouch/scripts), or
    /// the copy inside the app. It runs retouch_builder.py for the layers, the one `url` finds (RETOUCH_BUILDER).
    static var photocraftOverride: URL?
    static var photocraftURL: URL? {
        if let photocraftOverride { return photocraftOverride }
        if let builder = url {
            let script = builder.deletingLastPathComponent().deletingLastPathComponent()
                .appendingPathComponent("photocraft-retouch/scripts/pcraft_retouch.py")
            if FileManager.default.fileExists(atPath: script.path) { return script }
        }
        return Bundle.main.url(forResource: "pcraft_retouch", withExtension: "py", subdirectory: "skill/photocraft-retouch/scripts")
    }

    static func url(for target: Target) -> URL? { target == .compositor ? url : photocraftURL }

    static func environment(for target: Target) -> [String: String] {
        var values = environment
        if target == .photocraft, let builder = url { values["RETOUCH_BUILDER"] = builder.path }
        return values
    }

    /// What the app ships for the script, so the first photo doesn't wait for them and no compiler is needed: the
    /// Apple Vision face helper, built from the face_landmarks.swift beside the script (passed only while the
    /// script in use sits next to that same source), and the segmentation model.
    static var environment: [String: String] {
        var values: [String: String] = [:]
        let bundled = Bundle.main.url(forResource: "face_landmarks", withExtension: "swift", subdirectory: "skill/scripts")
        let beside = url?.deletingLastPathComponent().appendingPathComponent("face_landmarks.swift")
        if let helper = Bundle.main.url(forAuxiliaryExecutable: "face_landmarks"), let bundled, let beside,
           let a = try? Data(contentsOf: bundled), let b = try? Data(contentsOf: beside), a == b {
            values["RETOUCH_VISION_HELPER"] = helper.path
        }
        if let model = Bundle.main.url(forResource: "selfie_multiclass_256x256", withExtension: "tflite", subdirectory: "models") {
            values["RETOUCH_PARSING_MODEL"] = model.path
        }
        return values
    }
}

enum Compositor {
    static let bundleIdentifier = "com.wonderassembly.compositor"
    static var app: URL? { NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleIdentifier) }

    /// Opens projects in Compositor (only ever on the user's click).
    static func open(_ projects: [URL]) {
        guard let app, !projects.isEmpty else { return }
        NSWorkspace.shared.open(projects, withApplicationAt: app, configuration: NSWorkspace.OpenConfiguration())
    }
}

/// PhotoCraft, the open-source editor the .pcraft documents are for. Unlike Compositor it doesn't watch its files, so
/// a document changed while open there is reloaded with File › Revert, unless PhotoCraft was started linked (below);
/// and it is only ever opened or started on the user's click.
enum PhotoCraftApp {
    static let bundleIdentifier = "ai.storyteller.photocraft"
    static let cliVersion = "0.19.0"  // photocraft-retouch/scripts/photocraft.py VERSION

    // MARK: the link: PhotoCraft started with its control port, so pcraft_retouch.py saves a document open there
    // before adding to it and loads it again after (photocraft.py AppLink; it reads linkFile)

    static let linkPort = 47_917
    static var linkFolder: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("compositor-retouch")
    }
    static var linkFile: URL { linkFolder.appendingPathComponent("photocraft-link.json") }
    /// PhotoCraft writes a fresh token here the first time (readable by this user only) and reuses it after.
    static var tokenFile: URL { linkFolder.appendingPathComponent("photocraft-control.token") }

    /// What the script reads from linkFile: the port, the token's file, and the folder automation may use.
    static func linkSettings(root: URL) -> [String: Any] {
        ["port": linkPort, "tokenFile": tokenFile.path, "root": root.path]
    }

    /// PhotoCraft's own launch options for the link: its control port on this Mac only, the token file, and `root`
    /// (the home folder) as the one folder its automation reads and writes, where the documents are.
    static func linkArguments(root: URL) -> [String] {
        ["--control", String(linkPort), "--control-token-file", tokenFile.path,
         "--automation-read-root", root.path, "--automation-write-root", root.path]
    }

    static var isRunning: Bool { !NSRunningApplication.runningApplications(withBundleIdentifier: bundleIdentifier).isEmpty }

    /// Starts PhotoCraft linked, after writing linkFile. A PhotoCraft already running keeps how it was started, so this
    /// is for when it isn't.
    static func startLinked(root: URL = FileManager.default.homeDirectoryForCurrentUser) throws {
        guard let app = app(toOpen: URL(fileURLWithPath: "/document.pcraft")) else { throw CocoaError(.fileNoSuchFile) }
        try FileManager.default.createDirectory(at: linkFolder, withIntermediateDirectories: true)
        try JSONSerialization.data(withJSONObject: linkSettings(root: root), options: [.prettyPrinted, .sortedKeys])
            .write(to: linkFile, options: .atomic)
        let configuration = NSWorkspace.OpenConfiguration()
        configuration.arguments = linkArguments(root: root)
        NSWorkspace.shared.openApplication(at: app, configuration: configuration)
    }

    /// Whether something accepts connections on the link's port now: PhotoCraft, started linked.
    static func isListening(port: Int = linkPort) -> Bool {
        let socket = Darwin.socket(AF_INET, SOCK_STREAM, 0)
        guard socket >= 0 else { return false }
        defer { close(socket) }
        var address = sockaddr_in()
        address.sin_len = UInt8(MemoryLayout<sockaddr_in>.size)
        address.sin_family = sa_family_t(AF_INET)
        address.sin_port = in_port_t(UInt16(port).bigEndian)
        address.sin_addr.s_addr = inet_addr("127.0.0.1")
        return withUnsafePointer(to: &address) {
            $0.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(socket, $0, socklen_t(MemoryLayout<sockaddr_in>.size)) }
        } == 0
    }

    /// For the snapshot tool, which draws the card as on a Mac with PhotoCraft.
    nonisolated(unsafe) static var appOverride: URL?

    /// The app that opens `document`, when it is PhotoCraft.
    static func app(toOpen document: URL) -> URL? {
        if let appOverride { return appOverride }
        return [NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleIdentifier),
         NSWorkspace.shared.urlForApplication(toOpen: document)]
            .compactMap { $0 }.first { $0.lastPathComponent.localizedCaseInsensitiveContains("photocraft") }
    }

    static func open(_ documents: [URL]) {
        guard let first = documents.first, let app = app(toOpen: first) else { return }
        NSWorkspace.shared.open(documents, withApplicationAt: app, configuration: NSWorkspace.OpenConfiguration())
    }

    /// The engine pcraft_retouch.py will use when it is on this Mac already, as the script looks for it
    /// (PHOTOCRAFT_CLI, PATH, its cache); without one the first PhotoCraft run downloads it.
    static var cli: URL? {
        let fm = FileManager.default
        if let path = ProcessInfo.processInfo.environment["PHOTOCRAFT_CLI"], fm.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path)
        }
        let cache = fm.urls(for: .cachesDirectory, in: .userDomainMask)[0].appendingPathComponent(
            "photocraft-retouch/photocraft-cli-\(cliVersion)/photocraft-cli-\(cliVersion)-macos-universal/photocraft-cli")
        let candidates = ((ProcessInfo.processInfo.environment["PATH"] ?? "").split(separator: ":").map(String.init)
                          + ["/opt/homebrew/bin", "/usr/local/bin"]).map { URL(fileURLWithPath: $0).appendingPathComponent("photocraft-cli") }
        return (candidates + [cache]).first { fm.isExecutableFile(atPath: $0.path) }
    }
}

/// The queue: photos and projects, the settings for them, and the run through them.
@MainActor @Observable final class Studio {
    var jobs: [RetouchJob] = []
    var selection: RetouchJob.ID?
    /// The tab: which editor photos added now are retouched for, and whose queue the window shows. Remembered when
    /// `remembers`. Documents keep their own (a .comp is Compositor's, a .pcraft PhotoCraft's).
    var target: Target = .compositor {
        didSet {
            if remembers && target != oldValue { UserDefaults.standard.set(target.rawValue, forKey: "target") }
            if selectedJob.map({ $0.target != target }) ?? true { selection = visibleJobs.first?.id }
        }
    }
    /// The queue as the current tab shows it.
    var visibleJobs: [RetouchJob] { jobs.filter { $0.target == target } }
    /// Kept across launches when `remembers` (the app; not the tests): every change is written out.
    var settings = RetouchSettings() {
        didSet { if remembers && settings != oldValue { settings.save() } }
    }
    let remembers: Bool
    var showInspector = true
    /// Compositor's photos join the queue by themselves as they come to the front there.
    var autoQueue = UserDefaults.standard.bool(forKey: "autoQueue") {
        didSet { UserDefaults.standard.set(autoQueue, forKey: "autoQueue") }
    }
    /// Before a project open in Compositor is retouched, it is saved there first (File › Save pressed through the
    /// Accessibility API), so the layers are computed from what is on screen and nothing is lost to a "keep
    /// mine" answer. On unless switched off.
    var autoSave = UserDefaults.standard.object(forKey: "autoSave") as? Bool ?? true {
        didSet { UserDefaults.standard.set(autoSave, forKey: "autoSave") }
    }
    var isRunning = false
    var current: RetouchJob.ID?
    var batch: [RetouchJob.ID] = []   // the jobs this run started with
    let environment: PythonEnvironment
    let compositor: CompositorLink
    private var run: CommandRun?
    private var stopRequested = false

    init(environment: PythonEnvironment, compositor: CompositorLink, remembers: Bool = false) {
        self.environment = environment
        self.compositor = compositor
        self.remembers = remembers
        if remembers {
            settings = RetouchSettings.load()
            if let raw = UserDefaults.standard.string(forKey: "target"), let saved = Target(rawValue: raw) { target = saved }
        }
        compositor.onFrontChange = { [weak self] front in self?.compositorChanged(front) }
    }

    /// Compositor brought another project to the front. With 自动加入队列 on, one not in the queue yet joins it;
    /// one already there (done included) is left alone, since running again would stack a second set of layers.
    func compositorChanged(_ front: CompositorLink.Front) {
        followSmoothing(front)
        guard autoQueue, case .project(let url) = front, environment.state == .ready,
              Arguments.problem(with: settings, for: RetouchJob(source: url)) == nil,
              !jobs.contains(where: { CompositorLink.key($0.source) == CompositorLink.key(url) }) else { return }
        retouchNow(url)
    }

    /// The jobs still to do, the one being worked on included.
    var pendingCount: Int { jobs.filter { $0.status == .waiting || $0.status == .running }.count }

    /// A waiting job's place in line (1 = next).
    func place(of id: RetouchJob.ID) -> Int? {
        jobs.filter { $0.status == .waiting }.firstIndex { $0.id == id }.map { $0 + 1 }
    }

    /// Without Compositor (tests): a link that never finds it.
    convenience init(environment: PythonEnvironment) {
        self.init(environment: environment, compositor: CompositorLink(front: .notInstalled, recent: []))
    }

    var selectedJob: RetouchJob? { jobs.first { $0.id == selection } }
    var pending: [RetouchJob] { jobs.filter { !$0.status.isFinished && $0.status != .running } }
    var finishedCount: Int { jobs.filter { $0.status.isFinished }.count }
    var currentJob: RetouchJob? { jobs.first { $0.id == current } }

    /// How many of this run's jobs are through, and how far the run is, counting the photo being worked on.
    var batchFinished: Int { jobs.filter { batch.contains($0.id) && $0.status.isFinished }.count }
    var progress: Double {
        guard !batch.isEmpty else { return 0 }
        return min(1, (Double(batchFinished) + (currentJob?.fraction ?? 0)) / Double(batch.count))
    }

    /// Adds photos and projects (anything else is left out), each once per editor. A photo is retouched for the tab
    /// it joins in, a document for its own editor; when none of them belongs to the tab, the window turns to theirs.
    func add(_ urls: [URL]) {
        var known = Set(jobs.map { "\($0.target.rawValue):\($0.source.standardizedFileURL.path)" })
        var new: [RetouchJob] = []
        for url in urls.map(\.standardizedFileURL) where RetouchJob.accepts(url) {
            var job = RetouchJob(source: url)
            job.target = Target.of(document: url) ?? target
            if known.insert("\(job.target.rawValue):\(url.path)").inserted { new.append(job) }
        }
        jobs += new
        if let first = new.first, !new.contains(where: { $0.target == target }) { target = first.target }
        if selection == nil { selection = visibleJobs.first?.id ?? jobs.first?.id }
    }

    func remove(_ id: RetouchJob.ID) {
        guard jobs.first(where: { $0.id == id })?.status != .running else { return }
        jobs.removeAll { $0.id == id }
        if selection == id { selection = visibleJobs.first?.id }
    }

    func clearFinished() {
        jobs.removeAll { $0.status.isFinished }
        if selectedJob == nil { selection = visibleJobs.first?.id }
    }

    /// Retouches a project now with the current settings, as Compositor's 精修 buttons do: queued (again, if it ran
    /// before, which adds another set of layers) and selected, and the queue started unless it is running already.
    func retouchNow(_ url: URL) { queueNow(url, refresh: false) }

    /// 重算: recomputes a project's snapshot layers (smoothing, eye brilliance) from the project as it is now, after
    /// manual work under them, instead of adding layers; queued the same way.
    func refreshNow(_ url: URL) { queueNow(url, refresh: true) }

    private func queueNow(_ url: URL, refresh: Bool) {
        let target = CompositorLink.key(url)
        if let index = jobs.firstIndex(where: { CompositorLink.key($0.source) == target }) {
            if jobs[index].status != .running && jobs[index].status != .waiting {
                jobs[index].refresh = refresh
                retry(jobs[index].id)
            }
            selection = jobs[index].id
        } else {
            add([url])
            if let index = jobs.firstIndex(where: { CompositorLink.key($0.source) == target }) {
                jobs[index].refresh = refresh
                selection = jobs[index].id
            }
        }
        if !isRunning { Task { await start() } }
    }

    /// Puts one back in line, to run again with the current settings (a finished photo makes a second project).
    func retry(_ id: RetouchJob.ID) {
        guard let index = jobs.firstIndex(where: { $0.id == id }), jobs[index].status != .running else { return }
        jobs[index].status = .waiting
        jobs[index].step = ""
    }

    /// Runs again: failed and cancelled ones get another go.
    func requeueUnfinished() {
        for index in jobs.indices where jobs[index].status != .done && jobs[index].status != .running {
            jobs[index].status = .waiting
        }
    }

    func start() async {
        guard !isRunning, let script = Script.url, environment.state == .ready else { return }
        isRunning = true
        stopRequested = false
        batch = jobs.filter { $0.status == .waiting }.map(\.id)
        defer { isRunning = false; current = nil; run = nil }
        while !stopRequested, let index = jobs.firstIndex(where: { $0.status == .waiting }) {
            guard let script = jobs[index].target == .compositor ? script : Script.photocraftURL else {
                jobs[index].status = .failed("找不到 PhotoCraft 版的修图脚本（photocraft-retouch/scripts/pcraft_retouch.py）")
                continue
            }
            await process(index, script: script)
        }
    }

    func stop() {
        stopRequested = true
        run?.interrupt()
    }

    private func process(_ index: Int, script: URL) async {
        let settings = self.settings
        var job = jobs[index]
        if let problem = Arguments.problem(with: settings, for: job) {
            jobs[index].status = .failed(problem)
            return
        }
        let id = job.id
        current = id
        if selection == nil { selection = id }
        job.status = .running
        job.log = ""
        job.lastError = nil
        job.warnings = []
        job.step = "准备中"
        job.fraction = 0
        jobs[index] = job
        let started = Date()

        var input = job.source
        let fm = FileManager.default
        if ["heic", "heif"].contains(job.source.pathExtension.lowercased()) {  // the script reads JPEG, PNG, TIFF
            update(id) { $0.step = "转换 HEIC" }
            let converted = fm.temporaryDirectory.appendingPathComponent(UUID().uuidString + ".tif")
            _ = await CommandRun(URL(fileURLWithPath: "/usr/bin/sips"),
                                 ["-s", "format", "tiff", job.source.path, "--out", converted.path]).run { _ in }
            input = converted
        }
        let project: URL
        if job.isProject {
            project = job.source
        } else {
            var folder: URL? = nil
            if case .folder(let chosen) = settings.output { folder = chosen }
            project = Arguments.newProject(for: job.source, in: folder, documentExtension: job.target.documentExtension) {
                fm.fileExists(atPath: $0.path)
            }
        }
        if stopRequested {
            if input != job.source { try? fm.removeItem(at: input) }
            update(id) { $0.status = .cancelled; $0.step = "已取消" }
            return
        }
        let inCompositor = job.target == .compositor
            && (compositor.currentProject.map { CompositorLink.key($0) == CompositorLink.key(project) } ?? false)
        if inCompositor && autoSave {
            update(id) { $0.step = "先在 Compositor 里保存" }
            let outcome = await compositor.saveFront(project)
            update(id) { $0.log += String(format: "%6.1fs  ", Date().timeIntervalSince(started)) + Self.saveNote(outcome) + "\n" }
            if stopRequested {
                if input != job.source { try? fm.removeItem(at: input) }
                update(id) { $0.status = .cancelled; $0.step = "已取消" }
                return
            }
        }
        let command = [script.path] + Arguments.command(for: job, input: input, project: project, settings: settings,
                                                        inCompositor: inCompositor)
        let run = CommandRun(environment.python, command, environment: Script.environment(for: job.target))
        self.run = run
        let status = await run.run { [weak self] line in
            guard let self else { return }
            self.update(id) { job in
                job.log += String(format: "%6.1fs  ", Date().timeIntervalSince(started)) + line + "\n"
                let (kind, text) = Arguments.parse(line)
                switch kind {
                case .step:
                    if let step = Arguments.step(text) { job.step = step.title; job.fraction = step.fraction }
                case .ok:
                    if let notice = Arguments.notice(text) { job.step = notice }
                case .warning: job.warnings.append(text)
                case .error: job.lastError = text
                default: break
                }
            }
        }
        if input != job.source { try? fm.removeItem(at: input) }
        update(id) { job in
            job.seconds = Date().timeIntervalSince(started)
            job.finished = Date()
            job.fraction = nil
            if status == 0 {
                job.status = .done
                job.project = project
                job.step = "完成"
                let render = Arguments.renderImage(of: project)
                job.render = fm.fileExists(atPath: render.path) ? render : nil
                job.checks = Self.checkImages(in: Arguments.checksFolder(of: project))
            } else if self.stopRequested {
                job.status = .cancelled
                job.step = "已取消"
            } else if !job.retried, let error = job.lastError,
                      error.hasPrefix("project changed on disk") || Arguments.PhotoCraftMessages.savedMeanwhile(error) {
                // The editor saved the document while the script ran, so the script changed nothing: once more,
                // from the saved document (for Compositor the save pressed again first finds nothing left to write).
                job.retried = true
                job.status = .waiting
                job.step = "\(job.target.documentName)刚被保存过，再跑一次"
                job.log += "[app] 运行期间 \(job.target.title) 又保存了这个\(job.target.documentName)，这次没有改动它；自动再跑一次\n"
            } else {
                job.status = .failed(Arguments.failure(job.lastError, status: status))
                job.step = "失败"
            }
        }
    }

    private func update(_ id: RetouchJob.ID, _ change: (inout RetouchJob) -> Void) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        change(&jobs[index])
    }

    /// The log's line about saving in Compositor first.
    static func saveNote(_ outcome: CompositorLink.SaveOutcome) -> String {
        switch outcome {
        case .saved: "[app] 已替你在 Compositor 里保存"
        case .nothingToSave: "[app] 按了 Compositor 的「存储」，它没有写盘：当作没有未保存的改动（若它其实还在保存，脚本会发现并自动再跑一次）"
        case .otherProject: "[app] Compositor 前面已经换成别的工程，没有替你保存；按磁盘上的版本处理"
        case .noMenu: "[app] 没找到 Compositor 的「存储」菜单项，没有替你保存；有未保存的改动请先 ⌘S"
        case .unreadable: "[app] Compositor 没响应，没有替你保存；有未保存的改动请先 ⌘S"
        case .noAccess: "[app] 读不到 Compositor 的窗口，没有替你保存；有未保存的改动请先 ⌘S"
        }
    }

    /// 磨皮方式 follows the project in front: one smoothed one way (on the skin mask, or brushed on by hand) gets
    /// the same way again, so a second click re-makes that layer instead of stacking the other kind on top of it.
    private(set) var followedProject: URL?
    func followSmoothing(_ front: CompositorLink.Front) {
        followedProject = nil
        guard case .project(let url) = front else { return }
        let kinds = Arguments.smoothingKinds(of: url)
        guard kinds.count == 1, let kind = kinds.first else { return }
        if kind != settings.smoothingMask { settings.smoothingMask = kind }
        followedProject = url
    }

    /// What to say under the button about a project's smoothing and the current 磨皮方式: that it was followed, or
    /// that they differ and a click would add a second smoothing layer.
    func smoothingNote(for project: URL) -> String? {
        guard settings.isOn("delicious-skin") else { return nil }
        let kinds = Arguments.smoothingKinds(of: project)
        guard kinds.count == 1, let kind = kinds.first else { return nil }
        if kind == settings.smoothingMask {
            return followedProject.map(CompositorLink.key) == CompositorLink.key(project) ? "磨皮方式已按这个工程设为「\(kind.title)」" : nil
        }
        return "这个工程的磨皮是「\(kind.title)」，现在选的是「\(settings.smoothingMask.title)」：再点会多加一层磨皮"
    }

    /// The check images, in the order they are worth looking at.
    static func checkImages(in folder: URL) -> [URL] {
        let order = ["overview", "before_after", "masks", "features", "look", "glow"]
        let files = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        return files.filter { $0.pathExtension == "jpg" }.sorted {
            (order.firstIndex(of: $0.deletingPathExtension().lastPathComponent) ?? 99,
             $0.lastPathComponent) < (order.firstIndex(of: $1.deletingPathExtension().lastPathComponent) ?? 99,
                                      $1.lastPathComponent)
        }
    }

    static func checkTitle(_ url: URL) -> String {
        switch url.deletingPathExtension().lastPathComponent {
        case "overview": "总览"
        case "before_after": "修前修后"
        case "masks": "皮肤蒙版"
        case "features": "五官蒙版"
        case "look": "调色"
        case "glow": "辉光"
        default: url.deletingPathExtension().lastPathComponent
        }
    }
}
