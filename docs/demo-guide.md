# P6.1 控制台演示操作说明

页面已改为中文问题叙事：先看顶部“发生了什么”，再看任务因果链、修改与保留、恢复结果和全局校核。[改版报告与新版截图](demo-ui-redesign-report.md)保留具体数据来源与界面边界；原 Phase 6 性能结果不变。

这是二维任务态势演示，数据来自实际 P2–P5.1 引擎。默认 SC08、Deterministic Realtime，无模型凭据也能演示。实时 Agent 使用当前 P5.1 的 Qwen 配置，结果与耗时以每次运行记录为准。

## 安装与启动

本机已安装依赖，仓库根目录直接执行：

```bash
bash scripts/run_demo.sh
```

打开 <http://127.0.0.1:5173>；交互式后端文档为 <http://127.0.0.1:8000/docs>。
脚本读取本地 `docs/API`（若存在），按已有方式仅传入后端进程；浏览器不持有凭据。
已经启动时不要重复占用 8000/5173 端口。Ctrl+C 同时停止前后端。

新环境要求 Python 3.10+、Node 20.19+ 或 22.12+（本轮 Node 24.14.0）。安装：

```bash
python3 -m pip install --target /tmp/cluster-reconstruction-deps -r requirements-dev.lock
cd frontend
npm ci
cd ..
bash scripts/run_demo.sh
```

也可先激活 `.venv` 后运行脚本；`CLUSTER_DEPS_DIR` 可覆盖独立依赖目录。前端 `.npmrc` 设置 `bin-links=false`，npm scripts 直接调用 Node 文件，兼容本项目移动磁盘不支持符号链接的情况。

分开启动时，后端执行 `PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m uvicorn backend.app.main:app --port 8000`；前端在 `frontend/` 内执行 `npm run dev`。需要真实模型时，后端改用 `python3 -m scripts.run_phase5_server --credentials-file docs/API --port 8000`（保留 PYTHONPATH）。只运行一个后端 worker。

## 现场讲解顺序

1. 选择 SC08，确认顶部 **执行模式 / 当前结果**。“实时确定性模式”明确 0 次模型调用；要演示模型选择技能，切换“智能体自适应模式”。
2. **加载场景**：60 节点、8 任务、6 编队，初始已实现约束可行。
3. **1 注入事件**：U17、U21 失效；加入禁区；T05 优先级改为 5。状态版本到 4，现场方案 INVALID。
4. **2 查看影响原因**：页面跳到中文因果链，直接讲解“节点失效→编队中继能力不足→任务需要重构”和“限制区域→航迹冲突→任务需要重构”。“任务状态评估”内是中文需求/可用/缺口表。分析随事件实际执行，此按钮仅定位讲解区域。
5. **3 运行重构**：勾选“分步讲解”时，在隔离分支执行求解、验证和内部事务，返回候选方案。此时现场仍需要重构，尚未提交。
6. 查看候选拟修改内容、原方案保留和全局校核。策略卡与候选漏斗可按需展开。只有通过后才允许 **4 校核并提交**；使用原 P4 事务在现场再次校核并发布，版本 4→5。
7. 比较“态势变化后 / 重构前”“重构后方案”“叠加对比”：违规任务恢复，失效节点仍是失效；绿表示方案变化，红表示失效/冲突。点击关注任务可突出对应对象，点击实体查看技术详情。
8. 展示模型耗时、算法/验证/提交耗时，以及五项 NOT_EVALUATED。不能把 PASS_WITH_LIMITATIONS 讲成物理执行完全保证。

**一键综合演示** 按当前选定模式自动加载 SC08、注入、展示影响并自动提交，中间有短暂讲解停顿。停顿不计入后端性能。取消“分步讲解”后，普通“运行重构”也会自动提交。演示 SC07 应使用首页对应卡或手动选择，不能用固定运行 SC08 的一键按钮。

## 三种模式与关键场景

| 模式 | 使用方式 | 必须解释的结果 |
|---|---|---|
| Deterministic Realtime | 默认演示、离线后备、算法性能 | 固定策略 baseline，model_calls=0 |
| Adaptive Agent | 模型自主选择 option/tool、反馈重规划 | pure_agent_success，真实模型耗时；不承诺 <5s |
| Bounded Agent | 最多两轮模型、5 秒 Agent 预算，然后按现有策略 fallback | fallback_used、fallback_result；预算不包含 fallback 的端到端保证 |

SC04 是 route-only，SC05 是 TargetMove，SC06 是能力与航迹混合异常。SC07 **请选择 Adaptive Agent**：局部 P3 工具先产生诊断提案；模型先验证，收到真实 `UNRELATED_TASK_REGRESSION` 后扩大范围并修正，再通过验证。第一份诊断提案标有来源，不宣称由模型生成。该场景 baseline 会失败，正是反馈闭环的对照。

SC02 默认隐藏；勾选“显示部分支持诊断场景”才出现。P2 对冗余能力判断 KEEP，但 P4 禁止保留失效编队成员，因此目前不能完成其“故障后无需重构”的完整验收。报告明确保留这一限制。

SC08 的 PriorityChange 只说明输入与策略排序属性变化，**动态优先级调度未评估**。不要将其演示成已经实现抢占或调度优化。

## 回放与故障表现

每次运行保存到 `artifacts/phase6/demo/runs/`。底部“真实运行回放”可选历史记录，点击“下一条决策 / 展示完整过程”，读取真实状态与记录，不再次调用模型。地图与结果始终显示该次最终记录，逐条回放只控制决策展示。后端重启后仍可读历史记录，但内存会话已清空，回放不能对新会话提交。

无凭据时 Adaptive 显示 `MODEL_NOT_CONFIGURED`；模型错误可在右侧查看。Bounded 明示 deterministic fallback。缺少凭据不会妨碍确定性演示。历史在线回放导入由下面的截图脚本复制**未修改原始记录**完成。

## 命令行复现与验证

启动服务后，从真实 REST API 完整执行 SC08 并保存 JSON：

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m scripts.demo_phase6
```

可加 `--mode adaptive_agent` / `--mode bounded_agent`。正式评测命令见[完成报告](phase6-report.md)。浏览器验证：

```bash
cd frontend
npm test
npm run build
DEMO_REAL_AGENT=1 DEMO_CHROME_PATH=/usr/bin/google-chrome npm run test:e2e
cd ..
DEMO_CHROME_PATH=/usr/bin/google-chrome node frontend/scripts/capture-evidence.mjs
```

最后一条需要先有正式 agent、feedback、bounded 评测文件；现在输出到 `artifacts/phase61/replays/`，保留原 P6 证据。`DEMO_REAL_AGENT=1` 启用实际在线 SC07 浏览器测试；不设时跳过该项。未安装系统 Chrome 时，可在前端目录运行 `node node_modules/@playwright/test/cli.js install chromium`，测试时省略 `DEMO_CHROME_PATH`。

真实截图：[SC08 baseline](../artifacts/phase6/browser/sc08-deterministic.png)、[SC08 Agent](../artifacts/phase6/browser/sc08-adaptive.png)、[SC07 验证反馈](../artifacts/phase6/browser/sc07-feedback.png)、[SC08 fallback](../artifacts/phase6/browser/sc08-bounded.png)。截图来源、run_id、时间和原始文件哈希见[在线截图元数据](evidence/phase6/browser-online-metadata.json)。

前端使用 [React](https://react.dev/learn)、[Vite](https://vite.dev/guide/) 和 SVG，浏览器冒烟使用 [Playwright](https://playwright.dev/docs/intro)。无大型地图或 3D 引擎。
