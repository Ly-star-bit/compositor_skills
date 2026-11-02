# Compositor `.comp` 格式速查（写 format version 11，读到 20）

依据 [robbietilton/Compositor](https://github.com/robbietilton/Compositor) 的 `docs/writing-comp-files.md`、`docs/project-format.md` 和 `Compositor/IO/ProjectStore.swift`（MIT，commit `22c7b8d5`，2026-10-08；版本 14–20 的规则和重载机制按 2026-10-26 的 `6d49a239`，见文末）；图层效果一节依据 `Compositor/Document/LayerEffects.swift` 和 `Compositor/IO/ImageExporter.swift`（commit `b4bfdea8`，2026-10-09）；16 位和 HDR 图层（版本 12、13）依据 commit `e8c9e29`（2026-10-12，Compositor 1.5.1）。下面的 JSON 形状由 Compositor 自己的 Swift `Codable` 类型编码得到。

> **Compositor 遇到任何一处不合规就静默拒绝整个工程**：画布保持原样，没有任何提示。手改后先运行 `retouch_builder.py --comp X.comp --check`。

## 包结构

```text
Portrait.comp/
├── manifest.json
├── images/
│   ├── <ID>.png          图层像素：8 位 PNG（图层用 RGBA）
│   └── <ID>.mask.png     蒙版：8 位灰度、无 alpha；白 = 显示，黑 = 隐藏
└── QuickLook/Preview.jpg 访达空格预览；改动工程后删除，Compositor 下次保存时会重写
```

Compositor 1.5 起保存的工程是版本 13（新建工程默认 12 以上），图层图片还可以是：

- 16 位 PNG（版本 12 起，只限图层图片，蒙版仍是 8 位）：导入 16 位照片时保留精度。manifest 不变，PNG 自己的位深说明它是 16 位图层。
- OpenEXR（版本 13 起），文件名 `<ID>.exr`：HDR 图层，可以比白更亮。由 ImageIO 写出，存的是**线性光、未预乘**的 sRGB 值（白 = 1）；读的时候先转回 sRGB 编码，Compositor 显示和编辑的是截到白的 8 位副本。

本 skill 新建的工程仍写版本 11（旧版 Compositor 也能打开）；给版本 12、13 的工程加图层时保持原版本号，新图层都是 8 位 PNG。

## manifest.json

```json
{
  "format": "com.compositor.project",
  "version": 11,
  "colorSpace": "sRGB",
  "documentID": "0C5E7A91-3B2D-4F6A-8E1C-9D0B7A6F5E4D",
  "width": 4000,
  "height": 6000,
  "resolution": 300,
  "activeLayerID": "6F1D3C2A-0B7E-4E8A-9C4D-2A1B3C4D5E6F",
  "layers": [
    {
      "id": "6F1D3C2A-0B7E-4E8A-9C4D-2A1B3C4D5E6F",
      "name": "原图 (Original)",
      "imageFile": "6F1D3C2A-0B7E-4E8A-9C4D-2A1B3C4D5E6F.png",
      "isVisible": true,
      "isGroup": false,
      "opacity": 1,
      "blendMode": "Normal",
      "transform": {
        "origin": [0, 0],
        "size": [4000, 6000],
        "rotation": 0,
        "flipX": false,
        "flipY": false,
        "sampling": "High quality"
      }
    }
  ]
}
```

- `layers` **自下而上**：第一个画在最底下，最后一个画在最上面。
- 编辑已有工程时保留 `documentID`，并保留不认识的字段（`guides`、`text`、`effects` 等）。
- `resolution`（ppi，1–9600）和 `activeLayerID` 可省略。

## 必须遵守的规则

| 项目 | 规则 | 违反时 |
|---|---|---|
| `transform` | `origin` 是 `[x, y]`，`size` 是 `[w, h]`（**数组，不是对象**）；`rotation`、`flipX`、`flipY`、`sampling` 必须都写上（Swift 的 Codable 不会用默认值补缺） | 解码失败，整个工程被拒 |
| `sampling` | `"High quality"`、`"Smooth"` 或 `"Nearest"` | 被拒 |
| `id` | 大写 UUID，工程内唯一 | 被拒 |
| `imageFile` | 像素层必须写，且等于 `"<ID>.png"`。不写就是空白图层 | 名字不对会被拒；漏写则图层是空的 |
| 蒙版 | `maskFile` 必须等于 `"<ID>.mask.png"`；`maskEnabled` 只能和 `maskFile` 一起出现 | 被拒 |
| PNG | 位深不超过 8 位（版本 12 起图层图片可以是 16 位）；蒙版为 8 位灰度、无 alpha。允许 1×1 的纯色蒙版（拉伸到整层，用户开始涂抹时才分配全尺寸像素） | 被拒 |
| `.exr` | 只有版本 13 起的图层图片可以用，且文件必须真是 OpenEXR | 被拒 |
| `blendMode` | 严格拼写：`Normal`、`Darken`、`Multiply`、`Color Burn`、`Linear Burn`、`Lighten`、`Screen`、`Color Dodge`、`Linear Dodge (Add)`、`Overlay`、`Soft Light`、`Hard Light`、`Vivid Light`、`Linear Light`、`Pin Light`、`Hard Mix`、`Difference`、`Exclusion`、`Subtract`、`Divide`、`Hue`、`Saturation`、`Color`、`Luminosity` | 被拒 |
| `opacity` | 0–1 | 被拒 |
| 尺寸上限 | 每边 30,000 px；最多 10,000 层；manifest 不超过 4 MiB；单个 PNG 不超过 512 MiB | 被拒 |
| 内存预算 | 所有图层像素合计不超过 min(800 MP, max(200 MP, 内存 ÷ 16))；蒙版另算一份同样的预算 | 被拒 |

## 文件夹

文件夹的记录写 `"isGroup": true`，不写 `imageFile`，混合模式只能是 `Normal`。子图层用 `"parentID": "<文件夹 ID>"` 指向它；同一父级下的图层按数组顺序自下而上排列（只有同一文件夹里的先后有意义，文件夹的内容不必在数组里连续）。最多嵌套 64 层。文件夹是穿透的：里面的图层直接画在下面的内容上，不先合成为一个整体，所以里面的调整层作用于它下面的**全部**内容，分组本身不改变画面。文件夹的不透明度乘到里面每一层上。隐藏文件夹会连带隐藏子图层，子图层自己的 `isVisible` 不变。折叠状态不存进工程：打开工程时文件夹都是展开的；外部写入触发重新载入时，用户折叠过的文件夹保持折叠，新文件夹是展开的。

文件夹也可以带蒙版（`maskFile` + `maskEnabled`，同样命名为 `<文件夹 ID>.mask.png`）：蒙版铺满文件夹自己的 transform 区域（新建时即画布大小），并乘到里面每一层的覆盖范围上。几层要共用一个蒙版时（例如双色辉光的内圈 + 外圈），把它们放进一个带蒙版的文件夹，用户只需改一个蒙版。

## 调整层

调整层写 `adjustment` 对象，不写 `imageFile`，作用于它下面的全部内容，可以有蒙版、不透明度和混合模式。每种调整都要带完整的单位 `levels` 和 `curves` 块。

```json
"adjustment": {
  "kind": "Curves",
  "hue": 0, "saturation": 0, "lightness": 0, "colorize": false,
  "levels": { "channel": "RGB", "ranges": [
    { "black": 0, "gamma": 1, "white": 255, "outputBlack": 0, "outputWhite": 255 },
    { "black": 0, "gamma": 1, "white": 255, "outputBlack": 0, "outputWhite": 255 },
    { "black": 0, "gamma": 1, "white": 255, "outputBlack": 0, "outputWhite": 255 },
    { "black": 0, "gamma": 1, "white": 255, "outputBlack": 0, "outputWhite": 255 } ] },
  "curves": { "channel": "RGB", "channels": [
    [ { "x": 0, "y": 0 }, { "x": 128, "y": 160 }, { "x": 255, "y": 255 } ],
    [ { "x": 0, "y": 0 }, { "x": 255, "y": 255 } ],
    [ { "x": 0, "y": 0 }, { "x": 255, "y": 255 } ],
    [ { "x": 0, "y": 0 }, { "x": 255, "y": 255 } ] ] }
}
```

- `kind` 可取：`Hue/Saturation`、`Levels`、`Curves`、`Exposure`、`Gradient Map`、`Grain`、`Invert`、`Black & White`、`Color Balance`、`Gaussian Blur`、`Motion Blur`、`Add Noise`（这三种要版本 9 起）；版本 16 起还有 `Progressive Blur`、`Bloom / Glow`、`RGB Split`，18 起 `Vignette`、`Lens Effects`、`Tonal Contrast`，19 起 `Sharpen`，它们的参数在 `filterLayerSettings` 对象里（`vignette`、`tonal`、`lens` 块要 18 起，`sharpen` 块 19 起）。本 skill 不写这些，只在校验时放行。
- `ranges` 和 `channels` 的顺序都是 RGB、红、绿、蓝。曲线有 2–32 个点，x 从 0 到 255 严格递增，取值 0–255；点之间用保形三次插值，不会过冲。
- 在上面的基础结构里再加对应字段：

```json
"blackWhiteSettings": { "reds": 40, "yellows": 60, "greens": 40, "cyans": 60, "blues": 20, "magentas": 80,
                        "tint": false, "tintHue": 40, "tintSaturation": 20 }

"grainSettings": { "amount": 10, "size": 2, "roughness": 50, "seed": 12345 }
```

Grain 的 `amount` 取 0–100，`size` 取 0.5–20 文档像素，`roughness` 取 0–100，`seed` 是 UInt32。

```json
"colorBalanceSettings": { "shadowCyanRed": 0, "shadowMagentaGreen": 0, "shadowYellowBlue": 0,
                          "midCyanRed": 4, "midMagentaGreen": 4, "midYellowBlue": -10,
                          "highlightCyanRed": 0, "highlightMagentaGreen": 0, "highlightYellowBlue": 0,
                          "preserveLuminosity": true }
```

色彩平衡的 9 个值都要写，各取 −100–100：正值分别偏向红、绿、蓝，负值偏向青、洋红、黄。

按色彩范围调整的色相/饱和度：Swift 把以枚举为键的字典编码成**扁平数组** `[键, 值, 键, 值, ...]`，`bands` 要把 7 个范围都列出来：

```json
"hsvSettings": {
  "range": "Yellows", "colorize": false, "invertRange": false,
  "adjustments": [ "Yellows", { "hue": 0, "saturation": -60, "lightness": 12 } ],
  "bands": [
    "Master",   { "falloffStart": 0,   "rangeStart": 0,   "rangeEnd": 360, "falloffEnd": 360 },
    "Reds",     { "falloffStart": 315, "rangeStart": 345, "rangeEnd": 15,  "falloffEnd": 45 },
    "Yellows",  { "falloffStart": 15,  "rangeStart": 45,  "rangeEnd": 75,  "falloffEnd": 105 },
    "Greens",   { "falloffStart": 75,  "rangeStart": 105, "rangeEnd": 135, "falloffEnd": 165 },
    "Cyans",    { "falloffStart": 135, "rangeStart": 165, "rangeEnd": 195, "falloffEnd": 225 },
    "Blues",    { "falloffStart": 195, "rangeStart": 225, "rangeEnd": 255, "falloffEnd": 285 },
    "Magentas", { "falloffStart": 255, "rangeStart": 285, "rangeEnd": 315, "falloffEnd": 345 } ]
}
```

其他种类的精确结构，可以在 Compositor 里加一个、保存，再从 manifest 里抄。

## 图层效果（effects）

像素层可以带 `effects`，里面的 `stroke`、`shadow`、`colorOverlay`、`innerShadow`、`outerGlow`、`innerGlow` 各自可选。辉光用的是颜色叠加：

```json
"effects": { "colorOverlay": { "enabled": true, "red": 1, "green": 0.8157, "blue": 0.5412, "opacity": 1 } }
```

- 颜色是 sRGB 0–1，`opacity` 0–1。`enabled` 可省略（等于开）；`false` 时效果保留在图层下、不显示。
- 除 `enabled` 外**每个字段都必须写**（Swift 的 Codable 不会用默认值补缺），缺一个整个工程被拒。数值超出范围不会被拒，但这个效果不会显示。各字段：描边 `size`（0–500）、颜色、`opacity`、`inside`；投影和内阴影 `angle`（±360）、`distance`（0–5000）、`blur`（0–500）、颜色、`opacity`；外发光和内发光 `size`（0–500）、颜色、`opacity`。
- 绘制顺序：先把图层蒙版作用到像素上，再画效果（颜色叠加用 source-atop：颜色按 `opacity` 盖过像素自己的颜色，透明度不变），最后按图层的不透明度和混合模式与下方合成。所以滤色图层加了颜色叠加，叠上去的仍是滤色。
- 调整层没有像素，Compositor 不给它画效果。
- 画布预览时，带效果的图层按最长边不超过 1536 像素渲染；导出是全分辨率。

## 安全写入（工程开着时也安全）

Compositor 给打开的工程算一个指纹：`manifest.json` 的字节，加上 `images/` 里每个文件的名字和大小（`ProjectDigest.swift`）。指纹一变，它就**整个工程重新加载**：每一层 PNG 都重新解码（16 位层还要再做一份抖动副本），大工程要好几秒；加载期间又有变化的话，它按 0.25 s、0.5 s、1 s……最长 32 s 的间隔退避重试（`ProjectController+ExternalChanges.swift`）。所以一次运行里陆陆续续往 `images/` 放文件，会让它反复重载、越等越久。正确做法：

1. 新的和改过的 PNG 先写到 `images/` **之外**（本 skill 用包内的隐藏文件夹 `.retouch/`；包根目录的变化不进指纹）。
2. 全部算完、manifest 按加载规则校验通过后，再一口气把文件 rename 进 `images/`，接着把 manifest 写到包内临时文件（如 `.manifest.json.tmp`）、rename 覆盖 `manifest.json`。几毫秒内完成，Compositor 只看到一次变化，重载一次。rename 是原子的，它不会读到写了一半的文件。
3. 删除 `QuickLook/`（放在这一串 rename 之前，免得把合并窗口拖长）；manifest 不再引用的 PNG 也删掉。

改已有图层时保留它的 `id`，覆盖它的 PNG 后再重写 manifest。如果新 PNG 恰好与旧的一样大，就顺便改一下 manifest（比如图层名），否则指纹不变、Compositor 不重载。

## Compositor 打开时的行为

- 最后一次变化后约 0.3 秒重新加载，短时间内的多次写入合并为一次更新；但见上节，每次重载都是全量解码。
- 重新加载会保留缩放、滚动位置和选区，但会清空撤销历史。
- 如果用户自己有未保存的改动，Compositor 会让用户选择用你的版本还是保留自己的，不会悄悄覆盖；该标签页不在前面时，等它回到前面再问。
- 加载失败的写入会被忽略，直到下一次改动；修正后会正常显示。
- 它自己的保存是整包原子替换（写一个同级临时包再换过去）：运行中若被保存，包里等待的文件会随旧包一起消失，脚本在提交前核对 manifest 的时间戳和大小，发现变了就放弃、报错重跑。

## 版本 14–20（Compositor 1.5.x，2026-10）

都是增量，本 skill 写的图层形状不变：14 形状层圆角平滑、15 线条样式、16 `Progressive Blur`/`Bloom / Glow`/`RGB Split` 调整层和 `filterLayerSettings`、17 GIF 层（`gif` 对象）、18 `Vignette`/`Lens Effects`/`Tonal Contrast`、19 `Sharpen`、20 `lens.prismFringe`。读取版本 20 的工程时保持它的版本号；新加的层按版本 9 以来通用的形状写。
