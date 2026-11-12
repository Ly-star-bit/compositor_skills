---
name: photocraft-liquify
description: >-
  Reshapes people in a photo with PhotoCraft's Liquify filter and matches the photo's colour tone to a reference
  picture, headless through photocraft-cli's MCP server (no window ever opens). Body parts are traced one by one
  (jaw, cheek, eyes, neck, shoulder, upper arm, elbow, forearm, waist, hip, thigh, knee, calf) and moved by tested
  recipes, then tone layers are measured from the reference; the result is a .jpg plus a layered .pcraft with the
  Liquify as a smart filter and the tone as adjustment layers. Use this whenever the user wants a person reshaped or
  slimmed, or a photo made to "look like" a reference picture in figure or colour, even when PhotoCraft is not named:
  液化, 瘦脸, V脸, 小脸, 大眼, 瘦手臂, 瘦胳膊, 拜拜肉, 收腰, 瘦腰, 瘦胯, 瘦腿, 瘦身, 修身材, 修形体, 修成这种风格,
  照着参考图修, 跟参考图调色, 色调对齐, 去黄, 冷白皮色调. For skin smoothing, dodge & burn or a layered retouching
  setup use photocraft-retouch or compositor-retouch instead.
---

# PhotoCraft 液化修形 + 参考图调色

把照片里的人修瘦、修脸型，再把色调调到和参考图一致。液化用 PhotoCraft 自己的「液化」滤镜，全程通过 `photocraft-cli mcp`（PhotoCraft 的 MCP 服务，没有窗口）完成。你要做的只有两件事：**看图，把要修的部位的边缘描出来**；**看检查图，判断修得对不对**。推多少、用多大的画笔、怎么换算成液化笔触，全部由脚本按验收过的配方决定。

动手之前先读一遍 [references/body-parts.md](references/body-parts.md)：每个身体部位怎么描、量哪里、不能碰什么都在里面。不读它直接写计划，几乎一定会把手肘推尖、把下巴推成锥子。

## 规则

- **不要打开任何 GUI**：不用 `open`、`osascript`，不启动 PhotoCraft，不弹访达，不开浏览器。要看图就用 Read 工具读脚本写出的图。
- **原图永远不动**：脚本只写新文件 `<原图名>_液化.jpg`（成品）、`<原图名>_液化.pcraft`（分层文档）、`<原图名>_液化.plan.json`（这次的计划，返工时接着用）和 `<原图名>_液化.checks/`（检查图）。
- **每一步都看图再往下走**：计划图没确认点位之前不要液化；脚本用 `[READ]` 列出的每一张检查图都读过之前，不要告诉用户"好了"。
- **宁轻勿重**：配方的标准强度就是客户验收过的力度。用户没说"再瘦一点"，不要自己加强度。
- **参考图只用来对色调**。参考图里人的姿势、胖瘦、比例不用去模仿：液化修多少由配方决定，不是照着参考图的轮廓去凑。
- **读脚本输出再汇报**：出现 `[SUCCESS]` 才算成功；`[Error]` 时什么都没写，把原因告诉用户；每一条 `[WARN]` 都要处理（见"脚本的提示"），处理不了的如实告诉用户。
- 被头发、衣服、别的肢体挡住看不见的边，不要猜着描。看不见就不修那一段。

## 准备（每台电脑第一次）

`${CLAUDE_SKILL_DIR}` 是本 SKILL.md 所在的文件夹；在不展开这个变量的环境里，换成加载技能时给出的技能目录。

```bash
PY="$(bash "${CLAUDE_SKILL_DIR}/scripts/python.sh")"      # 一个带 numpy 和 OpenCV 的 Python；没有就自动建一个（只建一次）
L="${CLAUDE_SKILL_DIR}/scripts/pcraft_liquify.py"
```

- `python.sh` 打印一个可用的 Python 路径：电脑上的 `python3` 已经有 numpy 和 OpenCV 就用它，否则在缓存文件夹里建一个私有环境（约 150 MB，需要联网一次）。Windows 上没有 bash 时手动装：`python -m pip install "opencv-python-headless<5" numpy`，然后 `PY=python`。
- PhotoCraft 命令行工具 `photocraft-cli`：按 `PHOTOCRAFT_CLI` 环境变量、`PATH`、缓存的顺序找；macOS 上都没有时第一次运行会自动下载 0.19.0（约 107 MB，按 SHA-256 和 Apple 公证校验）。别的系统要先自己装好并放进 `PATH`，脚本会说明去哪里下载。
- 每次新开终端都要重新设 `PY` 和 `L` 这两个变量。

