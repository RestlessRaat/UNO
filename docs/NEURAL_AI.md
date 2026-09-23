# 本地神经网络 UNO

本功能提供从随机权重开始的自战训练、断点续训、独立评测和游戏内推理。
当前短训练模型是验证流程的实验模型，未经强度认证，不应标为 Hard/Devil 的替代品。
实现计划见 [NEURAL_AI_PLAN.md](NEURAL_AI_PLAN.md)。

## 本机启动

在项目根目录打开 PowerShell：

```powershell
# 打开游戏，所有 AI 席位默认使用 Neural，可在每个席位的 AI 设置中更改。
.\launch_neural.cmd

# 短训练：自动继续 build/neural/local/latest.pt；不存在时随机初始化。
.\train_neural.cmd --updates 20 --device cuda

# 后续较长训练：在一轮完整更新结束后检查时限并保存，最多约一小时。
.\train_neural.cmd --updates 100000 --minutes 60 --device cuda
```

启动器在窗口出现之前加载模型，使用 CPU 推理和现有 AI 后台执行器。
独立的本地游戏资料保存在 `build/neural/game-user`，不会覆盖普通游戏设置。
它运行项目源码；旧的 `dist` EXE 没有新接口和 PyTorch，不能仅复制插件就获得此功能。

默认模型路径是 `build/neural/local/actor.pt`。指定其他模型：

```powershell
.venv\Scripts\python.exe tools/play_neural.py --checkpoint build/neural/another-run/actor.pt
```

启动后选择 **Play with AI**，再开始游戏。默认从策略分布采样；插件设置中的
**Choose highest probability** 可以改为每次选最大概率动作，两种方式的评测应分开。

## 环境与依赖

本机验证环境：Windows、Python 3.14.0、PyTorch 2.11.0+cu128、NumPy 2.4.6，
RTX 5070 Laptop GPU（8 GB）。既有 `.venv` 允许读取系统包，已包含这些依赖。
训练时显式 `--device cuda` 会在 CUDA 不可用时报错，`--device auto` 可自动选 CPU。

在新机器准备环境（只需首次执行；当前机器无需重复安装）：

```powershell
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.lock.txt
.venv\Scripts\python.exe -m pip install torch==2.11.0 --index-url https://download.pytorch.org/whl/cu128
.venv\Scripts\python.exe -m pip install -r requirements-neural.txt
```

GPU 兼容性取决于相应驱动和 PyTorch 构建。普通游戏不导入神经模块，也无需安装 ML 依赖。

## 模型和信息边界

- Actor：1,065,936 参数。手牌是 68 种牌型的无序多重集合，2 层 128 维、4 头
  注意力；全局状态和最多 4 名相对座位玩家使用另外 2 层注意力。
- 历史：最近 64 个合法可见事件经过 256 维 GRU。每次从窗口起点重新编码，
  对窗口中的全部事件反向传播，不在玩家之间共享隐藏状态。它不是无限记忆。
- 动作：规则引擎提供合法动作，等价实体牌按牌型合并，候选动作评分并掩码。
  编码器不把实体牌 ID、随机种子、名字或牌堆顺序当作可学习特征。
- Critic：199,940 参数，与 Actor 完全独立；输入各玩家真实手牌计数、牌堆
  牌型直方图和公共状态，输出绝对座位顺序的 4 个价值。未使用真实牌堆顺序。
- 辅助头：预测 3 名相对对手和牌堆的牌型比例，以真实隐藏状态为训练标签。
  它是边缘分布学习信号，不能当作满足整副牌守恒约束的完整贝叶斯信念。
- 插件 v1 增补公开 `history` 和合法得知的 `known_opponent_cards`；使用默认值
  保持已有插件兼容。回放引擎、规则和网络协议版本保持现有行为。

## 自战与奖励

单进程推进多份无界面规则环境，在 GPU 上批量执行 Actor/Critic 推理和 PPO 更新。
它是同步批处理；CPU 多进程采样、自动 exploiters 和胜率优先配对尚未实现。
前几轮由当前模型自战；定期保存冻结快照并形成最多 4 个历史对手。默认一半新局
从历史池随机选对手，并随机选择一名当前策略席位，其余席位用冻结历史策略。
另一半新局的所有席位使用当前策略。历史策略的动作不进入 PPO 策略损失。

默认 `round` 目标在一局结束时奖励赢家 +1、其他人各 -1/(人数-1)，中途没有手工
行为奖励。`match` 目标在累计比分达到真实规则目标时才产生同样的终局奖励，
各局间保留分数。它不叠加每局积分奖励，从而避免悄悄改变最大化比赛胜率的目标。
`gamma=0.997` 的折扣仍可能偏好较早得到的奖励；应作为超参数评估。

GAE 在全局动作时钟上为每个座位计算，包括已淘汰但仍等待结果的玩家。
每个采样批次结束时用 Critic 自举；达到步数上限也自举并切断轨迹，不能算作失败。
终局才将下一状态价值置零。训练日志单独记录完成局数与截断次数。

训练种子限定为 1–59999，评测预留 60000–65535。原规则的洗牌器只有 16 位状态，
预留种子有助于评估泛化，但其组合空间和随机性依然受原引擎限制。

## 新训练和续训

为新实验使用新目录。`train_neural.cmd` 固定服务本地默认实验，其他实验请调用模块：

