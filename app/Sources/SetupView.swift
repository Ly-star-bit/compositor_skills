import AppKit
import ApplicationServices
import SwiftUI

/// The first launch: what the app needs, getting it, and what to do when that fails.
struct SetupView: View {
    @Bindable var environment: PythonEnvironment
    @State private var showLog = false

    private let steps = ["创建独立的 Python 环境", "更新安装工具", "下载 NumPy、OpenCV、Pillow", "下载人像分割运行库（可选）"]

    var body: some View {
        ZStack {
            Color(nsColor: .windowBackgroundColor).ignoresSafeArea()
            VStack(spacing: 22) {
                AppMark(size: 84)
                VStack(spacing: 8) {
                    Text("欢迎使用 Compositor 工具").font(.system(size: 28, weight: .semibold))
                    Text("把照片做成可以在 Compositor 里继续修改的分层精修工程")
                        .font(.system(size: 13.5)).foregroundStyle(.secondary)
                }
                card
            }
            .padding(40)
            .frame(maxWidth: 560)
        }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 16) {
            switch environment.state {
            case .checking:
                HStack(spacing: 10) { ProgressView().controlSize(.small); Text("正在检查运行环境…") }
                    .frame(maxWidth: .infinity, alignment: .center)
            case .missing:
                intro
                Button { Task { await environment.install() } } label: {
                    Label("开始准备（约 150 MB）", systemImage: "arrow.down.circle.fill")
                }
                .buttonStyle(PrimaryButtonStyle())
            case .installing(let title, let fraction):
                stepList(current: title)
                ProgressBar(fraction: fraction)
                Text("正在\(title)…  需要联网，只需要这一次").font(.system(size: 11.5)).foregroundStyle(.secondary)
            case .failed(let message):
                Label(message, systemImage: "exclamationmark.triangle.fill")
                    .font(.system(size: 12.5)).foregroundStyle(Theme.failure)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) {
                    Button { Task { await environment.install(fresh: true) } } label: { Label("重试", systemImage: "arrow.clockwise") }
                        .buttonStyle(PrimaryButtonStyle())
                    Button { showLog.toggle() } label: { Label(showLog ? "收起日志" : "查看日志", systemImage: "doc.text") }
                        .buttonStyle(.glass)
                }
                if showLog { logView }
            case .ready:
                Label("运行环境已就绪", systemImage: "checkmark.circle.fill").foregroundStyle(Theme.success)
            }
        }
        .glassCard(corner: 22, padding: 22)
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 10) {
            Text("第一次使用要准备运行环境").font(.system(size: 14, weight: .semibold))
            ForEach([("lock.shield", "装在应用自己的文件夹里，不影响系统和你的其他软件"),
                     ("wifi", "需要联网下载一次，之后完全离线使用"),
                     ("cpu", "所有修图都在这台 Mac 上完成，照片不会上传")], id: \.1) { symbol, text in
                Label(text, systemImage: symbol).font(.system(size: 12.5)).foregroundStyle(.secondary)
            }
        }
    }

    private func stepList(current: String) -> some View {
        let index = steps.firstIndex(of: current) ?? 0
        return VStack(alignment: .leading, spacing: 9) {
            ForEach(Array(steps.enumerated()), id: \.offset) { position, title in
                HStack(spacing: 10) {
                    Group {
                        if position < index { Image(systemName: "checkmark.circle.fill").foregroundStyle(Theme.success) }
                        else if position == index { ProgressView().controlSize(.mini) }
                        else { Image(systemName: "circle").foregroundStyle(.tertiary) }
                    }
                    .frame(width: 16)
                    Text(title).font(.system(size: 12.5, weight: position == index ? .semibold : .regular))
                        .foregroundStyle(position > index ? .secondary : .primary)
                }
            }
        }
    }

    private var logView: some View {
        ScrollView {
            Text(environment.log.isEmpty ? "（没有记录）" : environment.log)
                .font(.system(size: 10.5, design: .monospaced)).textSelection(.enabled)
                .frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(height: 140)
        .padding(8)
        .background(.primary.opacity(0.05), in: RoundedRectangle(cornerRadius: 10))
    }
}

