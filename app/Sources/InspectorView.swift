import AppKit
import SwiftUI

/// The settings: preset, modules, color grade, glow, background and grain, strength, where projects go, and the
/// button that runs the queue.
struct InspectorView: View {
    @Bindable var studio: Studio

    var body: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(spacing: 14) {
                    presetCard
                    ForEach(ModuleGroup.allCases) { group in moduleCard(group) }
                    smoothingCard
                    freqSepCard
                    lookCard
                    glowCard
                    finishCard
                    strengthCard
                    outputCard
                }
                .padding(.horizontal, 14).padding(.top, 14).padding(.bottom, 8)
            }
            .scrollIndicators(.never)
            actionBar
        }
        .frame(minWidth: 300, idealWidth: 330)
    }

    private var settings: Binding<RetouchSettings> { $studio.settings }

    /// "在「DR5」基础上：+中性灰 −胶片颗粒"
    private var changes: String {
        let s = studio.settings
        let added = Arguments.moduleOrder.filter(s.modules.contains).map { "+" + Arguments.title(ofModule: $0) }
        let removed = Arguments.moduleOrder.filter(s.removed.contains).map { "−" + Arguments.title(ofModule: $0) }
        let base = s.preset == .none ? "自选" : "在「\(s.preset.title)」基础上"
        return base + "：" + (added + removed).joined(separator: " ")
    }

    // MARK: preset

    private var presetCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle(title: "预设", symbol: "square.stack.3d.up.fill")
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 3), spacing: 8) {
                ForEach(Preset.allCases) { preset in
                    Chip(title: preset.title, selected: studio.settings.preset == preset) {
                        studio.settings.choose(preset)
                    }
                }
            }
            if studio.settings.isCustomized {
                HStack(alignment: .firstTextBaseline) {
                    Text(changes).font(.system(size: 11.5)).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                    Spacer(minLength: 6)
                    Button("恢复预设") { studio.settings.choose(studio.settings.preset) }
                        .buttonStyle(.plain).font(.system(size: 11, weight: .medium)).foregroundStyle(Theme.accentColor)
                }
            } else {
                Text(studio.settings.preset.detail + "。下面的开关都可以再改。")
                    .font(.system(size: 11.5)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
        }
        .glassCard()
    }

    // MARK: modules

    private func moduleCard(_ group: ModuleGroup) -> some View {
        let included = studio.settings.preset.includes
        let picked = group.modules.filter { studio.settings.isOn($0.flag) }.count
        return VStack(alignment: .leading, spacing: 10) {
            SectionTitle(title: group.title, symbol: groupSymbol(group), trailing: picked > 0 ? "\(picked) 项" : nil)
            VStack(spacing: 2) {
                ForEach(group.modules) { module in
                    ModuleRow(module: module.adapted(to: studio.target), inPreset: included.contains(module.flag),
                              isOn: Binding(get: { studio.settings.isOn(module.flag) },
                                            set: { studio.settings.set(module.flag, $0) }))
                }
            }
        }
        .glassCard()
    }

    private func groupSymbol(_ group: ModuleGroup) -> String {
        switch group {
        case .skin: "face.smiling"
        case .features: "eye"
        case .light: "slider.horizontal.3"
        }
    }

    // MARK: color grade

    private var lookCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle(title: "调色", symbol: "camera.filters",
                         trailing: studio.settings.look == .none ? nil : studio.settings.look.title)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 10), count: 4), spacing: 12) {
                ForEach(Look.allCases) { look in
                    LookTile(look: look, selected: studio.settings.look == look) { studio.settings.look = look }
                }
            }
        }
        .glassCard()
    }

    // MARK: glow

    private var glowCard: some View {
        VStack(alignment: .leading, spacing: 12) {
            SectionTitle(title: "辉光", symbol: "sun.max.fill",
                         trailing: studio.settings.glow == .none ? nil : studio.settings.glow.title)
            LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 4), spacing: 8) {
                ForEach(GlowStyle.allCases) { style in
                    GlowTile(style: style, selected: studio.settings.glow == style) { studio.settings.glow = style }
                }
            }
            if studio.settings.glow == .mist || studio.settings.glow == .halation {
                HStack(spacing: 6) {
                    ForEach(GlowColorMode.allCases) { mode in
                        Chip(title: mode.title, symbol: modeSymbol(mode), selected: studio.settings.glowColorMode == mode) {
                            studio.settings.glowColorMode = mode
                        }
                    }
                }
                switch studio.settings.glowColorMode {
                case .natural:
                    Text(studio.settings.glow == .mist ? "光晕取高光自己的颜色，之后可以在 \(studio.target.title) 里换色"
                                                        : "胶片光晕不着色，就是高光自己的颜色")
                        .font(.system(size: 11.5)).foregroundStyle(.secondary)
                case .single:
                    ColorChooser(title: "颜色", color: settings.glowColor)
                case .twoTone:
                    ColorChooser(title: "内圈", color: settings.glowColor)
                    ColorChooser(title: "外圈", color: settings.glowOuterColor)
                }
            } else if studio.settings.glow == .dream {
                Text(studio.target == .compositor ? "梦幻柔焦是实时的高斯模糊层，用照片自己的颜色"
                                                  : "梦幻柔焦是盖印的智能对象 + 高斯模糊，用照片自己的颜色")
                    .font(.system(size: 11.5)).foregroundStyle(.secondary)
            }
        }
        .glassCard()
        .animation(.snappy(duration: 0.25), value: studio.settings.glow)
        .animation(.snappy(duration: 0.25), value: studio.settings.glowColorMode)
    }

    private func modeSymbol(_ mode: GlowColorMode) -> String {
        switch mode {
        case .natural: "circle.dashed"
        case .single: "circle.fill"
        case .twoTone: "circle.lefthalf.filled"
        }
    }

    // MARK: grain

    private var finishCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(title: "质感", symbol: "photo.artframe")
            VStack(spacing: 2) {
                ModuleRow(module: Module(flag: "grain", title: "胶片颗粒", symbol: "circle.dotted",
                                         note: "颗粒大小随分辨率"),
                          inPreset: studio.settings.preset.includes.contains("grain"),
                          isOn: Binding(get: { studio.settings.isOn("grain") }, set: { studio.settings.set("grain", $0) }))
            }
        }
        .glassCard()
    }

    // MARK: DR5 smoothing: mask, strength, texture

    private var smoothingOn: Bool { studio.settings.isOn("delicious-skin") }

    private var smoothingCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(title: "DR5 磨皮", symbol: "circle.hexagongrid",
                         trailing: smoothingOn ? "\(studio.settings.smoothingMask.title) · \(studio.settings.skinStrength.title)" : "未开")
            Text("方式").font(.system(size: 12, weight: .semibold))
            HStack(spacing: 6) {
                ForEach(SmoothingMask.allCases) { mask in
                    Chip(title: mask.title, symbol: mask == .manual ? "paintbrush.pointed" : "person.crop.circle.dashed",
                         selected: studio.settings.smoothingMask == mask) {
                        studio.settings.smoothingMask = mask
                    }
                }
            }
            Text(smoothingOn
                 ? (studio.settings.smoothingMask == .manual ? "手动：图层带黑蒙版，像 PS 里 DR5 手动磨皮一样，用白画笔刷哪里磨哪里"
                                                              : "自动：按识别出的皮肤范围盖上，蒙版可擦改")
                 : "打开「DR5 磨皮」后可选")
                .font(.system(size: 11)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("力度").font(.system(size: 12, weight: .semibold))
            HStack(spacing: 6) {
                ForEach(SkinStrength.allCases) { strength in
                    Chip(title: strength.title, selected: studio.settings.skinStrength == strength) {
                        studio.settings.skinStrength = strength
                    }
                }
            }
            Text("轻 53%、中 88%、重 100% 盖在皮肤上；重也更积极地先祛痘。再点精修会按新力度重做已有的磨皮层")
                .font(.system(size: 11)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("质感").font(.system(size: 12, weight: .semibold))
            HStack(spacing: 6) {
                ForEach(SkinTexture.allCases) { texture in
                    Chip(title: texture.title, selected: studio.settings.texture == texture) {
                        studio.settings.texture = texture
                    }
                }
            }
            Text("自然：毛孔几乎全留；柔和：接近 DR5 插件的细腻；光滑：更精致")
                .font(.system(size: 11)).foregroundStyle(.secondary)
        }
        .disabled(!smoothingOn)
        .opacity(smoothingOn ? 1 : 0.55)
        .glassCard()
    }

    // MARK: frequency separation: the radius

    private var freqSepOn: Bool { studio.settings.isOn("freq-sep") }

    private var freqSepCard: some View {
        let auto = studio.settings.freqSepAuto
        let note = auto ? "自动：脸宽的 1/200，毛孔、细纹、小瑕疵进高频，肤色和明暗留在低频"
                        : "手动：半径越大，进高频的越多（再大一些，色块、明暗也会进去）"
        let redo = studio.target == .photocraft ? "PhotoCraft 文档里已有的高低频不会重做，换半径先删掉那个组"
                                                : "已有的高低频没手修过时，换半径再点精修会按新半径重做；手修过的不动"
        return VStack(alignment: .leading, spacing: 10) {
            SectionTitle(title: "高低频", symbol: "square.split.2x1",
                         trailing: freqSepOn ? (auto ? "按脸宽" : Arguments.radiusText(studio.settings.freqSepRadius)) : "未开")
            Text("半径").font(.system(size: 12, weight: .semibold))
            HStack(spacing: 6) {
                Chip(title: "按脸宽", symbol: "face.dashed", selected: auto) { studio.settings.freqSepAuto = true }
                Chip(title: "手动", symbol: "slider.horizontal.3", selected: !auto) { studio.settings.freqSepAuto = false }
            }
            if !auto {
                Slider(value: Binding(get: { studio.settings.freqSepRadius },
                                      set: { studio.settings.freqSepRadius = ($0 * 2).rounded() / 2 }),
                       in: RetouchSettings.freqSepRadii) {
                    EmptyView()
                } minimumValueLabel: {
                    Text("细").font(.system(size: 11)).foregroundStyle(.secondary)
                } maximumValueLabel: {
                    Text("粗").font(.system(size: 11)).foregroundStyle(.secondary)
                }
                .tint(Theme.accentColor)
            }
            Text(freqSepOn ? note + "。" + redo : "打开「高低频」后可选")
                .font(.system(size: 11)).foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .disabled(!freqSepOn)
        .opacity(freqSepOn ? 1 : 0.55)
        .glassCard()
    }

    // MARK: strength

    private var strengthCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(title: "辉光与调色强度", symbol: "dial.medium.fill",
                         trailing: String(format: "%.1f×", studio.settings.strength))
            Slider(value: Binding(get: { studio.settings.strength },
                                  set: { studio.settings.strength = ($0 * 10).rounded() / 10 }), in: 0.3...1.7) {
                EmptyView()
            } minimumValueLabel: {
                Text("轻").font(.system(size: 11)).foregroundStyle(.secondary)
            } maximumValueLabel: {
                Text("重").font(.system(size: 11)).foregroundStyle(.secondary)
            }
            .tint(Theme.accentColor)
            Text("辉光和调色文件夹的不透明度；磨皮的力度在上面单独选")
                .font(.system(size: 11)).foregroundStyle(.secondary)
        }
        .glassCard()
    }

    // MARK: output

    private var outputCard: some View {
        VStack(alignment: .leading, spacing: 10) {
            SectionTitle(title: "输出", symbol: "tray.and.arrow.down.fill")
            HStack(spacing: 6) {
                Chip(title: "照片旁边", symbol: "photo", selected: studio.settings.output == .besidePhoto) {
                    studio.settings.output = .besidePhoto
                }
                Chip(title: folderTitle, symbol: "folder", selected: studio.settings.output != .besidePhoto) {
                    chooseFolder()
                }
            }
            Toggle(isOn: settings.render) { Label("生成后渲染预览图", systemImage: "eye") }
            Toggle(isOn: settings.checks) { Label("生成检查图", systemImage: "checkmark.seal") }
        }
        .toggleStyle(.switch)
        .controlSize(.small)
        .font(.system(size: 12.5))
        .glassCard()
    }

    private var folderTitle: String {
        if case .folder(let url) = studio.settings.output { return url.lastPathComponent }
        return "选择文件夹…"
    }

    private func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "存到这里"
        if panel.runModal() == .OK, let url = panel.url { studio.settings.output = .folder(url) }
    }

    // MARK: run

    private var actionBar: some View {
        VStack(spacing: 10) {
            if studio.isRunning {
                VStack(spacing: 6) {
                    ProgressBar(fraction: studio.progress)
                    Text("第 \(min(studio.batchFinished + 1, studio.batch.count)) / \(studio.batch.count) 张 · \(studio.currentJob?.step ?? "")")
                        .font(.system(size: 11)).foregroundStyle(.secondary).lineLimit(1)
                }
                Button { studio.stop() } label: { Label("停止", systemImage: "stop.fill") }
                    .buttonStyle(PrimaryButtonStyle(tint: AnyShapeStyle(.primary.opacity(0.1)), text: AnyShapeStyle(.primary)))
            } else {
                if let reason = blockReason {
                    Text(reason).font(.system(size: 11)).foregroundStyle(.secondary).multilineTextAlignment(.center)
                }
                Button {
                    studio.requeueUnfinished()
                    Task { await studio.start() }
                } label: {
                    Label(studio.pending.isEmpty ? "开始精修" : "开始精修 · \(studio.pending.count) 张", systemImage: "paintbrush.pointed.fill")
                }
                .buttonStyle(PrimaryButtonStyle())
                .disabled(blockReason != nil)
                .keyboardShortcut(.return, modifiers: .command)
            }
        }
        .padding(14)
        .background(.bar)
        .overlay(alignment: .top) { Divider() }
    }

    private var blockReason: String? {
        if studio.environment.state != .ready { return "运行环境还没准备好" }
        if studio.jobs.isEmpty {
            if studio.target == .photocraft { return "先把照片或 .pcraft 文档拖到左边" }
            return studio.compositor.currentProject != nil ? "点左上角的「精修这个工程」，或者把照片拖到左边" : "先把照片拖到左边"
        }
        if !studio.settings.doesSomething { return "还没有选择任何效果" }
        if studio.jobs.allSatisfy({ $0.status == .done }) { return "队列里的都已完成，再拖入新的照片吧" }
        return nil
    }
}

