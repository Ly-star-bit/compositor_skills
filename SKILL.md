---
name: compositor-retouch
description: >-
  Builds layered portrait-retouching projects for Compositor, the macOS image editor (.comp packages), and renders
  them headless: DR5-style skin smoothing (automatic skin mask or brushed on by hand), neutral gray and dual curves
  for dodge & burn, 3D sculpt, eye brilliance, frequency separation, color grades
  (airy, film, cool-fair, creamy, HK, B&W), glow (pro-mist, halation, dream; any color), grain and observation
  folders, as layers, live adjustment layers and masks painted in from Apple Vision face landmarks and segmentation,
  which the user keeps editing in Compositor. Use when the user mentions Compositor or a .comp file, or wants a
  retouching setup for a portrait: 修图工程, 人像精修, 磨皮, DR5, 手动磨皮, 中性灰, 双曲线, 重算, 骨相, 眼神光, 调色,
  滤镜, 日系, 胶片, 冷白皮, 奶油肌, 港风, 黑白, 辉光, 黑柔, 柔焦, 高低频, 观察组, 渲染预览.
---

# Compositor 人像精修工程

`scripts/retouch_builder.py` 从一张照片新建 Compositor `.comp` 工程，或给已有工程追加精修图层。生成的都是普通图层：用户在 Compositor 里调不透明度、在蒙版上擦改即可。工具围绕一套手工精修流程：中性灰或双曲线做光影，DR5 式磨皮（默认像 PS 一样黑蒙版手动刷出，也可自动蒙版），再按需加滤镜（调色、辉光）。各模块的算法、阈值和对比数据都在 [references/modules.md](references/modules.md)，按小节名查，不用整个读。

## 规则

- **不要打开任何 GUI**：不用 `open` / `osascript`，不启动 Compositor，不弹出访达。用户若已在 Compositor 中打开该工程，写入后约 0.3 秒自动刷新。要看效果，读检查图（见下文）。
- 只用脚本写工程。要手改 `manifest.json` 时先读 [references/writing-comp-files.md](references/writing-comp-files.md)，改完运行 `--check`：Compositor 遇到任何一处不合规都会**静默拒绝整个工程**，画布没有任何变化，也没有提示。
- 不覆盖已有工程：对已存在的 `.comp` 用 `--init` 会报错。只有用户明确同意替换时才加 `--force`，它会删除原有图层。
- 依赖：`python3 -m pip install "opencv-python-headless<5" pillow numpy`，可选 `ai-edge-litert`（人像分割模型，皮肤蒙版准得多；不要装 `mediapipe` 整包）。人脸关键点用 Apple Vision：macOS 加 Xcode Command Line Tools 的 `swiftc`，第一次运行自动编译并缓存，没有窗口。拿不到 Vision 时退回 OpenCV，眼神光、骨相的蒙版改为黑色需手涂，脚本会警告。细节见 modules.md「依赖与输入」。
- 读脚本输出再汇报：退出码 0、出现 `[SUCCESS]` 和图层栈才算成功。退出码非 0 时把 `[Error]` 的原因告诉用户，此时工程保持原样。把 `[WARN]` 也转告用户。

## 用法

`${CLAUDE_SKILL_DIR}` 是本 SKILL.md 所在目录；在不展开该变量的环境里，换成加载技能时给出的技能目录。

