---
name: photocraft-retouch
description: >-
  Only when PhotoCraft or a .pcraft file is named (for Compositor or a .comp, or no app named, use compositor-retouch).
  Builds layered portrait-retouching documents for PhotoCraft, the open-source image editor (.pcraft files), headless
  through photocraft-cli serve: the compositor-retouch modules (DR5-style skin smoothing, neutral gray and dual curves
  for dodge & burn, 3D sculpt, eye brilliance, frequency separation, color grades airy/film/cool-fair/creamy/HK/B&W,
  glow, grain, observation folders) as PhotoCraft layers, masks, adjustment layers and smart filters the user keeps
  editing in PhotoCraft. Use when the user mentions PhotoCraft or a .pcraft file, or wants a retouching setup to open
  in PhotoCraft: PhotoCraft 修图工程, PhotoCraft 人像精修 / 磨皮 / 中性灰 / 双曲线 / 高低频 / 调色 / 辉光, .pcraft 重算, 渲染预览.
---

# PhotoCraft 人像精修工程

`scripts/pcraft_retouch.py` 从一张照片新建 PhotoCraft 文档（`.pcraft`），或给已有文档追加精修图层。图层由上一级 compositor-retouch 的 `scripts/retouch_builder.py` 计算（同一套算法、预设和参数），再通过 PhotoCraft 的命令行引擎 `photocraft-cli serve` 搭进文档，全程没有窗口。每种图层怎么对应、和 Compositor 版有什么不同，见 [references/mapping.md](references/mapping.md)。

## 规则

- **不要打开任何 GUI**：不用 `open` / `osascript`，不启动 PhotoCraft，不弹出访达，不打开文件夹。要看效果，读检查图或 `--render` 的图。用户自己在 PhotoCraft 里打开 `.pcraft`。
- **PhotoCraft 不会自动刷新**：给已有文档追加模块或 `--refresh` 之前，先让用户在 PhotoCraft 里 ⌘S 保存；跑完让用户按 F12（文件 › 恢复）载入，先别按 ⌘S，否则会盖掉脚本的修改。运行期间文档被保存过时，脚本报错退出、不写入，再跑一次即可。
- **联动时这两步是自动的**：PhotoCraft 是用「Compositor 工具」的「联动启动 PhotoCraft」启动的（它的控制端口开着）时，脚本对 PhotoCraft 里开着的文档先替用户保存（输出 `[OK] PhotoCraft: saved … first`），写入后自动重新载入，回到原来的标签位置（`[OK] PhotoCraft: … loaded again`）；这时不用提醒 ⌘S 和 F12，但要告诉用户这个文档在 PhotoCraft 里的历史记录从这里重新开始（撤销不回这次之前）。联动只管用户文件夹里的文档：别处的（外接硬盘）脚本输出一行 `[NOTE]`，照旧手动。运行期间用户在 PhotoCraft 里改了这个文档，脚本报错退出、不写入，再跑一次即可（会先替用户保存）。同一个文档开在两个标签里，或开着智能对象的「编辑内容」标签时，脚本也报错退出、不写入：让用户关掉多余的标签再跑。上一次没能重新载入（`[WARN] PhotoCraft didn't load … again`）的标签比文件旧：之后在那里的改动脚本不会替用户保存，报错让用户先按 F12。联动没连上时脚本输出一行 `[NOTE]`；PhotoCraft 没开着时不输出，照旧手动。第一次用联动时，可以让用户在那台 Mac 上运行 `python3 "${CLAUDE_SKILL_DIR}/scripts/link_check.py"`，它在一个临时文档上把每一步试一遍并打印结果，不碰用户的文档。
- 不覆盖已有文档：对已存在的 `.pcraft` 用 `--init` 会报错。只有用户明确同意替换时才加 `--force`，它会丢掉原有图层。
- 依赖：和 compositor-retouch 一样，`python3 -m pip install "opencv-python-headless<5" pillow numpy`，可选 `ai-edge-litert`；人脸关键点用 Apple Vision（Xcode Command Line Tools 的 `swiftc`）。这个文件夹要放在 compositor-retouch 里面（或用环境变量 `RETOUCH_BUILDER` 指向 `retouch_builder.py`）。
- PhotoCraft 命令行工具：按 `PHOTOCRAFT_CLI` 环境变量、`PATH` 里的 `photocraft-cli`、缓存的顺序找；都没有时，第一次运行从 PhotoCraft 的 GitHub 发布页下载 0.19.0 的 macOS 版（约 107 MB）到 `~/Library/Caches/photocraft-retouch/`，按 SHA-256 和 Apple 公证校验后使用。别的版本也能跑，但会警告：命令参数在版本之间还在变。
- 读脚本输出再汇报：退出码 0、出现 `[SUCCESS]` 和图层栈才算成功。退出码非 0 时把 `[Error]` 的原因告诉用户，此时文档保持原样；只有 `[Error]` 里说 `was written with the new layers` 时（写入之后才停下，例如出图那一步被中断）文档已经写入了新图层。把 `[WARN]` 和 `[NOTE]` 也转告用户。