```powershell
# 2–4 人混合，完整 No Mercy 规则，单局胜率目标。
.venv\Scripts\python.exe -m uno.neural.training --output build/neural/round-v2 --updates 100 --envs 32 --horizon 128 --device cuda

# 完整比赛目标需要单独训练，不能在恢复时偷偷更改原实验目标。
.venv\Scripts\python.exe -m uno.neural.training --output build/neural/match-v1 --objective match --max-episode-steps 20000 --updates 100 --device cuda

# 恢复权重、优化器、模拟器、联赛和 RNG；updates 是追加的更新次数。
.venv\Scripts\python.exe -m uno.neural.training --resume build/neural/round-v2/latest.pt --output build/neural/round-v2 --updates 100 --minutes 30 --device cuda
```

续训恢复检查点中的超参数；新传入的 envs/players/objective 等不会覆盖原值。
device、threads、updates、minutes 和输出路径控制本次执行。
规则文件哈希或编码版本不匹配会拒绝加载，避免将不同规则的经验无声混在一起。

每轮更新后原子替换检查点。Ctrl+C 保留最后一轮已完成更新，丢弃尚未保存的部分。
若 `latest.pt` 已存在，新训练拒绝覆盖，必须明确传 `--resume` 或新目录。
本流程不启动后台常驻训练，也不自动下载模型。

每个运行目录包含：

| 文件 | 用途 |
|---|---|
| `run.json` | 硬件、训练配置、参数量、规则指纹 |
| `metrics.jsonl` | 每轮损失、熵、KL、梯度、吞吐、完成局数和历史对手动作数 |
| `initial-actor.pt` | 随机初始化基线 |
| `actor.pt` | 游戏加载的 Actor 和辅助头，无 Critic/优化器 |
| `latest.pt` | 完整续训状态：模型、优化器、RNG、在途游戏、联赛 |
| `evaluation.json` | 独立评测结果（评测命令指定时产生） |

检查点采用 `torch.load(..., weights_only=True)`。仅加载自己产生或可信来源的文件。

## 评测与部署检查

```powershell
.venv\Scripts\python.exe -m uno.neural.evaluate build/neural/local/actor.pt --seeds 20 --opponents normal hard --output build/neural/local/evaluation.json
.venv\Scripts\python.exe -m uno.neural.evaluate build/neural/local/initial-actor.pt --seeds 20 --opponents normal hard --output build/neural/local/initial-evaluation.json
.venv\Scripts\python.exe tools/play_neural.py --smoke
.venv\Scripts\python.exe -m pytest tests/test_neural.py tests/test_ai_plugins.py -q
```

评测将一个神经 AI 放到每个座位，其他玩家使用固定对手，分别汇报 2/3/4 人结果。
轮换座位共享洗牌种子，报告中的 Wilson 区间仅作描述，不校正这些配对相关性。
小样本不足以证明强度提升。正常棋力发布需要更多未参与调参的种子、不同策略池
对手，以及匹配实际部署采样方式的评测；不把短训练结果转换为确定 Elo。

`--smoke` 运行真实游戏的后台 AI 执行器和绘制流程，要求模型至少成功执行一步且
没有回退 Normal，并保存 `build/neural/ui-smoke.png`。

## 本次验证记录（2026-09-21）

按“先完成短训练和部署验证”的范围完成，没有留下后台长训练任务。

- CUDA：16 个环境、128 步采样、每轮最多 3 个 PPO epoch，先完成 8 次更新，
  再退出进程并从 `latest.pt` 恢复追加 1 次更新。
- 累计 18,432 个环境动作、63 个完整训练局，0 次步数截断；历史池有 4 个冻结
  快照。更新循环累计约 75 秒，不包含解释器启动、测试与独立评测。
- `actor.pt` 约 4.1 MiB，`latest.pt` 约 31 MiB。后者保留优化器、随机数状态、
  正在进行的牌局和历史对手，可直接继续训练。
- 初始随机模型和训练后模型分别使用 4 个预留种子，在 2/3/4 人的全部座位对战
  Normal、Hard，各完成 72 局，没有截断。以下数字是神经玩家的胜局/总局数：

| 人数 / 对手 | 随机初始化 | 9 次更新后 |
|---|---:|---:|
| 2 / Normal | 4/8 | 4/8 |
| 2 / Hard | 0/8 | 1/8 |
| 3 / Normal | 2/12 | 1/12 |
| 3 / Hard | 0/12 | 0/12 |
| 4 / Normal | 3/16 | 0/16 |
| 4 / Hard | 1/16 | 1/16 |

这次实验验证了训练和部署链路，没有证明棋力提升；部分小样本指标下降。
当前模型仍很弱，不能替代现有强规则 AI。原始逐局结果分别保存在
`build/neural/local/initial-evaluation.json` 和 `evaluation.json`。
训练后评测耗时约 17.7 秒，CPU 批量推理摊销约 1.79 ms/决策；这不是独立单次
UI 推理延迟，也不是游戏帧率测量。

真实 App 的后台执行器已成功加载 `local.neural` 并执行动作，未触发 Normal
回退，截图保存在 `build/neural/ui-smoke.png`。实现阶段完整回归为 229 项通过；
随后新增两项完整比赛奖励测试，再运行神经模块 11 项全部通过。覆盖了隐藏信息
隔离、牌序不变性、合法动作、真实参数更新、截断自举、历史对手损失屏蔽、
检查点恢复和跨局分数保留。测试有一条既有 pytest `cache_dir` 配置警告。

## 后续扩展顺序

1. 扩大独立评测样本；先用单局目标确认优于随机初始化与 Normal 的证据。
2. 增加 CPU 多进程环境，减少当前规则/观察编码成为瓶颈的比例。
3. 引入颜色置换增强、胜率优先的历史对手抽样和专门的 exploiter。
4. 完整比赛训练；比较折扣和课程安排，避免仅优化短局打法。
5. 如需要更长记忆，再做有 burn-in 的跨窗口循环 PPO；有稳定强度后再优化
   ONNX/独立推理进程与 EXE 打包。
