# Phase 6.2 动态任务演示操作指南

在仓库根目录执行：

```bash
bash scripts/run_demo.sh
```

打开 <http://127.0.0.1:5173>。首页保留 SC01–SC08 标准技术验证；点击右上角 **动态任务演示**进入 Random Challenge。标准模式用于固定 benchmark 和 Verified Replay，动态模式用于展示随机布局、持续执行和多轮在线重构。

## 推荐的现场演示

1. 保持默认 60 节点、8 任务、L3、2×；演示稳定性优先时选择“实时确定性模式”，展示模型决策时选择“智能体自适应模式”。填写场景 seed 和事件 seed 可以复现；留空会随机生成。
2. 点击 **生成新任务场景**。先讲顶部的双 seed、节点/任务/编队规模和 8/8 初始可行，再看地图中各编队、任务目标和航迹的随机布局。
3. 点击 **开始运行**。节点随编队参考点沿路线移动，右侧“任务执行进度（仿真）”持续变化，并显示“有效执行 300s · 时间窗 600s”。这 300 秒冗余用于容纳事件、重构和恢复；顶部 Planning State 版本不会随动画跳变。
4. 可用 **暂停 / 继续 / 单步 +1s / 速度**控制服务端仿真时钟。需要讲解某一帧时先暂停。
5. 在“运行时态势注入”选择 L1 并点击 **生成动态事件**。页面展示影响链，但时间线给出“当前任务方案仍可继续”，任务不中断，也不出现重构按钮要求。
6. 再选择 L3。系统生成节点故障与航迹限制区域，TRDG 图显示“节点→编队→能力→任务”和“区域→航迹→任务”的真实传播链，任务变为 BLOCKED。
7. 点击 **重构并校核提交**。Agent 或 baseline 通过既有 P2–P5.1 核心生成 Proposal，Global Validator 校核后事务提交。TRDG 下方随后展示该轮真实 Run 的方案 Delta、候选筛选、策略与工具执行、恢复结果、Validator 和耗时；任务短暂显示 RECOVERED 后继续执行。
8. 再注入一次 L2/L3，重复重构。持续任务指标应显示多次动态事件和至少两次成功重构，同一地图继续推进，不会在第一次提交后结束。
9. 点击 **保存当前随机场景**保存 scenario JSON。页面底部选择“历史真实动态任务回放”，拖动 Checkpoint 滑块查看持久化运行位置和当时 Planning version；回放不会重跑模型。

## 难度与操作方式

L1 用于演示“受影响但无需重构”；L2 用于局部编队修复；L3 用于节点能力与航迹混合问题；L4 用于展示局部修复被全局 Validator 拒绝后扩大范围。自动事件模式会在随机仿真间隔后采样当前难度；手动/半随机适合现场讲解。

当重构尚未结束时，新事件会显示为等待事件，并在当前事务完成后应用。Adaptive Agent 使用 `docs/API` 配置的 Qwen；未配置模型时仍可切换实时确定性模式完成整个动态演示。

## 建议录屏脚本

按“生成随机挑战 → 开始运行 → L1 无需重构 → 继续 → L3 突发事件 → 查看 TRDG → 重构与提交 → 继续运动 → 第二个 L2 事件 → 第二次重构 → 打开真实回放”的顺序录制。页面应称“二维任务态势动态仿真”或“动态任务环境”，不要称为高保真无人机仿真。

当前浏览器验收截图位于 [dynamic-continuous-mission.png](../artifacts/phase62/browser/dynamic-continuous-mission.png)。它来自真实 REST API，会话包含 3 个事件、2 次事务提交和 Runtime Checkpoint 回放。

## 自动验证

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m pytest backend/tests/test_phase62_dynamic.py -q
cd frontend
npm test
npm run build
DEMO_CHROME_PATH=/usr/bin/google-chrome npm run test:e2e -- --grep "dynamic mission"
```

两个或三个真实 Agent 小样本可在已用专属凭据启动后端后运行：

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m scripts.smoke_phase62_agent \
  --seeds 62001,62002 --output artifacts/phase62/agent-smoke.json
```

脚本只访问本地 REST API，不读取或打印密钥；后端按 `scripts/run_demo.sh` 的既有方式加载凭据。
