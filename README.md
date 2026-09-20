# UNO Show 'Em No Mercy — Python 36-card edition

Windows 桌面重写版，使用本项目 SB3 的 168 张牌、造型、字体、音乐与语音。
支持 2–4 人离线 AI 对战，以及同一局域网内真人与 AI 混合对战。

## 运行

解压 `dist/UNO_No_Mercy_Windows.zip`，双击里面的 `UNO_No_Mercy.exe`。
请保留整个目录及 `_internal` 子目录。游戏无需安装 Python、Scratch 或浏览器。

从源码运行（Python 3.14）：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.\.venv\Scripts\python.exe -m uno
```

已生成的 `assets` 可直接使用。`sb3_assets` 和原始 SB3 是资源导入的源文件。

## 操作

- **Play with AI**：填写名字，选择 2、3 或 4 人和 AI / Elo 后开始。**Normal（正常）**保留原有强度；**Hard（困难）**使用策略 AI；**Devil（魔鬼）**使用模拟搜索 AI。默认正常，选择会自动保存；按钮上的数字是双人校准的相对 Elo。
- 动画以 **1.5 倍速**播放，包括发牌、抽牌、换手、计分、悬停和颜色选择；语音与音乐保持正常速度，联机房主使用相同的加速动画时长安排 AI 动作。
- 鼠标悬停放大手牌；点击亮色的合法牌出牌。点击右侧牌堆或 Draw 按钮抽牌。
- 万能牌选择颜色；7 选择交换对象。颜色轮盘由受罚的下一位玩家选颜色。
- `←` / `→` 选择手牌，Enter 出牌；`D` / 空格抽牌，`M` 静音，`F11` 全屏，`Esc` 返回或打开离开确认。
- Messages 使用原版 20 条预设短语；问号打开原版说明页。
- UNO 自动喊出。当前 No Mercy 模式没有普通的“结束回合”按钮，也没有经典 UNO 的 +4 挑战。
- 累计分数达到两人 500、三人 750、四人 1000 后赢得整场；New match 重新计分。

## 局域网

1. 房主选择 **Create LAN room**，使用默认端口 `8765` 或自选端口。
2. 房间显示本机局域网 IP 和端口。其他玩家在 **Join LAN room** 输入该地址，例如 `192.168.1.10:8765`。
3. 房主可添加 AI、移除座位，并在大厅选择 AI / Elo；真人客人点击 Ready，房主点击 Start。房内所有 AI（包括掉线接管）使用同一难度，开局后固定，下一局沿用；客人看到房主提供的 Elo 分数。
4. Windows 防火墙询问时，为游戏允许“专用网络”；两台电脑需处于可互相访问的网络。如果有多个网卡，使用朋友能访问的那个本机 IPv4 地址。

游戏不需要互联网。第一版使用 IP 直连，不提供自动发现、公开匹配或 NAT 穿透。
房主决定牌序和规则，只向客人发送其自己的手牌及公开信息。

非房主掉线后由 AI 接管；客户端自动尝试重连。关闭重开程序后，使用相同地址和保存的令牌也能回到原座位。
重连玩家在自己下一个回合恢复控制，AI 会先完成当前操作。房主退出则结束整个房间。
回合中仅接受重连，不接受新玩家加入；不要把局域网服务端口暴露到公共互联网。

## 规则依据

本版按 SB3 的实际 No Mercy 分支实现，**唯一主动修改的玩法是手牌达到 36 张即淘汰**。
部分细节与实体 UNO 官方规则不同，详见 `docs/RULES.md`。
保留原版四种颜色、叠加限制、7 换手、0 轮转、特殊牌与计分；未增加其他 UNO 模式。

## AI 难度

- **正常 / Normal**：沿用原版随机合法出牌、随机换手和手牌内随机选色。
- **困难 / Hard**：优先争取直接出完，评估弃同色牌和连续回合的后续出牌；结合各家公开张数使用 7 换手、0 轮转、跳过和罚牌；根据剩余手牌选色，并单独处理颜色轮盘和双人反转 +4。

- **魔鬼 / Devil**：搜索连续行动直接出完的路线，记忆公开弃牌，并对可能的未知牌分布进行多次模拟，比较候选动作的胜算。离线和联机均在后台计算，避免思考时阻塞界面。

正常、困难、魔鬼 AI 只读取自己的手牌及公开信息，不读取真实对手暗牌或牌堆顺序。模拟中的暗牌是随机假设，规则与其他难度相同。

- **God**：全知难度，可以读取所有手牌、弃牌及完整牌堆顺序，按真实牌局进行逐层加深的对抗搜索，完整结算罚牌与轮盘，规划颜色封锁、反叠罚牌及淘汰。颜色轮盘按真实牌序（包括回收洗牌）选择最近可命中的颜色。牌序在洗牌之间缓存，模拟分支共享牌序并分别推进索引；强制摸牌直接执行。选择时先显示警告，深红色 Yes 确认，No 取消。它仍遵守出牌规则；全知不表示保证获胜。不计算或显示 Elo。

## AI 相对 Elo

普通 AI 固定为 **1000** 分基准；困难 AI 为 **1174** 分（旧版 35 张规则下的历史参考）；魔鬼 AI 最新为 **1267** 分（36 张规则下的间接校准）。God 不显示 Elo。

最新校准在附件 AI 自带的规则引擎中进行，按双人单局胜负计算：

- 附件 AI 对普通：种子 30001–30200，交换座位共 400 局，324 胜、76 负，胜率 81%，拟合 **1251.02** 分。
- Devil 对附件 AI：独立种子 40001–40200，交换座位共 400 局，Devil **209 胜、191 负，胜率 52.25%**；比附件 AI 高 **15.61** 分，得到 **1266.63** 分。
- 同时考虑两组测试的不确定性，Devil 的成对 bootstrap 95% 区间约为 **1213–1325**。这次对战优势较小，不能据此认定两者存在明显强度差距。

双方使用未修改的策略与默认搜索预算，附件学习权重固定。接口只向 Devil 提供自身手牌与公开信息，包括轮盘公开牌。附件引擎的洗牌及计分实现不同，因此新分数应视为该测试环境中的间接标定，不能把与旧 1300 分的差值解释为策略变弱。3／4 人游戏沿用双人标定数字。

分差按 `400 × log10((胜局 + 0.5) / (负局 + 0.5))` 计算。程序从 `assets/ai_elo.json` 保存的两段真实战绩重新计算，不信任手工填写的参考分。旧 Devil 直接校准证据也保存在该文件中。

复现新校准（需保留 `build/external-ai-evaluation/source` 中的附件副本）：

```powershell
.\.venv\Scripts\python.exe tools/evaluate_imported_ai.py --seeds 200 --start-seed 30001 --workers 8 --output build/external-ai-evaluation/result.json
.\.venv\Scripts\python.exe tools/evaluate_imported_ai.py --opponent devil --seeds 200 --start-seed 40001 --workers 16 --output build/external-ai-evaluation/devil-result.json
.\.venv\Scripts\python.exe tools/publish_imported_ai_calibration.py --reference build/external-ai-evaluation/result.json --duel build/external-ai-evaluation/devil-result.json --report build/external-ai-evaluation/devil-calibration.json
.\.venv\Scripts\python.exe tools/build_release.py
```

仍可用 `tools/calibrate_elo.py --challenger devil` 在本项目引擎中重新进行直接对普通的校准；发布结果会替换该难度的现行证据。

## 保存与诊断

设置、重连令牌、最近离线回放、房主回放和错误日志保存在 `%LOCALAPPDATA%\UnoNoMercy`。
令牌仅用于回到当前房间。回放包含完整牌序，只保存在离线玩家／房主本机，不发送给客人。
可以用环境变量 `UNO_USER_DIR` 指定不同目录。

```powershell
.\.venv\Scripts\python.exe -m uno --replay path\to\replay.json
```

## 资源重建

仅开发者重建素材需要 Node.js 和 Chromium。已打包游戏不需要它们。

```powershell
.\.venv\Scripts\python.exe tools/prepare_assets.py
npm ci --prefix tools/renderer --no-audit --no-fund
node tools/renderer/render.mjs
.\.venv\Scripts\python.exe tools/finalize_assets.py
```

渲染脚本默认使用已安装的 Chrome 或 Edge，也可设置 `UNO_CHROMIUM_PATH`。
SVG 使用 Scratch 自带的字体渲染；空 SVG 输出透明占位图。
原版说明第 1 页嵌入了带“25”的位图，本版用同一手写字体重新排版为 36 张规则；其他页复用原版。
源文件不修改；资源索引保存原文件名、角色／造型映射、旋转中心和位图比例。

## 测试与打包

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider
.\.venv\Scripts\python.exe tools/benchmark_ai.py --seeds 100 --output build/ai-benchmark.json
.\.venv\Scripts\python.exe tools/smoke_lan.py
.\.venv\Scripts\python.exe -m uno --smoke game --screenshot build/screenshots/game.png
.\.venv\Scripts\python.exe tools/build_release.py
.\.venv\Scripts\python.exe tools/smoke_release.py
```

