# 测试

全部在命令行里运行，不打开任何窗口（不启动 Compositor，也不打开访达）。

```bash
python3 -m pip install "opencv-python-headless<5" pillow numpy ai-edge-litert   # ai-edge-litert 可选
tests/run.sh                         # 全部
tests/run.sh glow benches            # 只跑某几套
PYTHON=/path/to/python3 tests/run.sh # 指定解释器
KEEP=1 tests/run.sh skin-stack       # 保留生成的工程、日志和检查图（路径会打印出来）
tests/run.sh timing                  # 另跑：24MP 照片走 dr5 预设加中性灰的耗时
```

第一次运行会下载约 7 MB 的测试照片，并编译五个小工具（Swift 和 C，约 1 MB），都缓存在 `~/Library/Caches/compositor-retouch/tests/`。装了 `ai-edge-litert` 时，skill 自己还会下载 16 MB 的分割模型，缓存在 `~/Library/Caches/compositor-retouch/`。测试照片是 scikit-image 的 astronaut，加上几张 NASA 公有领域的航天员标准照，肤色深浅各异。生成的工程写在临时目录，跑完删除。

| 套件 | 内容 |
|---|---|
| `format` | 新建、追加、拒绝覆盖、`--force`、坏工程回滚、错误输入、已去掉的模块名被拒；运行期间工程被保存过时（连包内等待的 `.retouch/` 一起被替换）commit 拒绝覆盖、什么都不留；新文件在提交前留在 `.retouch/`、提交时一次落地：每 50 ms 采样 Compositor 的工程指纹（manifest 字节 + images/ 文件名和大小），整次运行只变一次 |
| `skin-stack` | 图层文件夹的顺序、皮肤层组在文件夹里的顺序（新工程、追加到 dr5、studio），检查图，`--delicious-skin manual` 的黑蒙版（皮肤以外和蒙版羽化外半段都等于原图、脸以外的皮肤也有磨皮），图层改名只留 emoji 后仍被认出重做，`--strength`；`daily` 预设在照片和已有工程上，连跑三次不堆图层（中性灰、双曲线、骨相、颗粒、调色、观察组跳过，磨皮原地重做），`--skin-strength 0.6` 重做时保留图层和蒙版文件、不透明度变 53%；高低频半径跟着脸的大小走（同一张脸在画面里小 1.5 倍，半径也小 1.5 倍），没有人脸时按分辨率；颜色观察组里平均肤色是灰的、人工点上的红斑是红的（叠了调色也一样），黑白观察组里两种肤色的皮肤都在中灰附近 |
| `folders` | 以前版本的工程（把文件夹去掉模拟出来，和旧版写的逐层一致，含旧版的自动中性灰层）：分文件夹前后渲染逐像素相同；追加模块时旧图层原地不动、新文件夹插在同类旧图层上方；高低频不重复；往隐藏的文件夹里加图层时警告 |
| `refresh` | `--refresh`：什么都没改时重算画面几乎不变、manifest 和所有蒙版不变、没有残留文件；在高频层上抹掉一块纹理，重算前被磨皮快照盖住，重算后显出来；工程会被拒时一个文件都不动；没有可重算的层时提示；按图层名里记下的力度重算；以前版本的工程也能重算 |
| `deep` | 16 位照片新建工程（`Make16.swift` 写出带 Display P3 配置文件、旋转 90° 的 16 位 TIFF）：原图是 16 位图层、格式 12，已转正，颜色和 8 位的转法一致，精度保留，高低频两层是 16 位且合起来就是原图，磨皮层也是 16 位且皮肤上有超过 8 位的层次，重做、重算后仍是 16 位；`--8bit` 做成 8 位、格式 11。还有 Compositor 1.5 的工程：16 位 PNG 图层（版本 12）和 ImageIO 写出的 OpenEXR 图层（版本 13，`ToEXR.swift`）能检查、追加、渲染，读出的原图和 8 位原图一致，HDR 高光截到白，版本号不变；版本 11 的工程里出现 16 位图层要拒绝 |
| `vision` | 脸的上下方向按左右眼标签和嘴定（倒着的脸轴向翻转、骨相蒙版落在同一处）；歪头、两个人、`--no-vision`、没有 swiftc、`--no-parsing`、漏掉人脸的人像分割不被采用；设了 `PYTHON5`（装 OpenCV 5 的环境）时再测 OpenCV 5 |
| `glow` | 辉光各风格和颜色、非法写法被拒、图层顺序、坏的图层效果被拒；把脚本的合成结果和 Core Graphics 逐像素比较（按 Compositor 导出的方式合成） |
| `render` | 用 Compositor 自己的代码（`AdjustReference.*`，从它的源码抄来，附 MIT 许可声明）逐像素核对脚本移植的调整层算法；`--render` 不改工程；6 种调色都能生成 |
| `benches` | 量化测试（`bench.py`）。内容：皮肤蒙版的覆盖率；磨皮（`smoothing`）淡掉多少画上的痘印、真实的法令纹和眼袋留下多少、五官旁边的皮肤被改了多少，并要求默认的 `form` 引擎在这几项上胜过旧的 `guided`。低于调参时记下的门槛就标 BAD |
| `heal` | 在已知位置人工点斑（`heal_eval.py`），看磨皮先行的祛痘找回了多少、有没有误修；`dense` 是痘痘肌：整张脸 40 个斑，大多"稍暗又稍红"，只搜内脸的旧方法找到 12 个；还检查 `--delicious-skin` 的图层名标出祛痘数和痘痘肌 |
| `validator` | 校验程序本身还能识别 Compositor 会拒绝的写法 |

每套最后都用 `CompValidator` 检查生成的工程，它按 Compositor 自己的加载规则校验。任何一行出现 BAD、FAIL 或 Python 报错，这一套就算失败，最后汇总。

`CompValidator.swift` 抄写了 Compositor（MIT 许可，Wonder Assembly LLC）的工程格式类型和校验规则（到格式版本 13，commit `e8c9e29`），许可声明在文件开头。