## 用法

`${CLAUDE_SKILL_DIR}` 是本 SKILL.md 所在目录；在不展开该变量的环境里，换成加载技能时给出的技能目录。

```bash
P="${CLAUDE_SKILL_DIR}/scripts/pcraft_retouch.py"

# 新建：DR5 工程（高低频、磨皮、3D 骨相、双曲线、眼神光、胶片颗粒、观察组）
python3 "$P" --init "/path/portrait.jpg" --pcraft "/path/Portrait.pcraft" --preset dr5

# 新建：影棚工程（高低频、中性灰、双曲线、观察组）
python3 "$P" --init "/path/portrait.jpg" --pcraft "/path/Portrait.pcraft" --preset studio

# 常用：中性灰 + DR5 磨皮 + 观察组；新建或已有文档都行，再跑一次不会重复加层
python3 "$P" --pcraft "/path/Portrait.pcraft" --preset daily

# 追加模块、调色、辉光（已有文档：先让用户在 PhotoCraft 里 ⌘S）
python3 "$P" --pcraft "/path/Portrait.pcraft" --look film --strength 0.7
python3 "$P" --pcraft "/path/Portrait.pcraft" --glow mist:gold,rose

# 手修之后（比如在高低频上修过），按现在的画面重算磨皮，并重新盖印骨相、眼神光、梦幻柔焦
python3 "$P" --pcraft "/path/Portrait.pcraft" --refresh

# 渲染预览：PhotoCraft 自己合成整个文档，默认写到 Portrait.render.jpg；只给 --pcraft 时列出图层栈
python3 "$P" --pcraft "/path/Portrait.pcraft" --render
```

参数和 compositor-retouch 的 `retouch_builder.py` 相同，只是用 `--pcraft` 代替 `--comp`：`--preset daily|dr5|studio`、`--strength`、`--skin-strength`、`--texture`、`--engine`、`--blur-radius 像素|auto`（只管新建的高低频：文档里已有的不会按新半径重做，要换先删掉「🎚️ 高低频」组）、`--without`、`--no-vision`、`--no-parsing`、`--no-checks`、`--progress`。没有 `--8bit` 和 `--check`：照片是 16 位时文档就是 16 位，文档由 PhotoCraft 自己写。输入支持 JPEG、PNG、TIFF；HEIC、RAW 先转换（`sips -s format tiff in.heic --out in.tif`）。

## 模块

