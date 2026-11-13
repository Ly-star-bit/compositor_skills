# Compositor 图层 → PhotoCraft 图层（photocraft-retouch）

[SKILL.md](../SKILL.md) 的展开版。图层都由上一级 compositor-retouch 的 `scripts/retouch_builder.py` 算出（写进临时 `.comp`），本技能再用 `photocraft-cli serve`（没有窗口）在 PhotoCraft 文档里逐层搭出来。下表的"误差"是 `tests/run.sh` 在 NASA 标准照（2615×3269）上量的：PhotoCraft 导出的画面对比 retouch_builder 自己的渲染（它的测试说它和 Compositor 相差 1–2 级以内）。

## 对应方式

| Compositor（脚本写的） | PhotoCraft | 误差 |
|---|---|---|
| 像素层（原图、高低频、中性灰、AI 光影画好的中性灰、辉光） | 像素层，`doc.place` 加 `smartObject:false`，原样放入 | 0（AI 光影：最大 1） |
| 磨皮层 | 智能对象：`--refresh` 和重做时用 `doc.replaceSmartObject` 换内容，图层 id、名字、不透明度、蒙版都不变 | 0 |
| 蒙版（8 位灰度 PNG） | 把 PNG 的红通道读成选区（`select.loadSelection`），再"显示选区"；1×1 的纯黑、纯白用"隐藏全部"、"显示全部" | 256 级全保留，0 |
| 文件夹 | 穿透的组，不透明度 100%；Compositor 文件夹的不透明度乘到组里每一层上（见下文） | 见调色 |
| 文件夹蒙版（双色辉光） | 组蒙版 | 1 |
| 曲线 | 曲线调整层；Compositor 的保形曲线重采样成最多 19 个点 | 观察组最大 1 / 5 级 |
| 黑白 | 黑白调整层，权重相同 | 0 |
| 色相/饱和度、色彩平衡（调色） | 颜色查找调整层：按脚本移植的 Compositor 算法烘焙的 33³ 3D LUT，三线性插值，名字后加"· 3D LUT" | 6 种调色（强度 0.7）最大 2–5 级，平均 < 0.5 |
| Tonal Contrast（3D 骨相） | 盖印 → 智能对象 → 同半径的"高反差保留"智能滤镜，柔光 | 效果相关系数 0.88，强度 1.02 倍 |
| Sharpen（眼神光） | 盖印 → 智能对象 → USM 锐化智能滤镜（数量、半径相同） | 相关系数 0.999，强度 0.99 倍 |
| 高斯模糊调整层（梦幻柔焦） | 盖印 → 智能对象 → 高斯模糊智能滤镜，滤色 | 最大 10 级（画面边缘），平均 0.09 |
| Grain（胶片颗粒） | 50% 灰、叠加模式的智能对象，"添加杂色"（颗粒大时再加轻微高斯模糊）；杂色数量按这张照片中间一块实测，使强度和脚本的颗粒一致 | 标准差 3.19 对 2.97 |
| 辉光（黑柔、胶片光晕）和它的颜色叠加 | 一个混合模式为滤色的组，里面是正常模式的像素层 + 图层样式"颜色叠加"：组内先上色、整组再滤色，和 Compositor 一样 | 1 |
| `--render` | PhotoCraft 自己合成导出（`doc.save` 存 PNG，再按需转 JPEG） | — |

## 和 Compositor 版用起来的不同