```bash
B="${CLAUDE_SKILL_DIR}/scripts/retouch_builder.py"

# 新建：DR5 工程（高低频、磨皮、3D 骨相、双曲线、眼神光、胶片颗粒、观察组）
python3 "$B" --init "/path/portrait.jpg" --comp "/path/Portrait.comp" --preset dr5

# 新建：影棚工程（高低频、中性灰、双曲线、观察组），留给手工精修
python3 "$B" --init "/path/portrait.jpg" --comp "/path/Portrait.comp" --preset studio

# 常用：中性灰调光影 + DR5 磨皮（黑蒙版，手动刷出）+ 观察组；新建或已有工程都行，再跑一次不会重复加层
python3 "$B" --comp "/path/Portrait.comp" --preset daily

# 磨皮轻一点（只影响磨皮；--strength 管辉光和调色）
python3 "$B" --comp "/path/Portrait.comp" --preset daily --skin-strength 0.6

# 磨皮默认像 PS 里 DR5 手动磨皮：磨皮层带黑蒙版，用白画笔刷哪里磨哪里；要按识别出的皮肤自动盖上，写 auto
python3 "$B" --comp "/path/Portrait.comp" --delicious-skin auto

# 高低频换半径（像素；auto 回到按脸宽）：还没手修过的高低频按新半径原地重做，手修过的不动
python3 "$B" --comp "/path/Portrait.comp" --freq-sep --blur-radius 8

# 给已有工程追加模块（可同时给多个），--strength 调整力度
python3 "$B" --comp "/path/Portrait.comp" --sculpt --eyes --strength 0.7

# 辉光：暖金色黑柔；双色（内圈金、外圈粉）；一次加两种
python3 "$B" --comp "/path/Portrait.comp" --glow mist:gold
python3 "$B" --comp "/path/Portrait.comp" --glow mist:gold,rose
python3 "$B" --comp "/path/Portrait.comp" --glow mist --glow halation:red

# 调色：胶片暖调（文件夹不透明度 70%）
python3 "$B" --comp "/path/Portrait.comp" --look film --strength 0.7

# 手修之后（比如在高低频上修过），按现在的画面重算磨皮层；先让用户在 Compositor 里 ⌘S 保存
python3 "$B" --comp "/path/Portrait.comp" --refresh

# 渲染预览：把整个工程（含所有调整层）合成成一张图，不打开 Compositor；默认写到 Portrait.render.jpg
python3 "$B" --comp "/path/Portrait.comp" --render

# 只校验，不改动（例如手改 manifest 之后）
python3 "$B" --comp "/path/Portrait.comp" --check
```

输入支持 JPEG、PNG、TIFF（含 16 位、EXIF 方向、嵌入的 ICC）；HEIC、RAW 先转换（`sips -s format tiff in.heic --out in.tif`）。16 位照片新建时保留 16 位（格式 12，要 Compositor 1.5 以上）；给旧版 Compositor 用加 `--8bit`。已有工程可以是 Compositor 存的格式 12 到 21，版本保持不变。细节见 modules.md「依赖与输入」。

## 预设

| 预设 | 图层（自下而上，［］是文件夹） | 需要 `--init` |
|---|---|---|
| `daily` | ［🧴 皮肤：DR5 磨皮（88%，黑蒙版）］ → ［🌓 光影：中性灰］ → 观察组。日常流程：中性灰调光影，再刷出要磨的皮肤 | 否 |
| `dr5` | 原图 → ［🎚️ 高低频］ → ［🧴 皮肤：DR5 磨皮（88%，黑蒙版）］ → ［🌓 光影：3D 骨相、双曲线］ → ［😊 五官：眼神光］ → 胶片颗粒 → 观察组 | 是 |
| `studio` | 原图 → ［🎚️ 高低频］ → ［🌓 光影：中性灰、双曲线］ → 观察组 | 是 |

`dr5`、`studio` 只能用于新建工程；给已有工程加同样的内容时，把模块逐个列出即可，例如 `--freq-sep --delicious-skin --sculpt --dual-curves --eyes --grain --inspection`。`--without 模块` 可以从预设里去掉某项。

## 模块

