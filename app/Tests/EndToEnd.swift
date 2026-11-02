// End-to-end check of the app's working parts, headless (no window, nothing opened): builds the Python environment
// from the internet into a scratch folder exactly as the first launch does, then runs batches through Studio the way
// the 开始精修 button does. Built and run by Tests/run.sh with the app's sources (all but App.swift).
//
//   EndToEnd <support folder> <work folder> <photo> <photo> [<photo>...]
import AppKit
import Foundation

@main
enum EndToEnd {
    nonisolated(unsafe) static var failures = 0

    static func expect(_ condition: Bool, _ what: String) {
        print(condition ? "  ok    \(what)" : "  FAIL  \(what)")
        if !condition { failures += 1 }
    }

    @MainActor static func main() async {
        let args = CommandLine.arguments
        guard args.count >= 5 else { print("usage: EndToEnd <support> <work> <photo> <photo>..."); exit(2) }
        let support = URL(fileURLWithPath: args[1])
        let work = URL(fileURLWithPath: args[2])
        let fm = FileManager.default
        let here = URL(fileURLWithPath: #filePath).deletingLastPathComponent()
        Script.override = here.appendingPathComponent("../../scripts/retouch_builder.py").standardizedFileURL
        expect(fm.fileExists(atPath: Script.url!.path), "the skill's script is found")

        // ---- commands always come back: many short ones, from tasks on any thread, each must report its exit ----
        let burst = Date()
        let statuses = await withTaskGroup(of: Int32.self) { group in
            for index in 0..<60 {
                group.addTask {
                    await CommandRun(URL(fileURLWithPath: "/bin/sh"), ["-c", "echo \(index); exit \(index % 3)"]).run { _ in }
                }
            }
            return await group.reduce(into: [Int32]()) { $0.append($1) }
        }
        expect(statuses.count == 60 && statuses.filter { $0 == 2 }.count == 20,
               String(format: "60 commands at once all came back with their exit status (%.1f s)", Date().timeIntervalSince(burst)))
        for index in 0..<30 {  // and one after another, as a queue runs them
            let status = await CommandRun(URL(fileURLWithPath: "/usr/bin/true"), []).run { _ in }
            if status != 0 { expect(false, "command \(index) came back with \(status)") }
        }

        // ---- the first launch: no environment, then one built from the internet ----
        let environment = PythonEnvironment(support: support)
        await environment.check()
        if environment.state == .missing {
            print("== installing (needs the network)")
            let started = Date()
            var seen: [String] = []
            let watcher = Task { @MainActor in
                while !Task.isCancelled {
                    if case .installing(let title, _) = environment.state, seen.last != title { seen.append(title); print("   … \(title)") }
                    try? await Task.sleep(for: .milliseconds(200))
                }
            }
            await environment.install()
            watcher.cancel()
            print(String(format: "   installed in %.0f s, %@, segmentation model %@", Date().timeIntervalSince(started),
                         environment.interpreterDescription, environment.hasSegmentationModel ? "yes" : "no"))
            expect(seen.count == 4, "the four install steps were shown (\(seen.count))")
            expect(fm.fileExists(atPath: environment.logFile.path), "the install log is written")
        } else {
            print("== environment already there: \(environment.state)")
        }
        expect(environment.state == .ready, "the environment is ready")
        guard environment.state == .ready else { print(environment.log.suffix(2000)); exit(1) }

        // ---- a batch: photos (one HEIC), a broken file, settings from every card ----
        try? fm.removeItem(at: work)
        try? fm.createDirectory(at: work, withIntermediateDirectories: true)
        var photos: [URL] = []
        for (index, path) in args[3...].enumerated() {
            let copy = work.appendingPathComponent("photo\(index + 1).jpg")
            try? fm.copyItem(at: URL(fileURLWithPath: path), to: copy)
            photos.append(copy)
        }
        let heic = work.appendingPathComponent("photo_heic.heic")
        _ = await CommandRun(URL(fileURLWithPath: "/usr/bin/sips"), ["-s", "format", "heic", args[3], "--out", heic.path]).run { _ in }
        let broken = work.appendingPathComponent("broken.jpg")
        try? "not a photo".write(to: broken, atomically: true, encoding: .utf8)
        try? fm.createDirectory(at: work.appendingPathComponent("photo1.checks"), withIntermediateDirectories: true)  // taken name

        let studio = Studio(environment: environment)
        studio.add(photos + [heic, broken, work.appendingPathComponent("notes.txt")])
        studio.add([photos[0]])  // once only
        expect(studio.jobs.count == photos.count + 2, "photos, HEIC and the broken file queued once, the text file left out")
        studio.settings.preset = .dr5
        studio.settings.modules = ["neutral-gray"]
        studio.settings.look = .film
        studio.settings.glow = .mist
        studio.settings.glowColorMode = .twoTone
        studio.settings.strength = 0.8

        var steps: [String: [String]] = [:]
        var fractions: [String: [Double]] = [:]
        let watcher = Task { @MainActor in
            while !Task.isCancelled {
                if let job = studio.currentJob {
                    if steps[job.name]?.last != job.step { steps[job.name, default: []].append(job.step) }
                    if let fraction = job.fraction, fractions[job.name]?.last != fraction { fractions[job.name, default: []].append(fraction) }
                }
                try? await Task.sleep(for: .milliseconds(50))
            }
        }
        print("== batch of \(studio.jobs.count)")
        let started = Date()
        await studio.start()
        watcher.cancel()
        print(String(format: "   batch done in %.0f s", Date().timeIntervalSince(started)))
        for job in studio.jobs {
            print("   \(job.name): \(job.status) \(job.seconds.map { String(format: "%.1fs", $0) } ?? "") steps: \((steps[job.name] ?? []).joined(separator: " → "))")
        }
        for job in studio.jobs where job.name != "broken" {
            expect(job.status == .done, "\(job.name) is done")
            guard let project = job.project else { continue }
            expect(fm.fileExists(atPath: project.appendingPathComponent("manifest.json").path), "\(project.lastPathComponent) written")
            expect(job.render != nil, "\(job.name) has its render")
            expect(job.checks.count >= 3, "\(job.name) has check images (\(job.checks.map { $0.lastPathComponent }))")
            expect(job.warnings.isEmpty || job.log.contains("[WARN]"), "\(job.name) warnings come from the log")
            expect((steps[job.name] ?? []).contains("DR5 磨皮") && (steps[job.name] ?? []).contains("渲染预览"),
                   "\(job.name) showed its steps as they happened")
        }
        expect(studio.jobs.first?.project?.lastPathComponent == "photo1 2.comp", "a taken name gets a number (\(studio.jobs.first?.project?.lastPathComponent ?? "-"))")
        if let failed = studio.jobs.first(where: { $0.name == "broken" }) {
            if case .failed(let reason) = failed.status {
                print("   broken.jpg: \(reason)")
                expect(reason.contains("读不了"), "the failure says why, in words")
                expect(failed.log.contains("[Error] cannot read"), "the script's own message is in the log")
            } else { expect(false, "broken.jpg failed") }
        }
        expect(fractions.values.allSatisfy { $0 == $0.sorted() } && fractions.values.contains { $0.count > 3 },
               "each photo's progress moves forward, step by step")
        expect(studio.progress == 1 && studio.batchFinished == studio.batch.count, "the run is complete")

        // ---- adding to an existing project, and a preset that needs a photo ----
        let projects = studio.jobs.compactMap(\.project)
        let again = Studio(environment: environment)
        again.add([projects[0]])
        again.settings = RetouchSettings(preset: .none, look: .bw, render: true, checks: false)
        await again.start()
        expect(again.jobs[0].status == .done && again.jobs[0].project == projects[0], "a look is added to an existing project")
        let layers = { (url: URL) -> Int in
            let data = try? Data(contentsOf: url.appendingPathComponent("manifest.json"))
            let manifest = data.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            return (manifest?["layers"] as? [Any])?.count ?? 0
        }
        print("   \(projects[0].lastPathComponent): \(layers(projects[0])) layers after the second run")
        let before = layers(projects[0])
        again.jobs[0].status = .waiting
        again.settings.choose(.dr5)
        again.settings.set("sculpt", false)
        again.settings.smoothingMask = .manual
        await again.start()
        expect(again.jobs[0].status == .done && layers(projects[0]) > before,
               "DR5 without 3D 骨相 goes on an existing project, module by module (\(before) → \(layers(projects[0])) layers)")
        expect(!again.jobs[0].log.contains("sculpt"), "the module switched off didn't run")
        expect(again.jobs[0].log.contains("黑蒙版 · 画笔涂白处磨皮"), "手动刷出: the smoothing layer starts with a black mask")

        // ---- 重算: the automatic layers recomputed in place from the project as it is, nothing added ----
        let refresher = Studio(environment: environment)
        refresher.settings = RetouchSettings(preset: .none, render: false, checks: false)
        refresher.add([projects[1]])
        refresher.jobs[0].refresh = true
        let count = layers(projects[1])
        expect(Arguments.canRefresh(projects[1]), "\(projects[1].lastPathComponent) offers 重算")
        await refresher.start()
        expect(refresher.jobs[0].status == .done && refresher.jobs[0].log.contains("(重算)") && layers(projects[1]) == count,
               "重算 recomputes the automatic layers in place (\(count) layers before, \(layers(projects[1])) after)")

        // ---- 精修 on another project while one is running: it joins the queue and follows ----
        let line = Studio(environment: environment)
        line.settings = RetouchSettings(preset: .none, look: .film, render: false, checks: false)
        line.retouchNow(projects[0])
        while !line.isRunning { try? await Task.sleep(for: .milliseconds(20)) }
        line.retouchNow(projects[1])
        expect(line.jobs.count == 2 && line.jobs[1].status == .waiting && line.place(of: line.jobs[1].id) == 1,
               "a second project sent while the first runs waits in line")
        while line.isRunning { try? await Task.sleep(for: .milliseconds(100)) }
        expect(line.jobs.allSatisfy { $0.status == .done }, "and is done after it, without another click (\(line.jobs.map(\.status)))")

        // ---- stopping half-way leaves no half-written project ----
        let stopper = Studio(environment: environment)
        let photo = work.appendingPathComponent("stop.jpg")
        try? fm.copyItem(at: photos[0], to: photo)
        stopper.add([photo])
        stopper.settings.preset = .dr5
        Task { @MainActor in
            while stopper.currentJob?.step != "DR5 磨皮" { try? await Task.sleep(for: .milliseconds(20)) }
            stopper.stop()
        }
        await stopper.start()
        expect(stopper.jobs[0].status == .cancelled, "stop cancels the photo being worked on (\(stopper.jobs[0].status))")
        expect(!fm.fileExists(atPath: work.appendingPathComponent("stop.comp").path), "and leaves no project behind")

        print(failures == 0 ? "PASS end-to-end" : "FAIL end-to-end: \(failures)")
        exit(failures == 0 ? 0 : 1)
    }
}