## 流程

按顺序做，不要跳步。工作文件（网格图、计划、色块文件）放在一个工作文件夹里：用户指定了就用他的，否则在照片旁边建一个 `work/` 或用系统临时目录；不要放进技能文件夹。下面例子里的 `/tmp/work` 都换成这个文件夹。

### 1. 看全图，再看局部

```bash
"$PY" "$L" grid "/path/photo.jpg" --out /tmp/work/all.jpg                              # 全图
"$PY" "$L" grid "/path/photo.jpg" --box 1200 2100 2300 3100 --out /tmp/work/face.jpg   # 局部放大：--box 左 上 右 下
"$PY" "$L" grid "/path/photo.jpg" --box 900 3600 2700 5000 --brighten --out /tmp/work/waist.jpg   # 暗处看不清边时加 --brighten
```

用 Read 工具读网格图。先读全图，找出脸、每条手臂、躯干、每条腿大概在哪；再给**每个要修的部位**各出一张放大的网格图（`--box` 把那个部位框住，四周多留一点），从放大图上读坐标。

读坐标的方法：竖线上标着 `x1200`，横线上标着 `y3400`，就是原图的像素坐标。点落在两条线之间时按比例估算，估到 20–30 像素以内就够了（画笔很大，这点误差没有影响）；更要紧的是**边的走向别读反**（一条往右下斜的边不要读成往左下斜），这在第 4 步的计划图上一眼能看出来。**不要从全图上读部位的坐标**，全图上一格几百像素，读不准。照片暗、黑衣服贴着暗背景时加 `--brighten`（只是把这张网格图显示得亮一些，不影响成品）。

拿不准一条边到底在哪（暗处、斜边、边缘发虚）时，用 `probe` 量：它沿一行或一列找亮度突变的位置。

```bash
"$PY" "$L" probe "/path/photo.jpg" --row 5100 --from 1300 --to 1900    # 在 y=5100 这一行、x 从 1300 到 1900 之间找边
"$PY" "$L" probe "/path/photo.jpg" --col 2000 --from 3600 --to 4200    # 在 x=2000 这一列上找边
```

输出形如 `x=1637: 12 -> 78 (dark to light)`：在 x=1637 处从暗（黑丝袜）变亮（白床单），那就是腿的外缘。一行可能列出几处突变，选两侧明暗和你在图上看到的相符的那一处。

### 2. 决定修哪些部位

先看用户说了什么（"瘦脸"只修脸，"瘦手臂"只修手臂）。用户只说"液化 / 修成这种风格 / 修身材"时，修画面里**看得见的**这些：下颌和脸颊、眼睛、上臂（连肩）、手肘、小臂、腰两侧、胯和大腿外侧。每个部位在 body-parts.md 里都有一节，按它描。

用户的措辞决定整体力度（第 5 步的 `--strength`）：没特别说 → 1（不写）；"稍微 / 一点点 / 自然一点" → 0.7–0.8；"明显一点 / 再瘦一点" → 1.2–1.3。

同时找出画面上**压上去的字**（标题、字幕、水印、logo）。它们要写进计划的 `captions`，脚本会检查字有没有被带歪。

### 3. 写计划 plan.json

从 [assets/plan-template.json](assets/plan-template.json) 抄格式，一个部位一条。完整的真实例子见 [references/example-plan.json](references/example-plan.json)。

```json
{
  "parts": [
    {"part": "jaw", "name": "jaw right", "width": 520, "toward": [1800, 2650],
     "edge": [[2026, 2548], [2028, 2610], [2023, 2658], [2018, 2692], [1999, 2740], [1970, 2778], [1932, 2812]]},
    {"part": "eye", "name": "eye left", "at": [1617, 2463], "width": 106},
    {"part": "upper_arm", "name": "upper arm left", "width": 345, "toward": [1650, 3450],
     "edge": [[1200, 3062], [1180, 3126], [1176, 3190], [1182, 3270], [1200, 3350], [1224, 3422], [1264, 3502]]}
  ],
  "captions": [{"name": "title", "box": [1400, 180, 2600, 460]}]
}
```