/// The app's mark (and, drawn at 824 points, its icon): a graphite squircle with a lens aperture and a small
/// safelight dot. Photographic rather than "AI": no violet gradients, no sparkles.
struct AppMark: View {
    var size: CGFloat
    var shadow = true
    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * 0.225, style: .continuous)
        ZStack {
            shape.fill(LinearGradient(colors: [Color(white: 0.27), Color(white: 0.11)], startPoint: .top, endPoint: .bottom))
            shape.fill(RadialGradient(colors: [.white.opacity(0.10), .clear], center: UnitPoint(x: 0.5, y: 0.0),
                                      startRadius: 0, endRadius: size * 0.75))
            Image(systemName: "camera.aperture")
                .font(.system(size: size * 0.52, weight: .light))
                .foregroundStyle(Color(white: 0.93))
            Circle().fill(Theme.accentColor)
                .frame(width: size * 0.085, height: size * 0.085)
                .shadow(color: Theme.accentColor.opacity(0.6), radius: size * 0.03)
                .offset(x: size * 0.31, y: -size * 0.31)
            shape.strokeBorder(LinearGradient(colors: [.white.opacity(0.22), .white.opacity(0.04)], startPoint: .top, endPoint: .bottom),
                               lineWidth: max(0.5, size * 0.006))
        }
        .frame(width: size, height: size)
        .shadow(color: .black.opacity(shadow ? 0.28 : 0), radius: size * 0.1, y: size * 0.04)
    }
}

/// Settings (⌘,): the environment, the script in use, and Compositor.
struct SettingsView: View {
    @Bindable var environment: PythonEnvironment

    var body: some View {
        VStack(spacing: 14) {
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "运行环境", symbol: "shippingbox.fill")
                row("状态") { status }
                row("人像分割模型") {
                    Text(environment.hasSegmentationModel ? "可用 · 头发、衣服分得更准" : "不可用 · 按纹理区分头发")
                        .foregroundStyle(.secondary)
                }
                row("位置") { path(environment.venv.path) }
                HStack(spacing: 8) {
                    Button { Task { await environment.check() } } label: { Label("重新检查", systemImage: "arrow.clockwise") }
                    Button { Task { await environment.install(fresh: true) } } label: { Label("重新安装", systemImage: "arrow.down.circle") }
                }
                .controlSize(.small)
                .disabled(isInstalling)
            }
            .glassCard()
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "修图脚本", symbol: "terminal.fill")
                row("使用") {
                    Text(Script.isBundledCopy ? "应用内置的副本" : "skill 文件夹里的，改动立即生效").foregroundStyle(.secondary)
                }
                row("路径") { path(Script.url?.path ?? "找不到") }
            }
            .glassCard()
            VStack(alignment: .leading, spacing: 12) {
                SectionTitle(title: "Compositor", symbol: "square.stack.3d.up.fill")
                row("状态") {
                    Text(Compositor.app == nil ? "这台 Mac 上没有找到，工程仍会照常生成" : "已安装，可以一键打开工程")
                        .foregroundStyle(.secondary)
                }
                row("当前工程") {
                    if AXIsProcessTrusted() {
                        Label("可以读取 Compositor 正在编辑的工程", systemImage: "checkmark.circle.fill").foregroundStyle(Theme.success)
                    } else {
                        HStack(spacing: 8) {
                            Text("还不能读取").foregroundStyle(.secondary)
                            Button("允许读取") { CompositorLink.requestAccess() }.controlSize(.small)
                        }
                    }
                }
            }
            .glassCard()
        }
        .font(.system(size: 12.5))
        .padding(20)
        .frame(width: 520)
        .fixedSize(horizontal: false, vertical: true)
        .background(Color(nsColor: .windowBackgroundColor))
    }

    private var isInstalling: Bool {
        if case .installing = environment.state { return true }
        return environment.state == .checking
    }

    @ViewBuilder private var status: some View {
        switch environment.state {
        case .ready:
            Label("就绪 · \(environment.interpreterDescription)", systemImage: "checkmark.circle.fill").foregroundStyle(Theme.success)
        case .checking: Text("检查中…").foregroundStyle(.secondary)
        case .installing(let step, _): Text("安装中：\(step)").foregroundStyle(.secondary)
        case .missing: Text("未安装").foregroundStyle(.secondary)
        case .failed: Label("安装失败", systemImage: "exclamationmark.triangle.fill").foregroundStyle(Theme.failure)
        }
    }

    private func row<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(title).foregroundStyle(.primary).frame(width: 84, alignment: .leading)
            content().frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private func path(_ text: String) -> some View {
        Text(text).font(.system(size: 11, design: .monospaced)).foregroundStyle(.secondary)
            .lineLimit(2).truncationMode(.middle).textSelection(.enabled)
    }
}