/// One module: an icon, its name and what it does, and a switch.
struct ModuleRow: View {
    let module: Module
    let inPreset: Bool
    @Binding var isOn: Bool
    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: module.symbol)
                .font(.system(size: 12, weight: .semibold))
                .frame(width: 26, height: 26)
                .foregroundStyle(isOn ? AnyShapeStyle(Theme.onInk) : AnyShapeStyle(.secondary))
                .background(RoundedRectangle(cornerRadius: 7, style: .continuous)
                    .fill(isOn ? AnyShapeStyle(Theme.ink) : AnyShapeStyle(.primary.opacity(0.07))))
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 5) {
                    Text(module.title).font(.system(size: 12.5, weight: .medium))
                    if inPreset && isOn {
                        Text("预设").font(.system(size: 9.5, weight: .semibold))
                            .padding(.horizontal, 5).padding(.vertical, 1)
                            .foregroundStyle(.secondary)
                            .background(.primary.opacity(0.07), in: Capsule())
                    }
                }
                Text(module.note).font(.system(size: 10.5)).foregroundStyle(.secondary).lineLimit(1)
            }
            Spacer(minLength: 4)
            Toggle("", isOn: $isOn).labelsHidden().toggleStyle(.switch).controlSize(.mini)
        }
        .padding(.vertical, 4)
        .contentShape(Rectangle())
        .onTapGesture { isOn.toggle() }  // the whole row is the switch
    }
}