每条部位的字段：

| 字段 | 含义 |
|---|---|
| `part` | 部位类型，决定用哪个配方：`jaw` `cheek` `neck` `shoulder` `upper_arm` `elbow` `forearm` `wrist` `waist` `hip` `belly` `back` `thigh` `knee` `calf` `ankle`（描边）；`eye` `nose`（只给一个点） |
| `name` | 给自己看的名字，**用英文**（检查图上只能显示英文），同类部位要能区分，如 `jaw left`、`jaw right` |
| `edge` | 沿着这个部位**看得见的轮廓线**依次点出的点 `[[x, y], ...]`，至少 3 个，一般 5–15 个，相邻两点相距 50–120 像素。**顺序有规定**（比如下颌从颧骨到下巴前、上臂从肩到肘），见 body-parts.md |
| `toward` | 这个部位**内部**的一个点 `[x, y]`（肢体的中轴上、脸的中心）。边会朝它的方向收。放错到身体外面，部位会被推胖 |
| `width` | 这个部位自己的宽度，像素。量哪里每个部位不同（脸宽、手臂粗细、腰宽…），见 body-parts.md。也可以写成两个点 `[[x1, y1], [x2, y2]]`，脚本算距离 |
| `strength` | 可选，默认 1。只想这个部位轻一点写 0.7，用户要求更明显才写 1.2–1.3，上限 1.8 |
| `direction` | 可选。强制这条边所有点朝同一个方向推，如 `[1, 0]`（向右）、`[-1, 0]`（向左）。**画笔够得到字的边必须用水平方向**，否则字会歪。代价是斜着的边被水平推时，实际收进去的量会打折（45° 的边约七成），这是保字的代价，不用补偿 |
| `ease_end` | 可选，`true`。`edge` 的**最后一个点**不是这个部位自然的终点，而是你被迫提前停下的地方（再往前被头发盖住了、有字、有手或别的东西）时加上：推力在最后一段平滑减到很小，轮廓上不留台阶 |
| `ease_start` | 可选，`true`。同上，针对 `edge` 的**第一个点**（比如肩被头发盖住，上臂的边只能从头发边缘开始描） |
| `brush` | 可选，画笔直径（像素）。边旁边 150 像素内有不能变形的硬东西（袖口、手、道具、另一条肢体）时，写成"到那个东西的距离 × 2"。画笔比"推动距离 × 5"还小时脚本会警告 |

`eye`、`nose` 不描边，只给 `at`（中心点）和 `width`（眼睛从内眼角到外眼角的宽度；鼻翼的宽度）。

`captions` 里每条是 `{"name": 英文名, "box": [左, 上, 右, 下]}`，把一整行字框住。

高级写法 `contours`、`nudges`、`dabs`（自己给像素数和方向）只在配方覆盖不了时用，写法见 [references/engine.md](references/engine.md)。

### 4. 先画计划图，确认点位（不液化）

```bash
"$PY" "$L" apply "/path/photo.jpg" --plan /tmp/work/plan.json --plan-only
```

它在 `<原图名>_液化.checks/` 里写出放大的计划图 `01-<部位名>.plan.jpg`、`02-….plan.jpg`……（挨得近的部位合在一张里，所以图的张数比部位少），并用 `[READ]` 逐行列出路径。**列出的每一张都用 Read 读**，逐条核对：

1. **红点都在轮廓线上**。偏到皮肤里面或背景里超过约 20 像素的点，回网格图重读坐标。
2. **绿箭头全部指向身体内部**（往里收）。有箭头朝外，说明 `toward` 放到了部位外面。
3. **黄色的 ×（`toward`）在这个部位里面**：脸的中间、肢体的中轴上、腰的正中。
4. **蓝圈（每条边的第 1 个点）在规定的那一端**，圈旁写着部位名。比如 `jaw` 的蓝圈应在颧骨那端，`upper_arm` 的在肩那端，`forearm` 的在手肘那端。
5. 青色连线顺着轮廓走，没有来回折返、没有从身体中间横穿过去。
6. 箭头长度就是真实的推动距离。哪条边的箭头明显比别处长很多、或者长过了肢体粗细的三分之一，检查 `width` 是不是量错了。
7. 眼睛、鼻子不单独出图，是画在脸那张图上的洋红色圆圈：圆心应在瞳孔（鼻翼正中）。