| 参数 | PhotoCraft 里是什么 | 用户接下来做什么 |
|---|---|---|
| `--freq-sep` | 「🎚️ 高低频」组：低频、高频（线性光）两个像素层，紧贴原图 | 低频匀色、高频用仿制图章修瑕疵；修完 ⌘S，再 `--refresh` |
| `--delicious-skin` | 「🧴 皮肤」组：DR5 磨皮，智能对象 · 88% · 黑蒙版，算到全身皮肤（默认，和 PS 手动磨皮一样）；没写 `auto`/`manual` 时，文档里已有哪种就重做哪种 | 白画笔刷出要磨的地方 |
| `--delicious-skin auto` | 同上，蒙版按识别出的皮肤自动盖上 | 调不透明度；在蒙版上擦改 |
| `--sculpt` | 「🌓 光影」组：3D 骨相，盖印的智能对象 + 高反差保留智能滤镜 · 柔光 · 预涂鼻梁、颧骨、眉骨、下巴 | 白画笔加强、黑画笔收回；双击智能滤镜改半径 |
| `--neutral-gray` | 「🌓 光影」组：50% 中性灰像素层 · 柔光 | 浅灰、深灰画笔涂亮、涂暗 |
| `--dual-curves` | 「🌓 光影」组：提亮、压暗两个曲线调整层 · 明度 · 黑蒙版 | 低流量白画笔做 dodge & burn |
| `--eyes` | 「😊 五官」组：眼神光，盖印的智能对象 + USM 锐化 · 70% · 预涂虹膜 | 调不透明度；双击智能滤镜改锐化量 |
| `--look 风格` | 「🎬 调色 · 风格」组：曲线调整层 + 两个颜色查找层（按 Compositor 算法烘焙的 3D LUT），强度记在每层的不透明度上 | 调各层不透明度；要拖滑块就自己加一层色彩平衡、色相/饱和度 |
| `--glow [风格[:颜色[,颜色]]]` | 「🌟 辉光」组：`mist`、`halation` 各是一个滤色组，里面是正常模式的像素层 + 颜色叠加；双色是带蒙版的子组；`dream` 是盖印的智能对象 + 高斯模糊 · 滤色 | 调不透明度；黑画笔擦掉不想发光处；在里面那层的颜色叠加里换颜色（别用"将内部效果混合成组"，它会让整个文档改用 CPU 重绘） |
| `--grain` | 胶片颗粒：50% 灰叠加的智能对象 + 添加杂色 | 调不透明度；双击智能滤镜改数量 |
| `--inspection` | 两个默认隐藏的观察组：黑白 + 反差、颜色差异 ×3 | 打开其一检查，交付前关闭 |

调色、辉光的风格和颜色，各模块的算法、阈值，见 compositor-retouch 的 `references/modules.md`（上一级文件夹），两边一样。

## 须知

- **盖印层是快照**：骨相、眼神光、梦幻柔焦在 Compositor 里是实时调整层，PhotoCraft 没有对应的调整层，所以是"盖印下方画面的智能对象 + 智能滤镜"。在下面手修过就 `--refresh`；之后追加的模块插在它们下面时，脚本会自动重新盖印。
- 重复运行不会堆第二套图层：高低频、中性灰、骨相、眼神光、同一种调色、颗粒、观察组已有就跳过（`[SKIP]`），磨皮按这次的参数原地重做（换智能对象内容，蒙版保留），双曲线只补缺的那一层，只有辉光会叠加。
- 新图层按用途放进组，顺序和 compositor-retouch 一样：🎚️ 高低频 → 🧴 皮肤 → 🌓 光影 → 😊 五官 → 🎬 调色 → 🌟 辉光 → 胶片颗粒 → 观察组。组和图层都靠名字开头的 emoji 识别，改名时保留 emoji。
- **检查图**：脚本在文档旁边写 `<名称>.checks/`，内容和 compositor-retouch 的一样（`overview.jpg` 等，由它计算）。**每次生成后用 Read 工具看 `overview.jpg`**，确认蒙版没盖到头发或衣服、红圈没落在五官上、预涂落在对应五官上、修后不发假，再把发现告诉用户。`--render` 的图是 PhotoCraft 自己合成的最终效果。
- 文档较大：dr5 文档 8.5 MP 约 170 MB、24 MP 约 420 MB，新建分别约 5 秒、13 秒。24 MP 时 PhotoCraft 引擎约占 3.7 GB 内存、retouch_builder 约 4 GB；新建时两者先后运行，追加模块时同时运行。
- compositor-retouch 的图形界面「Compositor 工具」（上一级的 `app/`）有 PhotoCraft 标签页，调用的就是这个脚本：用户想点按钮、批量处理时告诉他们。不要替用户启动它，也不要打开访达。
- 修改脚本后运行 `tests/run.sh`：逐项对比 PhotoCraft 的画面和 retouch_builder 的渲染，再测追加、重复运行、`--refresh` 和出错时不改文档。说明见 [references/mapping.md](references/mapping.md)。