| 参数 | 生成内容 | 图层 · 所在文件夹 | 用户接下来做什么 |
|---|---|---|---|
| `--freq-sep` | 高低频，半径按最大那张脸的宽度，或 `--blur-radius` 给的 | 两层像素 · 「🎚️ 高低频」紧贴原图 | 低频匀色、高频修瑕疵；手修后 `--refresh` |
| `--delicious-skin` | DR5 磨皮：先祛痘（图层名标"祛痘 N 处"），再保形平滑，五官旁放轻；算到全身皮肤，黑蒙版 | 像素 · 88% · 黑蒙版 · 「🧴 皮肤」 | 白画笔刷出要磨的区域，低流量分层刷 |
| `--delicious-skin auto` | 同上，蒙版按识别出的皮肤自动盖上 | 像素 · 88% · 皮肤蒙版 · 「🧴 皮肤」 | 调不透明度；检查、修补蒙版 |
| `--sculpt` | 3D 骨相：Tonal Contrast 实时调整层（格式 18 起；更早的工程是中频反差像素层） | 调整层 · 预涂鼻梁、颧骨、眉骨、下巴 · 「🌓 光影」 | 白画笔加强、黑画笔收回；双击改强度 |
| `--neutral-gray` | 50% 中性灰 | 像素 · 柔光 · 「🌓 光影」 | 浅灰、深灰画笔涂亮、涂暗 |
| `--dual-curves` | 双曲线：提亮、压暗各一层 | 曲线调整层 · 明度 · 黑蒙版 · 「🌓 光影」 | 低流量白画笔做 dodge & burn |
| `--eyes` | 眼神光：Sharpen 实时调整层（格式 19 起；更早的工程是锐化像素层） | 调整层 · 70% · 预涂虹膜 · 「😊 五官」 | 调不透明度 |
| `--look 风格` | 调色：`airy` 日系清透、`film` 胶片暖调、`cool-fair` 冷白皮、`creamy` 奶油肌、`hk` 港风、`bw` 黑白 | 两三个实时调整层 · 「🎬 调色 · 风格」文件夹，不透明度 = `--strength` | 调文件夹不透明度；双击各层微调 |
| `--glow [风格[:颜色[,颜色]]]` | 辉光：`mist` 黑柔（默认）、`halation` 胶片光晕、`dream` 梦幻柔焦；颜色 `natural` `white` `gold` `film` `red` `rose` `lavender` `ice` `teal` 或 `#RRGGBB`，两个颜色是双色；可写多次叠加 | 滤色像素层（`dream` 是高斯模糊调整层）· 白蒙版 · 「🌟 辉光」 | 调不透明度；黑画笔擦掉不想发光处（如眼睛）；颜色叠加里换色 |
| `--grain` | 胶片颗粒，尺寸随分辨率 | Grain 调整层 · 观察组之下 | 双击调强度、尺寸 |
| `--inspection` | 两个观察组：黑白加反差、颜色差异 ×3，按这张脸校准 | 默认隐藏的调整层文件夹 · 最顶 | 打开其一检查，交付前关闭 |

其他参数：
- `--strength 0.1–2`：辉光和调色的强度；没给 `--skin-strength` 时也管磨皮。`--skin-strength 0.1–2`：只管磨皮的不透明度和祛痘灵敏度，记进图层名，`--refresh` 照用；皮肤本来就好用 0.5–0.7。
- `--texture natural|soft|smooth`：磨皮保留多少细纹理（默认 `natural`）。`--engine form|guided|bilateral`：磨皮引擎（默认 `form`，保形）。`--blur-radius 像素|auto`：只管高低频的半径（磨皮有自己的），最大 200；已有高低频时，给了不同的半径（或给 `auto` 而原来是手动的）就原地重做，但只在两层还没被手修过（加起来仍等于原图、没加蒙版、没挪动）时，否则不动并提示。
- `--no-vision`：不用 Apple Vision 找人脸。`--no-parsing`：不用人像分割模型。`--without 模块`：从预设里去掉某项，可重复。
- `--force`：配合 `--init` 替换已有工程。`--8bit`：配合 `--init`，只写 1.5 以前的 Compositor 读得懂的内容（8 位、像素层、格式 11）。`--no-checks`：不生成检查图。`--progress`：每步打印 `[STEP]`。
- `--render [文件]`：按 Compositor 导出的方式把整个工程合成一张图（默认 `<名称>.render.jpg`），可单独用（只读工程）；用 Read 看它就能确认最终效果，不用打开 Compositor。
- `--refresh`：手修之后按现在的画面重算磨皮层（以及旧格式工程里的眼神光像素层），不加新图层，见下文。

算法、阈值、和 DR5 插件的对比、辉光颜色表、各参数的取值细节：modules.md「模块」。

## 图层文件夹