有问题就改 plan.json 再跑一次 `--plan-only`，直到七条都对。这一步不调用 PhotoCraft，很快。

### 5. 液化

```bash
"$PY" "$L" apply "/path/photo.jpg" --plan /tmp/work/plan.json --reference "/path/reference.jpg"
```

`--reference` 可选，只是把参考图放进总览图里并排比较。要整体轻一点 / 重一点加 `--strength 0.7` / `--strength 1.2`。脚本对每条边打印 `px want>got`（每个点想推多少、实测推了多少）；带 `*` 的点在另一个部位的画笔范围里，两股推力叠加，属于正常。眼睛、鼻子各打印一行（`bloat x2` 之类）。

### 6. 看检查图（必须，一张都不能少）

脚本最后用 `[READ]` 列出这次写的每一张检查图。**每一张都用 Read 读完**再下结论，只看总览图是看不出手肘尖了、轮廓有坑这类问题的：

- `overview.jpg`：修前 | 修后（| 参考图）。整体比例是否自然，有没有哪里突然细了一截。
- `01-<部位名>.jpg`、`02-….jpg`……：每个部位的修前（带计划）| 修后放大图。**每个关节、每段肢体都要在这里看**，对照下面的表。暗的局部会自动显示得亮一些（图上写着 shown lighter）。
- `field.jpg`：哪里动了、动了多少（越红动得越多）。不该动的地方（脸中间、手、袖口、字）应该是原图颜色。白圈是脚本警告"压缩 / 拉伸过头"的位置。
- `captions.jpg`：有字被带动时才有，修前修后的字逐行对比。

| 在修后图上看到 | 原因 | 怎么改 |
|---|---|---|
| 手肘、膝盖变尖，像个角 | 关节被当成肢体推了，或推得比两边多 | 关节单独用 `elbow` / `knee`；肢体的边在关节前就结束 |
| 下巴变尖，成了锥子 | 下颌的边描到了下巴，或两侧都推到了下巴 | `jaw` 的边在嘴角正下方之前结束；下巴本身不描 |
| 小臂、小腿靠近关节的一端比手腕、脚踝一端还细 | 靠关节的一端推多了 | 用 `forearm` / `calf` 配方并保证顺序是关节 → 手腕（脚踝） |
| 轮廓上有一个凹坑、台阶或硬折 | 边在中途突然开始或结束 | 把边描完整；只能从半路开始 / 停下时加 `ease_start` / `ease_end` |
| 肢体被推胖了 | `toward` 在部位外面 | 把 `toward` 改到部位内部 |
| 背景里的直线（门框、墙缝、地平线）弯了 | 画笔扫到了直线 | 给那条边写小一点的 `brush`，或 `strength` 降到 0.6，或不修那条边 |
| 袖口、手表、眼镜、道具变形 | 画笔扫到了硬东西 | 同上；边在硬东西之前结束并加 `ease_end` |
| 字歪了、挤了 | 够得到字的边斜着推，或紧挨着字收尾 | 那条边加 `"direction": [1, 0]` 或 `[-1, 0]`；并让边在离字一个画笔半径以外结束（加 `ease_end`） |
| 边缘旁边的纹理（墙纸、布纹、皮肤毛孔）被拖出一圈模糊 | 画笔相对推动距离太小 | 那条边不写 `brush`（用默认的），或降 `strength` |
| 整个人看起来像另一个人 / 太夸张 | 力度过头 | 全部减轻：`--strength 0.7` |

改完 plan.json 重新跑第 5 步（同一条命令，会覆盖上一次自己写的成品），再把 `[READ]` 列出的图全部读一遍，直到表里的问题都没有。

### 7. 调色（有参考图、用户要"这种风格 / 这种色调"时）