`tools/renderer/baseline.mjs` 在本机 Scratch VM 中读取原 SB3，保存原版开场、大厅、牌桌和状态到 `build/baseline`，不上传项目。
`tools/benchmark_ai.py` 可用 `--challenger devil --opponent hard --players 2` 比较魔鬼与困难；默认让一名困难 AI 对战其余正常 AI，并遍历 2／3／4 人局的全部座位；默认每个座位 100 个种子，共 900 局，输出实际胜率与决策耗时。
已通过规则、动画、UI 和联网测试、75 局固定种子 AI 对局、本机双客户端进程整局，以及 EXE 独立目录启动／联机服务冒烟验证。Python 依赖在 `requirements.lock.txt`，素材构建依赖在 `tools/renderer/package-lock.json` 锁定。
动画保留原版逐帧运动轨迹，以每秒 45 个逻辑帧播放原版 30 帧节奏，即 1.5 倍速。源脚本依据、逐帧对照和 GIF 预览生成方法见 `docs/ANIMATIONS.md`。
视觉与测试记录见 `docs/VERIFICATION.md`。两台实体电脑以及完全未安装 Python 的 Windows 环境需要设备补验。

## 项目结构

- `uno/engine.py`：牌组、纯规则状态机、可见状态及回放。
- `uno/ai.py`：难度入口及正常／困难 AI。
- `uno/devil.py`：魔鬼 AI 的连续出牌搜索与公开信息模拟。
- `uno/elo.py`、`tools/calibrate_elo.py`：累计对战 Elo 拟合、双人轮换座位校准与结果加载。
- `uno/network.py`：房间、主机判定、私有快照、掉线与重连。
- `uno/app.py`、`uno/layout.py`、`uno/resources.py`：Pygame 界面、布局、动画、音乐及语音。
- `tools/`：只读 SB3 检查、资源构建、原版对照、联机冒烟和发行打包。
- `tests/`：规则、完整对局、素材、UI 操作、网络和大手牌选取测试。