/// A color grade's swatch and name.
struct LookTile: View {
    let look: Look
    let selected: Bool
    var action: () -> Void
    var body: some View {
        Button(action: action) {
            VStack(spacing: 5) {
                ZStack {
                    Circle().fill(LinearGradient(colors: look.swatch.map { Color(.sRGB, red: $0.0, green: $0.1, blue: $0.2) },
                                                 startPoint: .topLeading, endPoint: .bottomTrailing))
                    if look == .none { Image(systemName: "nosign").font(.system(size: 13)).foregroundStyle(.secondary) }
                }
                .frame(width: 38, height: 38)
                .overlay(Circle().strokeBorder(selected ? AnyShapeStyle(Theme.ink) : AnyShapeStyle(.primary.opacity(0.1)),
                                               lineWidth: selected ? 2.5 : 0.6).padding(-3))
                .shadow(color: .black.opacity(0.08), radius: 3, y: 1)
                Text(look.title).font(.system(size: 10.5, weight: selected ? .semibold : .regular)).lineLimit(1)
            }
            .frame(maxWidth: .infinity)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .animation(.snappy(duration: 0.2), value: selected)
    }
}

/// A glow color: the skill's named colors as swatches, plus any color from the system picker.
struct ColorChooser: View {
    let title: String
    @Binding var color: RGB
    var body: some View {
        HStack(spacing: 7) {
            Text(title).font(.system(size: 11.5)).foregroundStyle(.secondary).frame(width: 30, alignment: .leading)
            ForEach(RGB.named, id: \.0) { name, swatch in
                Circle().fill(Color(swatch))
                    .frame(width: 20, height: 20)
                    .overlay(Circle().strokeBorder(color == swatch ? AnyShapeStyle(Theme.ink) : AnyShapeStyle(.primary.opacity(0.12)),
                                                   lineWidth: color == swatch ? 2 : 0.5).padding(-2.5))
                    .help(name)
                    .onTapGesture { color = swatch }
            }
            Spacer(minLength: 0)
            ColorPicker("", selection: Binding(get: { Color(color) }, set: { color = RGB($0) }), supportsOpacity: false)
                .labelsHidden()
        }
    }
}

/// A glow style: a small picture of the light it makes, and its name.
struct GlowTile: View {
    let style: GlowStyle
    let selected: Bool
    var action: () -> Void

    private var symbol: String {
        switch style {
        case .none: "circle.slash"
        case .mist: "sun.haze.fill"
        case .halation: "sun.max.fill"
        case .dream: "sparkles"
        }
    }

    var body: some View {
        Button(action: action) {
            VStack(spacing: 6) {
                Image(systemName: symbol)
                    .font(.system(size: 15, weight: .semibold))
                    .foregroundStyle(selected ? AnyShapeStyle(Theme.onInk) : AnyShapeStyle(.secondary))
                    .frame(maxWidth: .infinity, minHeight: 34)
                    .background {
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(selected ? AnyShapeStyle(Theme.ink) : AnyShapeStyle(.primary.opacity(0.06)))
                    }
                Text(style.title).font(.system(size: 10.5, weight: selected ? .semibold : .regular)).lineLimit(1)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .animation(.snappy(duration: 0.2), value: selected)
    }
}