1. 在两张图上各找出**同一种东西**的小方块（色块），写成 pairs.json：
   - `pairs`（主体）：皮肤 2–3 处（脸颊、手臂、手），嘴唇，一处白色或灰色（白衬衫、袖口、白墙）。每一对的两个方块要**受光相近**：都在亮处，或都在阴影里。方块落在颜色均匀的地方，边长 60–200 像素（嘴唇可以小到 30），不要压到头发、五官、高光、阴影的边界。
   - `surroundings`（环境，可选，1–2 组）：照片里**颜色偏得最明显的一大块背景**（发黄的墙、窗帘、木头）配上参考图里的一大块背景。它们不需要是同一种东西，也不需要一样亮，只是告诉脚本"环境的颜色往哪个方向去"；脚本只让环境走 60% 的路（`--surroundings 0.6`，可改 0–1）。不要拿灰色、接近肤色的东西当环境。

```json
{"pairs": [
   {"name": "cheek",      "photo": [1880, 2540, 1960, 2620], "reference": [1960, 2040, 2050, 2140]},
   {"name": "arm",        "photo": [1300, 3350, 1500, 3500], "reference": [533, 2400, 800, 2560]},
   {"name": "lips",       "photo": [1780, 2690, 1880, 2740], "reference": [1880, 2215, 1960, 2250]},
   {"name": "white cuff", "photo": [1750, 3260, 1900, 3400], "reference": [880, 1840, 1000, 2000]}],
 "surroundings": [
   {"name": "curtain / wall", "photo": [300, 1500, 900, 2500], "reference": [300, 900, 800, 1400]}]}
```

2. 生成调色层并检查色块：

```bash
"$PY" "$L" tone "/path/photo.jpg" "/path/reference.jpg" --pairs /tmp/work/pairs.json --out /tmp/work/tone.json
```

用 Read 读它写出的 `tone.patches.jpg`：上排是照片的色块、下排是参考图的色块，**每一列上下必须是同一种东西**（都是脸颊、都是白袖口）。有一列框到了头发、阴影或别的东西，就改坐标重跑。

输出表里每行是"照片 → 调色后 | 参考图"的三个数：明暗 / 红绿 / 黄蓝。看两处：主体每一行的 `off target` 在 3 以内算对上了，3–5 可以接受（两处皮肤本来就不会完全一样，汇报时提一句），超过 5 就重选那一对色块；同一行里照片和参考图的**第一个数（明暗）相差不超过 12**，否则这一对色块一明一暗，换位置。改过 pairs.json 重跑之后，`tone.patches.jpg` 要重新读一遍。表后面有一行"neutral grey"：调色层会把中性灰带偏多少，数值在 ±8 以内是正常的风格偏移，脚本对超过 12 的会警告（多半是某一对色块框错了）。

3. 和液化一起出成品（计划和调色可以只给一个）：

```bash
"$PY" "$L" apply "/path/photo.jpg" --plan /tmp/work/plan.json --tone /tmp/work/tone.json --reference "/path/reference.jpg"
```

再读一次 `overview.jpg`：肤色自然、白色的东西没有明显偏色、整张图没有蒙上一层粉 / 绿 / 蓝、没有色带。调色过头时，告诉用户在 `.pcraft` 里把「色调」图层的不透明度调低，或者你把 tone.json 里每层加上 `"opacity": 0.7` 重跑。

调色只对齐**颜色**，不改明暗和光比：参考图是暗调、照片是亮调时，成品仍然是亮调，这一点要如实告诉用户。

同一组照片（同一场景、同样的灯光）可以直接复用上一张的 tone.json，不用重新量。[assets/tone-cool-pink.json](assets/tone-cool-pink.json) 是验收过的一套"暖光室内 → 冷粉去黄"，没有参考图时可以拿它起步。

### 8. 汇报

告诉用户：成品和分层文档的完整路径；修了哪些部位、各自大约动了多少像素（脚本输出里有）；**哪些地方没修以及原因**（被挡住、旁边有字、看不清边缘）；`.pcraft` 的大小（通常几十 MB）以及里面有什么（照片是智能对象，液化是它的智能滤镜，色调是上面的调整层，都可以在 PhotoCraft 里继续改）；还有没解决的 `[WARN]`。不要说"完美"，说你在检查图里实际看到了什么。

## 返工