新图层按用途放进文件夹，自下而上：🎚️ 高低频 → 🧴 皮肤（这两个紧贴原图）→ 🌓 光影 → 😊 五官 → 🎬 调色（每种风格一个文件夹）→ 🌟 辉光 → 胶片颗粒 → 观察组（最顶，这两个不放文件夹）。文件夹是穿透的，不改变画面；文件夹的不透明度乘到里面每一层。文件夹和图层都靠名字开头的 emoji 识别，改名时保留 emoji（手动磨皮层保留开头的 🔥✋）。以前版本的工程（图层没有分文件夹）照样能追加。放置规则细节见 modules.md「图层文件夹」。

## 手修之后重算（`--refresh`）

磨皮层是生成时按它下方画面算出的像素快照，之后在高低频或原图上手修，修过的地方会被它盖住：运行 `--refresh` 按现在的画面原地重算（图层 id、名字、蒙版、不透明度不变，用户擦改过的蒙版都保留）。**先让用户在 Compositor 里 ⌘S 保存**，脚本只读得到磁盘上的工程。实时的骨相、眼神光和所有调整层不需要重算；辉光要更新就删掉重新生成。和其他模块一起给时先重算，新加的层基于重算后的结果。细节见 modules.md「手修之后重算」。

## 检查图

脚本在工程旁写 `<名称>.checks/`（不在 `.comp` 包里）并打印路径。**每次生成后用 Read 工具看 `overview.jpg`**，一张图里是修前、修后、皮肤蒙版、五官预涂；加了 `--look`、`--glow` 时再看 `look.jpg`、`glow.jpg`（整幅的加前、加后对比）。图例：绿色是皮肤蒙版，洋红是被排除的头发，蓝线圈出磨皮避开的五官，黄框是人脸，红圈是祛痘处；青色是虹膜、橙色是骨相的预涂，白点是关键点。确认蒙版没盖到头发或衣服，红圈没落在五官或发际线上，预涂落在对应五官上，修后不发假，再把发现告诉用户。磨皮是默认的手动刷出（黑蒙版）时，修后图里还看不出磨皮，这是正常的：要等用户在 Compositor 里刷。效果过重就用更小的 `--strength` 重新生成；关键点明显错位就加 `--no-vision` 重做，并告诉用户这些蒙版需要手涂。

## 须知

- 重复运行不会堆第二套图层：高低频（换了 `--blur-radius` 且没手修过时按新半径重做）、中性灰、骨相、实时的眼神光、同一种调色、颗粒、观察组已有就跳过（`[SKIP]`），磨皮按这次的参数原地重做（`重做 · 保留蒙版`），双曲线只补缺的那一层，只有辉光会叠加。没写 `auto`/`manual` 时，工程里已有的磨皮是哪种就重做哪种。
- 像素层是快照，调整层是实时的。骨相、眼神光在格式 18/19 起的工程里是实时调整层，更早的工程里是像素层，脚本会说明；新建工程的版本跟着内容走（含眼神光 19，`--8bit` 固定 11），已有工程保持版本，在 Compositor 里保存一次就升到最新格式（1.5.x 是 20，1.6 是 21）。
- 写入是安全的：新文件先放在包内隐藏文件夹 `.retouch/`，校验通过后几毫秒内一起落地，Compositor 只重载一次；运行中工程若被 Compositor 保存过，脚本报错退出、不改动，再跑一次即可。
- `app/` 里是这个脚本的 macOS 图形界面「Compositor 工具」：自动认出 Compositor 正在编辑的工程、一键精修、批量处理，不用和 AI 对话。用户想点按钮时告诉他们，不要替用户启动它，也不要打开访达。
- 用户要的是 PhotoCraft（`.pcraft` 文档）时，用 `photocraft-retouch/` 里的技能（它有自己的 SKILL.md，同样的模块，用这个脚本计算图层）；「Compositor 工具」也有 PhotoCraft 标签页。
- 皮肤蒙版、祛痘、关键点都是近似：侧脸、遮挡、很小的脸、最深的肤色可能不准，请看检查图。调色是全图的，皮肤也跟着变。渲染复现了像素层的混合和常见调整层；动感模糊、添加杂色、颜色叠加以外的图层效果、剪贴蒙版、旋转的图层没有复现，会警告。
- 修改脚本后运行 `tests/run.sh`（说明见 [tests/README.md](tests/README.md)）。其余须知见 modules.md「须知」。
