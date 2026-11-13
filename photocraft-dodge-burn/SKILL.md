---
name: photocraft-dodge-burn
description: >-
  Paints a first pass of dodge & burn on a portrait in PhotoCraft, headless through photocraft-cli's MCP server (no
  window ever opens): PhotoCraft finds the face, makes a 50% gray Soft Light layer (neutral gray, 中性灰) or two Curves
  layers under black masks (dual curves, 双曲线), and its own soft brush paints on them. Twelve things, each judged per
  person (clear, some, none): skin patches evened on forehead, cheeks and chin; the shade under the eyes lifted; the
  dark line of each crease lightened (under the eye bags, smile lines, below the mouth's corners, forehead lines,
  between the brows, crow's feet); shine lowered; and, only when asked, a little modelling. The result is a .jpg plus
  a layered .pcraft with the photo untouched below and the pass on top, for the user to check, take back and paint
  on. Use this whenever the user wants the light and shade of a face retouched for them, even when PhotoCraft is not
  named: 修光影, 中性灰, 双曲线, dodge and burn, D&B, AI 先刷一遍, 帮我刷中性灰, 匀肤色, 肤色不匀, 明暗斑驳, 黑眼圈, 泪沟,
  眼袋, 法令纹, 嘴角纹, 木偶纹, 抬头纹, 川字纹, 眉间纹, 鱼尾纹, 淡化皱纹, 去油光, 压高光, 立体感, 轮廓光影. For reshaping
  (液化, 瘦脸) use photocraft-liquify; for skin smoothing or a whole layered retouching setup use photocraft-retouch
  or compositor-retouch.
---

# PhotoCraft 中性灰 / 双曲线：AI 先刷一遍光影

替用户在人像上刷第一遍 dodge & burn，留给用户自己检查、擦改、接着刷。全程通过 `photocraft-cli mcp`（PhotoCraft 的 MCP 服务，没有窗口）完成：PhotoCraft 自己找人脸、自己建图层、用它自己的软画笔一笔一笔画。你要做的是三件事：**看图，逐项判断修几成**；**看检查图，判断有没有画坏、画错地方**；**用审美再判断一遍：修完是不是真的更好看**。笔画在哪、多大、多重，由脚本量出来再定。

动手之前先读一遍 [references/regions.md](references/regions.md)：十二项各自是什么、在脸上哪里、量出来的数怎么读、三档怎么判断、画完看什么，都在里面。不读它就定档，最常见的结果是把眼妆当黑眼圈提亮、把表情纹抹掉。

只改明暗，只动脸上的皮肤。一共十二项，分五类：

| 类 | 项（`--needs` 里的名字） | 做什么 | 怎么画 |
|---|---|---|---|
| 匀：肤色不匀 | `forehead` 额头、`cheeks` 脸颊、`chin` 下巴（合称 `skin`） | 把比周围略亮、略暗的小块匀掉；毛孔纹理、立体感、痣和纹路不动 | 每个区域几十个软笔点，亮处点暗、暗处点亮 |
| 提：暗影 | `circles` 黑眼圈 | 把眼下的暗影整体提亮一些 | 每只眼下一笔 |
| 淡：纹路 | `troughs` 泪沟、`folds` 法令纹、`marionette` 嘴角纹、`forehead_lines` 抬头纹、`frown` 眉间纹、`crows` 鱼尾纹（后四项合称 `lines`） | 把纹路那条暗线提亮一些：纹还在，只是浅了 | 沿着找到的每一条线一笔细软笔 |
| 压：高光 | `shine` 油光 | 把最亮的反光点压暗一点 | 几个到几十个软笔点 |
| 塑：立体感 | `shape` | 鼻梁、额头、苹果肌、下巴加一点亮，鼻侧、颧骨下加一点暗 | 十来笔大软笔。**用户要求才做** |

## 规则

- **不要打开任何 GUI**：不用 `open`、`osascript`，不启动 PhotoCraft，不弹访达，不开浏览器。要看图就用 Read 工具读脚本写出的图。
- **原图永远不动**：脚本只写新文件 `<原图名>_光影.jpg`（成品）、`<原图名>_光影.pcraft`（分层文档）、`<原图名>_光影.light.json`（这次的记录）和 `<原图名>_光影.checks/`（检查图）。
- **因人而异，先看再画**：每个人不一样。每一项各自分三档：`clear` 全量、`some` 一半、`none` 不修。量到的数只是参考，要看过图再定。"这张不用刷"是合格的结论。
- **宁轻勿重**：`auto` 最多取一半，从不取全量；全量要你看过图、确认明显之后自己写。在两档之间拿不准，选轻的那一档。这只是第一遍，用户还要自己接着修。
- **不是瑕疵的不修**：眼妆、表情（笑出来的纹）、造型光打出来的影子、妆面的高光、这个人长相的一部分。详见 regions.md。
- **每一步都看图再往下走**：脚本用 `[READ]` 列出的每一张图都读过、并且做完第 5 步的审美判断之前，不要告诉用户"好了"。
- **读脚本输出再汇报**：出现 `[SUCCESS]` 才算成功；`[Error]` 时什么都没写，把原因告诉用户；每一条 `[WARN]` 都要处理或如实上报。`[OK]` 开头的是量到的数和进度，汇报时要用到里面的数；`[READ]` 后面是要读的检查图。

## 准备（每台电脑第一次）

`${CLAUDE_SKILL_DIR}` 是本 SKILL.md 所在的文件夹；在不展开这个变量的环境里，换成加载技能时给出的技能目录。

```bash
PY="$(bash "${CLAUDE_SKILL_DIR}/scripts/python.sh")"      # 一个带 numpy 和 OpenCV 的 Python；没有就自动建一个（只建一次）
D="${CLAUDE_SKILL_DIR}/scripts/pcraft_dodge_burn.py"
```

- `python.sh` 打印一个可用的 Python 路径：电脑上的 `python3` 已经有 numpy 和 OpenCV 就用它，否则在缓存文件夹里建一个私有环境（和 photocraft-liquify 共用，约 150 MB，需要联网一次）。Windows 上没有 bash 时手动装：`python -m pip install "opencv-python-headless<5" numpy`，然后 `PY=python`。
- 脚本的输出一律是 UTF-8，不随系统编码走（中文 Windows 的 GBK 也一样）。
- PhotoCraft 命令行工具 `photocraft-cli`：按 `PHOTOCRAFT_CLI` 环境变量、`PATH`、缓存的顺序找；macOS 上都没有时第一次运行会自动下载 0.19.0（约 107 MB，按 SHA-256 和 Apple 公证校验）。别的系统要先自己装好并放进 `PATH`，脚本会说明去哪里下载。
- 每次新开终端都要重新设 `PY` 和 `D` 这两个变量。

## 流程

按顺序做，不要跳步。

### 1. 先看，不画

```bash
"$PY" "$D" look "/path/photo.jpg"
```

照片也可以是分层文档（`.pcraft`、`.psd`）：这时量的是它当前可见的合成画面；先让用户在 PhotoCraft 里保存，脚本读的是磁盘上的文件。

它打印一张表，每项一行：

```
     cheeks           5.5   auto: some   patches differ, cheeks; plain to see: clear is worth considering
     circles         12.3   auto: some   darker under the eyes than the skin round them (picture's left 12.8, right 11.8)
     folds           19.2   auto: some   the smile lines stand out (picture's left 22.5, right 15.9)
     frown            0.0   auto: none   the lines between the brows stand out; no line found
```

依次是：项的名字、量到的数（0–255 级）、`auto` 会选的档、这个数量的是什么。有左右两侧的项会分别给出（"左右"指画面的左右）。`no line found` 是这一项没找到纹路；`plain to see: clear is worth considering` 是这个数已经算明显，可以考虑全量。

它还写出四张图（几张脸就各带编号），**都用 Read 读**：

- `look.jpg`：整张脸。左边是黑白检查视图（把这张脸的肤色放到中灰、反差加倍，明暗斑驳看这里）；右边是区域：绿 = 肤色不匀的范围，青 = 眼下，橙 = 法令纹的范围，黄 = 油光，**洋红色的线 = 找到的每一条纹路**。
- `look_eyes.jpg`、`look_mouth.jpg`、`look_forehead.jpg`：眼睛、嘴周围、额头的放大图，同样是检查视图｜区域。

先核对落位：青色在两只眼睛正下方；橙色从鼻翼旁到嘴角外；绿色在额头、脸颊、下巴的皮肤上，没有盖到眉毛、眼睛、嘴唇、头发；每一条洋红线都落在一条真的纹路上，而不是发丝、睫毛、眉毛、鼻孔的边。哪一项落错了，那一项用 `none`（或按"返工"自己给五个点）。

### 2. 逐项定档

对照 regions.md 里每一项的"三档怎么判断"，一项一项过。多数项接受 `auto` 就行；下面这些必须由你来改：

- **眼下的暗是眼妆**（烟熏、画出来的卧蚕）或造型光的影子 → `circles=none`，`troughs=none`。（量到 22 级以上时 `auto` 已经当成眼妆不修；低于 22 但你看得出是妆，也要改。）
- **人在笑** → `folds`、`crows`、`marionette` 最多 `some`：那是表情。用户点名"淡化法令纹 / 去皱纹"才用 `clear`。
- **皮肤已经精修过、很干净** → `skin=none`。
- **洋红线落在不是纹路的东西上**（发丝、睫毛的影子、眉毛边）→ 那一项 `none`。
- **高光是妆**（鼻梁、颧骨上打的高光）或用户喜欢的光泽 → `shine=none`。`shine` 只在油光明显、或用户点名"去油光 / 压高光"时用。
- **`shape` 只在用户要求时用**（"加强立体感 / 轮廓 / 骨相"）：`some` 起步。它是按五个点估的位置，没有经过验收。
- 用户只点名一项（"帮我去一下黑眼圈"）时，其余用 `all=none` 关掉。
- 用户对某一项说过话，以用户为准。在两档之间拿不准，选轻的那一档。

### 3. 画

```bash
"$PY" "$D" apply "/path/photo.jpg" --needs skin=some,circles=clear,folds=some,lines=none
"$PY" "$D" apply "/path/photo.jpg" --needs all=none,circles=clear,troughs=some           # 只修眼下
"$PY" "$D" apply "/path/photo.jpg" --needs skin=some,circles=some --on curves            # 画在双曲线的蒙版上
```

`--needs` 的写法：`项=档`，逗号分开，后写的盖过先写的，没写的项按 `auto`。

- 档：`clear` 全量、`some` 一半、`none` 不修、`auto`。
- 一次设几项：`skin`（= `forehead`、`cheeks`、`chin`）、`lines`（= `forehead_lines`、`frown`、`crows`、`marionette`）、`all`（除 `shape` 以外的全部）。
- 先 `all=none` 再写要修的项，就是"只修这几项"。

图层：

- 默认画在一层 **50% 灰、柔光**的图层上（中性灰）：白笔提亮、黑笔压暗。
- `--on curves` 画在**双曲线**上：压暗、提亮两个曲线调整层（明度混合），蒙版全黑，白笔刷哪里哪里生效。用户说"用双曲线"时用它；它不带偏色（中性灰柔光提亮会让颜色略微发灰，手刷中性灰也一样）。

所有项都是 `none`、或者量到的太少不值一笔时，脚本不写任何文件，以 `[SUCCESS] nothing to paint` 结束：这是合格的结果，告诉用户量到了多少、为什么不用刷。

脚本打印：同一张表，多出"画完再量"的数（`folds 19.2 -> 11.5`，量的是画之前的同一块皮肤、同一条线）；每一项画了几笔；纹路和黑眼圈每一笔"想提多少、实际提了多少"；笔点合起来画出了想要的变化的百分之几；整张脸最多提了、压了多少级。

### 4. 看检查图：有没有画坏（必须，列出的每一张都读）

- `light.jpg`：整张脸，修前｜修后。
- `light_bw.jpg`：黑白检查视图里的修前｜修后（两边用同一个中灰）。斑驳有没有变匀，看这张。
- `light_map.jpg`：左边是实际画上去的变化，放大 4 倍，亮 = 提亮、暗 = 压暗；右边是区域和每一笔的位置（白 = 提亮的笔，黑 = 压暗的笔，圆圈是笔点）。
- `light_eyes.jpg`、`light_mouth.jpg`、`light_forehead.jpg`：画过的部位各一张放大图，上排修前｜修后，下排同一处的检查视图。**眼下、纹路这些细的地方只有在放大图里才看得清。**

| 在检查图上看到 | 原因 | 怎么改 |
|---|---|---|
| 眼下被提成一块发白、发灰的"补丁" | 黑眼圈提多了，或那里是眼妆；`circles` 和 `troughs` 两笔叠在一起 | `circles` 降一档；是妆就两项都 `none` |
| 某条纹没了，脸像打了针 | 那一项提多了 | 那一项降一档 |
| 白色的笔画在了脸颊、嘴唇、发丝、睫毛上，不在纹上 | 找到的"线"不是纹路 | 那一项 `none`，告诉用户这一项留给他手刷 |
| 提亮、压暗落到了眉毛、嘴唇、头发、鼻孔上 | 区域错位，或皮肤范围把头发算进去了 | 那一项 `none`；或自己给五个点（见"返工"） |
| 脸变平了：颧骨、鼻梁、下颌的明暗弱了 | 肤色不匀匀多了，或油光压多了 | `skin` 降一档；`shine` 降一档或 `none` |
| 脸上出现一个个圆形的亮点、暗点 | 笔点太重 | 那个区域（`forehead` / `cheeks` / `chin`）降一档 |
| 鼻梁、额头中间多出一条不自然的亮带，或脸颊下多出一道暗 | `shape` 的位置不合这张脸 | `shape=none` |
| 几乎看不出变化 | 这张脸本来就干净，或档位是一半 | 不是毛病：这只是第一遍。不要因为"看不出"自己加档 |

改了档位就重新跑第 3 步的命令（每次都从原图重新画，会覆盖上一次自己写的成品），再把列出的图读一遍。

### 5. 审美判断：修完真的更好看吗（必须）

第 4 步查的是"有没有画坏"，这一步查的是"好不好看"。再读一遍 `light.jpg`、`light_bw.jpg` 和放大图，把自己当成第一次看到这张照片的人，逐条回答，每条都要能说出理由：

1. **还像本人吗？** 黑眼圈、法令纹、鱼尾纹是这个人长相和年纪的一部分：淡了可以，没了就不像了。
2. **看得出修过吗？** 只看修后那一半：眼下、嘴角旁、额头上有没有一眼就看得出的亮块、亮线。
3. **立体感还在吗？** 好的光影是把乱的匀掉、把形留下。脸比修前平了，就是修坏了。
4. **表情还在吗？** 笑的时候眼角和嘴边该有纹。纹全淡了，笑就假了。
5. **每一项：修后比修前好看吗？** 逐项看放大图。说不出"好看在哪里"的那一项，降一档或改成 `none`。
6. **和这张照片的气质合吗？** 冷酷、硬朗、有年代感的造型，眼下和脸上的阴影可能正是它要的。

| 结论 | 怎么办 |
|---|---|
| 都过 | 往下走 |
| 某一项过头、或不如修前 | 那一项降一档，重跑第 3 步，第 4、5 步重看 |
| 拿不准 | 留轻的那一版，在汇报里说清楚哪里拿不准，让用户决定 |

最多返工两轮。你的审美判断是给用户的参考，最后以用户的意见为准。

### 6. 汇报

告诉用户：成品和分层文档的完整路径；修了哪些项、各量到多少、各用了哪一档、画完再量是多少；哪些项没修和原因（没找到线、是眼妆、是表情、用户没要）；一共多少笔；第 5 步的审美结论（哪里变好了、哪里降了档、哪里拿不准）；还有没解决的 `[WARN]`。

再告诉用户**怎么自己检查**：在 PhotoCraft 里打开 `<原图名>_光影.pcraft`，最下面是没动过的「原图」，上面名字里带「AI 光影」的就是这一遍；开关它的眼睛对比修前修后；觉得重就降它的不透明度；局部不要的，中性灰版用 50% 灰的画笔盖掉，双曲线版在蒙版上涂黑；不够的地方照常接着刷。不要说"完美"，说你在检查图里实际看到了什么。

## 返工

- "再明显一点 / 太重了"：把那一项升一档或降一档，重跑第 3 步。
- 用户在 PhotoCraft 里改过 `.pcraft` 之后再跑，脚本会报错拒绝覆盖（避免丢掉他刷的东西）。问清楚后加 `--force`，或用 `--out` 另存一份。
- **某一项"没找到线"但用户想修**：脚本只沿着它找到的线画，没找到就不画。如实告诉用户这一项要手刷，不要改别的项来凑。
- **PhotoCraft 没找到脸，或者区域明显错位**：自己给五个点。在照片上读出两只眼睛的中心、鼻尖、两个嘴角的像素坐标，写成 faces.json，再给 `look` 和 `apply` 都加上 `--faces faces.json`：

  ```json
  {"faces": [{"rightEye": [1023, 715], "leftEye": [1232, 731], "nose": [1123, 839],
              "mouthRight": [1019, 928], "mouthLeft": [1198, 941]}]}
  ```

  `rightEye`、`mouthRight` 是**画面左边**的那一个（人物自己的右边）。给完先跑 `look`，读图确认区域落对了再画。读坐标读不准时不要硬来，那几项用 `none`。
- 眼睛间距不到 70 像素的小脸，脚本会跳过并用 `[NOTE]` 说明：像素太少，量不准也画不准。

## 脚本的提示

| 提示里说 | 意思 | 怎么办 |
|---|---|---|
| `[SUCCESS] measured only, nothing painted` | `look` 正常结束 | 读图，定档，跑 `apply` |
| `[SUCCESS] no face found …` | PhotoCraft 没找到脸，什么都没写 | 画面里确实有脸就按"返工"给五个点；否则告诉用户这张留给手工 |
| `[SUCCESS] nothing to paint …` | 没有一项要画，什么都没写 | 正常结果。告诉用户量到了多少、为什么不用刷；不要为了出文件硬加档 |
| 表里的 `no line found` | 这一项没找到纹路 | 没有纹，或纹太浅、被挡住。写了档位也不会画 |
| 表里的 `plain to see: clear is worth considering` | 这个数已经算明显 | 看图确认后可以写 `clear`；`auto` 自己只取一半 |
| 表里的 `22 or more is eye makeup or a cast shadow` | 眼下暗得不像黑眼圈 | 看图：是妆或影子就保持不修；确实是很重的黑眼圈才手动写 `circles=some` |
| `[NOTE] N face(s) … left alone` | 太小的脸被跳过 | 告诉用户 |
| `[WARN] a place moved by N levels, more than …` | 有个地方比一笔允许的重：几笔叠在了一起（常见于眼下：`circles` 加 `troughs`） | 在 `light_map.jpg` 里找到它，读那里的放大图；看得出来就把叠在那里的项降一档 |
| `[WARN] the picture changed outside the faces' own areas` | 脸以外的地方也变了 | 读成品，把哪里变了告诉用户；不要交付 |
| `[Error] … not as this script last wrote it` | 成品被用户改过 | 见"返工" |
| `[Error] the new layers alone changed the picture …` | 这个版本的 PhotoCraft 建出来的图层和技能预期的不一样 | 把原话告诉用户，不要继续 |

## 须知

- 全量也只去掉量到的一部分（在测试人像上：斑驳两到四成，眼下的暗两到三成，纹路那条线三到四成）：这是给用户接着修的底，不是成品。
- 肤色不匀是用笔点近似出来的（额头最多 80 个、脸颊 110 个、下巴 50 个，从最明显的小块画起），比整张算出来的图粗。
- 只改明暗：黑眼圈发青、泛红这类颜色问题不处理。只修脸，不修脖子和身体。几张脸时每张各自量；`--needs` 写明的档位对每张脸都一样，要分别对待就用 `auto`。
- `auto` 的门槛、`shape` 的位置和力度都是起步值，没有经过用户逐张验收。抬头纹、眉间纹在测试里只用画上去的线验证过（测试人像上没有真的）。所以流程里要求先 `look` 再定档、画完逐张看图。
- 人脸位置来自 PhotoCraft 自带的识别（五个点），不依赖 Apple 的系统功能，Windows 上一样能用。大侧脸、低头仰头很厉害、五官被挡住时区域会偏。
- 一张 850 万像素的照片，`look` 约 1 秒，`apply` 约 2 秒；`.pcraft` 约 22 MB。
- 修改脚本后运行 `tests/run.sh`。每个数字是怎么量出来的（画笔一笔的高度和宽度、区域怎么从五个点画出来、线怎么找、各个门槛）见 [references/method.md](references/method.md)。