- "再瘦一点 / 太夸张了"：整体用 `--strength 1.2` 或 `--strength 0.7`；只针对某个部位就改那条的 `"strength"`。每次改动幅度不要超过 0.3。
- 用户隔了很久再来改：上次的计划在 `<原图名>_液化.plan.json` 里，直接 `--plan` 指向它（或复制出来改）。
- 用户在 PhotoCraft 里改过 `.pcraft` 之后再跑，脚本会报错拒绝覆盖（避免丢掉他的修改）。问清楚后加 `--force`，或用 `--out` 另存一份。
- 只要 JPG：加 `--no-pcraft`。

## 脚本的提示

`[WARN]` 必须处理或如实上报；`[NOTE]` 是告知，读懂即可。

| 提示里说 | 意思 | 怎么办 |
|---|---|---|
| `[WARN] an edge would move N% of the part's width; held at …` | 这条边要推的距离超过了部位宽度的 30% | `width` 量错了（多半量小了），或边的首尾点放错了；重量重描 |
| `[WARN] edge points up to N px apart` | 相邻两点离得太远 | 中间补点 |
| `[WARN] a N px brush is small for a M px push` | 你写的 `brush` 相对推动距离太小，边两侧会被狠狠拉伸 / 压缩 | 边两侧都是纯色（黑衣服、白床单）可以不管；否则按提示改大 `brush` 或降这个部位的 `strength` |
| `[WARN] the picture is stretched N x at (x, y) … beside …`（或 `squeezed to N x`） | 实测某处被拉伸（超过 2.2 倍）或压缩（不到 0.4 倍）得太厉害，提示里写了坐标和是哪个部位造成的，`field.jpg` 上有白圈 | 读那个部位的放大图看白圈处：两侧是纯色就没事；有纹理、有字、有皮肤就按提示改 `brush` 或 `strength` |
| `[WARN] … reaches the text … with pushes that are not level` | 这条边的画笔够得到字，而且是斜着推 | 加 `"direction": [1, 0]` / `[-1, 0]`，或让边在离字一个画笔半径以外结束并加 `ease_end` |
| `[NOTE] …'s brush … reaches the text … Its pushes are level` | 画笔够得到字，但是水平推，字只会被轻微挤一下 | 一般不用管；想让字完全不动，就按提示的距离提前结束这条边 |
| `[WARN] The text will look bent` | 实测字被带歪了 | 读 `captions.jpg`；让那条边离字更远处结束（`ease_end`）、用小 `brush`、并保持水平推 |
| `[WARN] an edge point landed N px off its target` | 某个点没推到位（通常是两条边的画笔互相打架） | 看那个部位的放大图是否自然；不自然就把两条边分开一点或删掉重叠的点 |
| `[NOTE] N px of outline between the end of … and … is not traced` | 肢体的边和关节之间留了一段没描 | 那一段轮廓看得见（贴着衣服的边也算）就把肢体的边接着描到关节前；确实被挡住就不管 |
| `[WARN] still N from the reference`（调色） | 这个色块调完和参考图还差得多 | 读 patches 图，多半是这一对色块不是同一种东西 |
| `[WARN] the two patches differ in lightness`（调色） | 这一对主体色块一明一暗 | 换成受光相近的位置 |
| `[WARN] the layers tint neutral grey by N`（调色） | 调色层会给整张图蒙上一层颜色 | 某一对色块框错了，先查白 / 灰那一对 |
| `[NOTE] the surroundings and the subject share a hue`（调色） | 你选的环境色块和肤色是同一个色相，分不开，所以环境没有单独调 | 换一块颜色偏得更明显的背景（暖黄的墙、窗帘）；没有就接受，并告诉用户环境色没有单独处理 |

## 须知

- 一张 2100 万像素的照片，液化加出文档约 4–5 秒；`.pcraft` 约 80 MB。
- 液化是一次完成的（所有笔触在同一个滤镜里），画面只重采样一次，多次返工不会越修越糊：每次都是从原图重新做。
- 推多少是用"部位宽度的百分比"定的，所以同一份配方在任何分辨率下力度一致。各部位的百分比和依据见 body-parts.md 末尾的表。
- 修改脚本后运行 `tests/run.sh`。引擎的实测规律（为什么短笔触会被丢掉、为什么要对着实测位移场校正、调色曲线为什么是"画出来的"）见 [references/engine.md](references/engine.md)。