- **没有自动刷新**：PhotoCraft 不监视文件。脚本改完文档后，用户在 PhotoCraft 里按 F12（文件 › 恢复）载入。恢复会丢掉没保存的改动，所以追加模块、`--refresh` 之前，用户要先在 PhotoCraft 里 ⌘S。运行期间文档被保存过时，脚本报错退出，不写入。
- **联动**：PhotoCraft 用控制端口启动（`photocraft --control 47917 --control-token-file … --automation-read-root ~ --automation-write-root ~`，「Compositor 工具」的「联动启动 PhotoCraft」就是这样启动它，并把端口、令牌文件、文件夹写进 `~/Library/Application Support/compositor-retouch/photocraft-link.json`；也可以用 `PHOTOCRAFT_CONTROL_PORT`、`PHOTOCRAFT_CONTROL_TOKEN_FILE` 指定）时，`photocraft.py` 的 `AppLink` 连上它：PhotoCraft 开着这个文档并且有没保存的改动，就先 `app.save` 存回它自己的 `.pcraft`（存不了就报错退出、不写入）；脚本照常在后台引擎里做完；写入之前重新连一次（PhotoCraft 会断开空闲 30 秒的控制连接），这时它又有了没保存的改动（运行期间用户在改）就报错退出、不写入。原子替换文件之后，先 `app.open` 再打开一份，确认打开了才 `file.close` 关掉旧的，`document.move` 放回原来的标签位置，原来在前面的文档仍在前面；打不开时旧标签原样留着。不用文件 › 恢复，是因为自动化不许运行按路径读文件的命令（`file.revert` 被拒），关掉再打开会清空这个文档的历史记录。同一个文件开在两个标签里（自动化的 `app.open` 不会切到已开的标签，GUI 的打开会），或者开着某个智能对象的「编辑内容」标签（关掉它所属的文档会让它断开，改动再也回不去；只有当前文档看得出来，所以脚本把没有文件的标签逐个切到前面，看 `engine.commands` 里「存储内容」能不能用），脚本报错退出、不写入，让用户先关掉多余的标签。运行前 PhotoCraft 开着这个文档、写入前却连不上它（例如连接数满了），也报错退出、不写入；连接被拒绝说明 PhotoCraft 已经退出，照常写入。重新载入时新标签打开之后、旧标签关掉之前出了事（包括中断），脚本把新标签再关掉。PhotoCraft 开着这个文档、写入后却没能重新载入时（包括联动不管的文档，以及写入后被中断），那个标签比文件旧：脚本把文件此刻的样子（设备、inode、修改时间、大小）记进 `~/Library/Application Support/compositor-retouch/photocraft-stale.json`，文件没再变过而那个标签又有了没保存的改动，就不替用户保存（那样会把这次写入的图层盖掉），报错让用户先按 F12。PhotoCraft 的自动化只读写授权文件夹（用户文件夹）里的文件，路径里每一级名字也有限制（不能有 `:` 或 `\`，不能以点或空格结尾，不能是 con、aux、nul、com1 这类 Windows 设备名，各平台一样）：不合规的文档联动不管，照旧手动（名字限制见下一条）。文档按同一个文件比对（`samefile`），大小写或 é 的写法不同也认得出。桥接模式下 `doc.place`、`doc.replaceSmartObject` 都被拒，所以图层仍在后台引擎里搭，不直接搭进 PhotoCraft 的窗口（那样还会在历史面板里留下上百步）。令牌只能在本机用，但拿到它就能操控 PhotoCraft，并读写授权的文件夹（用户文件夹）。`scripts/link_check.py` 在一台装着 PhotoCraft 的 Mac 上实测这些步骤；`tests/check.py` 的 `link` 一组用一个照协议实现、背后是真引擎的替身检查同样的流程。
- **文档名**：文档保存时记下文件名，PhotoCraft 显示的是它。脚本在临时文件夹里用文档自己的文件名保存（以前存成 `out.pcraft`，PhotoCraft 里就显示成 out.pcraft），下次追加时会改过来。文件名不合引擎的规矩（见上一条）时，换成最接近的合规名字：`a:b.pcraft`（访达里显示成 a/b）→ `a-b.pcraft`，`con.pcraft` → `con_.pcraft`。
- **盖印层是快照**：骨相、眼神光、梦幻柔焦在 Compositor 里是实时调整层；PhotoCraft 没有这几种调整层，改成"盖印下方画面的智能对象 + 智能滤镜"。滤镜参数可以双击智能滤镜改，但内容不跟着下面变。`--refresh` 会按现在的画面重新盖印它们（智能滤镜照旧），追加模块时插在它们下面的新图层也会触发重新盖印；新图层都在它们上面时盖印不动（省掉导出和替换），输出一行 `[NOTE]` 提醒：在 PhotoCraft 里改过它们下面的图层，就跑一次 `--refresh`。
- **LUT 层不能拖滑块**：色相/饱和度和色彩平衡两种层，PhotoCraft 的算法和 Compositor 差得多（同样的参数，色彩平衡平均差 4–7 级，冷白皮的色相/饱和度最大差 39 级），所以烘焙成 LUT。要微调就降不透明度，或者自己再加一层色彩平衡、色相/饱和度。
- **文件夹不透明度**：调色、辉光文件夹的强度记在每一层的不透明度上，文件夹本身是 100%。Compositor 把文件夹不透明度乘到每一层；PhotoCraft（和 Photoshop 一样）是整组合成后再按不透明度混合，两层以上的组两者差得多（70% 时最大差 32 级）。用户之后再调低文件夹不透明度也行，只是按 PhotoCraft 的算法。
- **辉光是滤色组**：每个黑柔、胶片光晕辉光是一个滤色组，里面的层是正常模式。换色就改（或给原色辉光加上）这一层的"颜色叠加"，因为整组是滤色，出来的是光而不是贴上去的颜色。不要改用混合选项里的"将内部效果混合成组"：那是高级混合，PhotoCraft 的 GPU 合成器不支持，只要有一层用了，整个文档每次重绘都改用 CPU（大文档每次一秒多）。
- **颗粒的花纹不同**：强度对齐了，花纹是 PhotoCraft 的随机杂色。
- **16 位**：照片是 16 位时，文档也是 16 位。

## 实现细节

- `serve` 只能读写它的根目录：脚本在 `.pcraft` 所在文件夹里建一个隐藏的临时文件夹 `.pcraft-retouch-…`，把文档克隆进去（APFS 克隆，不占空间）、在里面改、存好后原子替换原文件，结束时删除。路径里不能有 `..`。
- 混合模式用 Photoshop 带空格的写法（`"Linear Light"`、`"Soft Light"`）。
- 新建调整层时有选区，选区就成为它的蒙版；没有选区是白蒙版，要全黑得先删蒙版再"隐藏全部"。
- `doc.render` 最多 2048 像素边长，全尺寸画面用 `doc.save` 存 PNG。
- 颜色查找的 `file` 参数在后台模式下被拒（按路径读文件的命令都被禁用），LUT 用 `data` 参数直接传文本（33³ 约 740 KB）。
- 追加模块时，先把插入位置以上的图层和观察组都隐藏、导出插入位置以下的画面，交给 retouch_builder 用 `--init` 算新图层，再搭进文档、移到位置。
- PhotoCraft 命令行工具固定用 0.19.0（`photocraft.py` 里的 `VERSION` 和 SHA-256）：命令参数在版本之间还在变。换版本要改这两项并重跑 `tests/run.sh`。
