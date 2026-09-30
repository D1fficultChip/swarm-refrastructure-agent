# Phase 6：场景验证、系统评测与最小演示 Demo

验证日期：2026-09-27。交付版本 0.6.0。已完成二维 Web Demo、8 场景定义、评测器、聚合 API、真实浏览器验证；止于 Phase 6，未开发 MCP、RAG、多 Agent 或训练模块。

## 完成边界与历史事实

沿用 P2–P4 原始确定性能力、P5.1 `OptimizedAgentOrchestrator`、稳定 option_id、DecisionSnapshot、Validator feedback replanning。未修改这些核心算法；P6 增加应用服务、展示投影、计数和评测代码。原 [P5.1 报告](phase5.1-report.md) 与 evidence 保留。历史 A/B/C/D 在线闭环成功，但对应 p95 约 8.52 / 13.42 / 8.00 / 18.15 秒，**没有云 Agent p95 <5s 的保证**。本轮不重新解释历史成功率或替换旧数据。

本轮结论：确定性重复实验 400/400 成功且 <5s；新增 SC06/SC08 真实 Adaptive 各 5/5 成功；SC07 真实反馈恢复 3/3 成功。SC02 的完整“故障后无需重构”目标受现有 P2/P4 约束语义差异阻碍，明确为 PARTIALLY_SUPPORTED，未通过改核心或隐藏验证解决。

## 场景支持矩阵

固定定义位于 [scenarios/demo](../scenarios/demo/catalog/manifest.json)，含初始状态、seed、按时间排序的事件及语义验收属性。除 SC07 小型窗口诊断外均为 60 节点、8 任务、6 编队；期望属性不指定最终选哪个备用节点。

| 场景 | 输入与目标 | 实测支持 |
|---|---|---|
| SC01 | U17 故障，T03 relay 缺口，原位修复 | 完整，100 次 baseline 通过 |
| SC02 | 两个 relay provider 中一个故障，期望 KEEP | **PARTIALLY_SUPPORTED**：P2 KEEP，P4 FAILED_NODE_ASSIGNED / MEMBER_UNAVAILABLE；baseline 拒绝提交 |
| SC03 | U17、U21 故障，共享候选、联合资源占用 | 完整，100 次 baseline 通过；归属唯一，无关任务不变 |
| SC04 | 加禁区，只影响 R05 | 完整，100 次 baseline 通过；只重规划航迹，编队/节点不变 |
| SC05 | T05 TargetMove 至 (850,620) | 完整；旧航迹终点失配后重规划、验证、提交 |
| SC06 | U17 故障 + R05 禁区 | 完整；原位修复 + 航迹工具；真实 Adaptive 5/5 |
| SC07 | TaskAdd 导致局部修复与未来 T06 窗口冲突 | **Adaptive 完整**：真实 FAIL→反馈→扩范围→PASS，3/3；baseline 失败，是有意保留的对照 |
| SC08 | 双故障 + 禁区 + T05 优先级改为 5 | 完整综合闭环；100 次 baseline、5 次 Adaptive 均成功。动态优先级调度仍 NOT_EVALUATED |

SC02 默认不展示，诊断开关可查看。它只能证明“受影响不必在 P2 判为不可行”，不能证明 P4 全局约束仍 PASS。另有无事件初始有效状态的真实 NO_RECONSTRUCTION_REQUIRED 回归测试，避免通过放松全局规则来制造 no-op。

任务事件另外用[原始链路探测](evidence/phase6/task-event-support.json)验证了 TaskAdd、TaskCancel、TaskPriorityChange、TargetMove，均实际走 P2→P3→P4 Validator→Commit。TaskAdd 探测使用预先存在的关联编队/航迹，事件前不含该任务；不能外推为任意新任务自动调度均已支持。Cancel 与纯 PriorityChange 可以产生合法的空重构 Delta；Demo 对当前全局可行状态会直接 no-op。SC07 的 TaskAdd 则实测暴露 baseline 的局限。

## 三模式与应用服务

| 模式 | 高层控制 | 对外标识 |
|---|---|---|
| adaptive_agent | 原 P5.1 模型→细粒度工具→反馈→再决策 | 真实模型名、调用数、pure_agent_success |
| bounded_agent | 原有限预算 Agent；超预算/失败触发既有 fallback | pure_agent_success、fallback_used、fallback_result 分开显示 |
| deterministic_realtime | 原 DeterministicReconstructionEngine | BASELINE、policy_mode、model_calls=0 |

`Frontend → REST Demo Adapter → DemoApplicationService → 原引擎/工具/Validator/事务`。
业务逻辑位于 [service.py](../backend/app/demo/service.py)，路由只做请求转换，前端不自行计算约束、选择候选或规划航迹。

Step Mode 在深拷贝隔离分支中调用原引擎，包括它原有的内部验证/事务；READY 只是候选可查看，现场未变。点击提交才通过原 P4 事务对现场状态再次检查版本、digest 和全局约束，成功 version+1。明确标记 trace_execution_scope=PRIVATE_BRANCH，只有最后 LIVE_TRANSACTION 表示现场发布。自动模式复用同一流程。旧方案拒绝，失败现场不受污染，同一 run 重复提交返回原回执。

这不是逐工具暂停执行的 Agent 调试器：影响计算随注入完成，界面第二步控制显示；运行期间轮询整个 job，完成后展示工具记录。每个会话串行保护，应用线程池最多两个任务。当前为单进程 Demo，运行记录 JSON 持久化，活动会话和提交索引在内存，重启后只允许历史回放。

| REST 接口（前缀 /api/v1/demo） | 用途 |
|---|---|
| GET /scenarios | 场景、支持范围、事件摘要 |
| POST /session | 加载初始状态 |
| POST /event | 按版本注入一个或所有剩余事件并返回 P2 结果 |
| GET /state/{session_id} | 聚合当前状态、评估、全局缺口 |
| POST /reconstruct | 三模式异步执行，返回 202 与 run_id |
| GET /run/{run_id} | RUNNING 或完整 DemoRunResult；支持持久化回放 |
| POST /commit | 对 READY 候选执行现场事务 |
| GET /runs | 最近 50 条结构化运行索引 |

[DemoRunResult](../backend/app/demo/models.py) 包含初始态、事件后 before_state、事件与影响路径、P2 assessment、两类 trace、proposal、validation、现场 commit、after_state、候选态、变化指标、计时和错误。前端 React/Vite/TypeScript/SVG 展示节点位置、成员关系、目标、航迹、禁区、Before/After/Overlay、具体 Delta、限制、时延、Auto/Step、持久化回放。

## 评测口径

[Phase6EvaluationHarness](../backend/app/demo/evaluation.py) 执行 Scenario × Mode × Trials。每格连续全部试验均记入，不筛成功样本；每次 Agent 同时保存相同 before digest 的确定性对照。运行前保存配置、场景副本和后端/场景源码哈希。solver_calls 使用每线程 profiler 统计真实 Formation/Task/Route 求解调用，包含诊断准备及 fallback，计数开销包含在耗时内；未向原算法插入统计逻辑。

| 指标 | 定义与限制 |
|---|---|
| Reconstruction success | 需要重构的试验中，实际提交且 Validator PASS/PASS_WITH_LIMITATIONS 的比例；失败计入分母，合法 no-op 单列 |
| Task recovery | 所有试验恢复的违规任务数 / 初始违规任务总数，不取比例的简单平均 |
| Constraints | 原 Validator 的硬检查总数、失败数、警告数、未评估项；成功仍带五项限制 |
| Scope ratio | 改变的任务/编队/节点/航迹实体数，除以重构前四类实体总数；节点包含派生归属/角色变化，环境输入变化不计重构扰动 |
| Plan disruption | 原八维字典序向量，不加权、不求总分；只比较两边都提交的方案，baseline 失败时为 not_comparable |
| Policy | 模型/工具/求解调用、验证重试、范围扩展、非法动作、dominance 拒绝、fallback、token |
| Latency | 事件/图/影响/评估、选项、模型、工具、验证、提交、fallback 和 total 分列 |

`total_ms = total_phase2_ms + reconstruction_total_ms`，覆盖应用层分支创建、求解和现场发布；不含人工等待、动画、排队、HTTP 返回序列化、最终展示投影和记录落盘。`deterministic_core_ms` 为原引擎的提案→验证→私有提交时间，不含第二次现场发布或 P2。模型项含网络与推理，内部部分 timing 可能嵌套（验证/工具/fallback/commit），**不能把各项简单相加**。本轮是开发机实测，非硬实时或生产容量承诺。

p50 为中位数，p95/p99 使用 nearest-rank；5 个样本的 p95/p99 实际就是最大值，不能据此推断稳定尾分布。bounded 的 5 秒是 Agent 策略预算，fallback、P2 和应用层开销使总时延可超过 5 秒。

## 60 节点确定性性能（每场景 100 次，单位 ms）

| 场景 | 原核心 p50 / p95 / p99 / max | 应用层 total p50 / p95 / p99 / max | 成功 / <5s |
|---|---|---|---|
| SC01 | 127.735 / 141.892 / 145.160 / 151.165 | 295.763 / 326.825 / 331.220 / 332.462 | 100/100 |
| SC03 | 239.540 / 260.151 / 269.608 / 425.176 | 490.865 / 523.765 / 540.326 / 871.714 | 100/100 |
| SC04 | 184.776 / 197.777 / 205.359 / 206.418 | 363.843 / 386.760 / 401.874 / 468.783 | 100/100 |
| SC08 | 386.307 / 399.007 / 490.989 / 494.877 | 744.793 / 768.470 / 886.294 / 1042.469 | 100/100 |

所有正式确定性样本的恢复率和重构成功率均为 100%，model_calls=0；支持探测中 SC02/SC07 的失败另外完整保留，不计入这四个预先指定性能场景的成功样本。

## 本轮真实模型对比（单位 s）

| 场景 / 模式 | n | 提交成功 | 纯 Agent | Fallback | total p50 / p95 / max | model p50 | 调用数 p50 / max |
|---|---:|---:|---:|---:|---|---:|---|
| SC06/adaptive_agent | 5 | 100% | 100% | 0% | 6.431 / 8.444 / 8.444 | 5.474 | 2 / 2 |
| SC08/adaptive_agent | 5 | 100% | 100% | 0% | 9.814 / 11.342 / 11.342 | 7.874 | 3 / 4 |
| SC07/adaptive_agent | 3 | 100% | 100% | 0% | 19.149 / 19.488 / 19.488 | 18.876 | 6 / 7 |
| SC06/bounded_agent | 2 | 100% | 50% | 50% | 5.346 / 5.672 / 5.672 | 4.233 | 2 / 2 |
| SC08/bounded_agent | 2 | 100% | 0% | 100% | 6.003 / 6.017 / 6.017 | 3.059 | 2 / 2 |

使用既有 `qwen3.8-flash` / `qwen3.8-max` 路由，temperature=0、enable_thinking=false；配置与实际每次模型名在 manifest/trace 中。本轮没有为改善测试数字更换核心策略。

| 在线分组 | 输入 token 合计 | 输出 token 合计 | 扰动比较 |
|---|---:|---:|---|
| SC06/adaptive_agent | 22342 | 783 | {"equal": 5} |
| SC08/adaptive_agent | 47970 | 1285 | {"equal": 5} |
| SC07/adaptive_agent | 31999 | 1671 | {"not_comparable": 3} |
| SC06/bounded_agent | 7388 | 246 | {"equal": 2} |
| SC08/bounded_agent | 8657 | 128 | {"equal": 2} |

不依据 token 推算未经核对的费用。全部调用、scope、验证重试、非法动作、dominance 拒绝、constraint counts 和逐项 timing 分布见[Adaptive summary](evidence/phase6/agent-summary.json)、[反馈 summary](evidence/phase6/feedback-summary.json)、[bounded summary](evidence/phase6/bounded-summary.json)。

## Agent 的价值与成本

SC06 的五次模型运行均两轮决策，使用编队修复和航迹两类能力；SC08 为 3–4 轮。与配对 baseline 的最终八维扰动全部相同：这两组说明 Agent 能完成混合任务编排，但**没有展示方案质量提升**，增加了模型时延与 token。固定策略在 SC01/03/04/05/06/08 已足够。

SC07 是本轮清楚展示额外价值的诊断：应用层用真实 P3 局部修复工具生成首份提案，明确记录 seed_origin；Agent 先调用验证，P4 返回 `UNRELATED_TASK_REGRESSION`，涉及 T06 未来窗口成员不可用。模型依据结构化反馈扩展任务范围、获取选项、替换提案，第二轮验证通过。三次均 validation_retries=1，scope_expansions=3–4（见逐次记录）；不是把第一份失败方案伪装成模型自主发现。

其中首个真实 trace 还重复请求了 T06 选项，最终才选择可覆盖窗口的成员，说明反馈恢复虽成功，仍有无效尝试与模型成本。SC07 仅三个小型诊断样本，不能证明普遍优于 baseline；baseline 失败时不强行比较扰动。Route-only 的离线真实工具测试显示不调用 Formation/Task solver；固定策略同样会跳过，因此这不是 Agent 独有收益。未对 SC04 新增在线重复实验，保留 P5.1 的 C 类 route-only 在线 evidence。

## 最终 SC08 真实结果

由 [scripts/demo_phase6.py](../scripts/demo_phase6.py) 通过已启动的真实 REST API 执行。[完整 JSON](evidence/phase6/final-sc08.json)，run_id=`ef8dee7b-f0bd-44c7-96ce-5bc754f99788`，模式 deterministic_realtime。

初始 60 节点、8 ACTIVE 任务、6 编队，version=0、8/8 可行。顺序注入 E001(U17)、E002(U21)、E_AREA(Z_DEMO)、E_PRIORITY(T05→5)，version=4。真实影响路径包括：

```text
node:U17 → formation:F03 → capability:formation:F03:relay → task:T03
node:U21 → formation:F04 → capability:formation:F04:relay → task:T04
environment:Z_DEMO → route:R05 → task:T05
task:T05  (priority input changed)
```

事件后 T03/T04 relay required=1、available=0，T05 航迹与禁区冲突；P4 共报告 7 条违规，涉及 3 任务。baseline 的真实候选/占用/策略 trace 得到 F03 `-U17 +U59`、F04 `-U21 +U47`，重规划 R05 绕开禁区；这些节点编号是求解输出，场景/前端未指定答案。

| 指标 | 重构前（事件后） | 现场提交后 |
|---|---:|---:|
| 可行任务 | 5/8 | 8/8 |
| 能力缺口总量 | 2 | 0 |
| 无效航迹 | 1 | 0 |
| 失效节点 | 2 | 2 |
| 版本 | 4 | 5 |

任务重构 Delta=0（优先级变化属于输入事件），编队变化=2，节点归属/角色变化=4，航迹变化=1；scope=7/82=8.54%。八维扰动 `[0,0,2,4,1,4,484.49034850037026,0]`。

全局 Validator 检查 8 任务、6 编队、8 航迹，137 项硬检查、0 失败、0 警告，结论 **PASS_WITH_LIMITATIONS**。未评估 travel_time、transfer_time、dynamic_energy、formation_geometry、dynamic_scheduling。现场回执 `8040580e-63ee-4f38-b15d-fe92235b39a1`，base_version=4，committed_version=5；候选与现场 digest 均保存。

本次影响分析 2.259 ms、评估 0.398 ms、选项/提案 205.821 ms、原确定性核心 456.135 ms、模型 0 ms、验证 10.265 ms、提交阶段 318.212 ms、应用层闭环总计 **813.615 ms**。两次事务用于 Step/Auto 统一边界，不把私有提交时间隐去。最终状态中 U17/U21 仍 FAILED，恢复的是任务方案。

另存 [SC08 真实 Adaptive](evidence/phase6/agent-sc08.json)、[SC07 真实反馈](evidence/phase6/feedback-sc07.json)、[SC08 真实 bounded](evidence/phase6/bounded-sc08.json)。[操作指南](demo-guide.md)链接四张真实截图与来源元数据；浏览器读取真实 API，未使用假数据。

## 复现命令与证据

在根目录设置 `PYTHONPATH=/tmp/cluster-reconstruction-deps:.`，下列命令各自使用新的输出目录，评测器拒绝覆盖已有 trials。

```bash
export PYTHONPATH=/tmp/cluster-reconstruction-deps:.
python3 -m scripts.benchmark_phase6 --scenarios SC01,SC03,SC04,SC08 --modes deterministic_realtime --trials 100 --output artifacts/phase6/deterministic
python3 -m scripts.benchmark_phase6 --scenarios SC06,SC08 --modes adaptive_agent --trials 5 --credentials-file docs/API --output artifacts/phase6/agent
python3 -m scripts.benchmark_phase6 --scenarios SC07 --modes adaptive_agent --trials 3 --credentials-file docs/API --output artifacts/phase6/feedback
python3 -m scripts.benchmark_phase6 --scenarios SC06,SC08 --modes bounded_agent --trials 2 --credentials-file docs/API --output artifacts/phase6/bounded
python3 -m scripts.benchmark_phase6 --scenarios SC01,SC02,SC03,SC04,SC05,SC06,SC07,SC08 --modes deterministic_realtime --trials 1 --output artifacts/phase6/support
python3 -m scripts.probe_phase6_events
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

`artifacts/phase6/<group>/` 保留 manifest、scenario_definitions、trials.jsonl、summary 和全部 run 原始记录，包括配对 baseline、失败记录及模型 token。docs 保留[证据索引](evidence/phase6/index.json)、各组 manifest/summary、上述代表性原始结果及[完整性核对](evidence/phase6/verification.json)。截图脚本直接复制在线 trial 原始 JSON 到 replay store，校验 API 返回与源文件完全相同，不重新造场景数据。

测试：**282 个后端测试通过**（包含原 P1–P5.1 全套，1 条既有 Starlette/httpx 弃用警告）；前端 2 个渲染/语义测试、TypeScript/生产构建通过；真实浏览器 2 条端到端测试通过，覆盖 Step 候选不污染现场、提交、版本、8/8 恢复、失效保持、零模型、回放和一键 SC08。另实际渲染三条在线回放截图，无 pageerror，API 数据与证据相同。单测中的 Mock 明确仅为离线测试替身，正式在线结果均来自专用 Alibaba API。

## 文件与技术成果对应

| 新增/修改 | 责任 |
|---|---|
| scenarios/demo/、scripts/build_demo_scenarios.py | 固定场景与语义属性 |
| backend/app/demo/{models,service,projection,api}.py | 应用服务、聚合协议、真实展示投影、异步作业与回放 |
| backend/app/demo/{evaluation,instrumentation}.py | 评测与真实求解调用统计 |
| backend/app/main.py、pyproject.toml | 注册 Demo 服务/路由、0.6.0 版本 |
| scripts/benchmark_phase6.py、demo_phase6.py、probe_phase6_events.py、run_demo.sh | 评测、真实 API 演示、事件兼容探测、启动 |
| frontend/ | React/Vite/TS/SVG、单元与浏览器测试、截图工具、依赖锁 |
| backend/tests/test_phase6_demo.py、test_phase6_evaluation.py | 场景、事务/并发边界、指标与持久化验证 |
| README、docs/demo-guide.md、本文、docs/architecture.md、docs/evidence/phase6/ | 使用说明、架构边界、可追溯证据 |

| 技术 | 可现场展示的证据 |
|---|---|
| 技术1：依赖建模、事件影响、约束评估 | P2 影响路径、能力 required/available、P2 与 P4 当前状态 |
| 技术2：最小扰动分层增量重构 | 真实候选/预留、局部成员替换、route-only、八维扰动与具体 Delta |
| 技术3：模型策略、工具、反馈重规划 | SC06/08 多技能选择；SC07 FAIL→feedback→scope expansion→PASS |
| 技术4：场景、注入、评测、集成验证 | 8 场景、400 次性能、在线对照、REST、Web、回放与真实浏览器 |

## 阶段结束判断与未来 MCP

**Q1：可以独立向甲方演示吗？** 可以，启动脚本+浏览器即可，离线 baseline 不依赖模型；展示 SC08 全链路与 SC07 在线/真实回放。应说明 SC02 部分支持和 Validator 的五项物理/调度限制。当前是单机最小 Demo，非生产集群控制系统。

**Q2：60 节点确定性是否稳定 <5s？** 本机四场景各 100 次均成功、全部 <5s；应用层最慢 1.043 秒，SC08 p95 0.768 秒。仅证明此固定场景和测量环境的重复实验，不给未测试规模/硬件做硬实时保证。

**Q3：Adaptive 当前真实时延？** SC06 p50/p95 6.431/8.444 秒；SC08 9.814/11.342 秒；SC07 19.149/19.488 秒。小样本，云模型仍是主要耗时，不承诺 Agent <5s。Bounded 也不能把 5 秒模型预算宣传为总闭环上限。

**Q4：额外价值在哪？** SC07 的独立全局反馈恢复，baseline 拒绝而 Agent 修正成功；SC06/08 展示混合技能编排，但本轮最终扰动与 baseline 相同，额外模型成本无质量收益，不作总排名。

**Q5：正式 MCP 接入需要甲方提供什么？** 下表是下一阶段接口澄清清单，本阶段仅保留应用服务边界，**未实现 MCP Adapter**。

| 需要甲方明确 | 具体字段/决定 |
|---|---|
| 双方角色 | 谁是 MCP Client / Server，谁拥有态势、决策、验证与最终提交权限 |
| Transport | 支持的 MCP 协议版本、stdio 或 HTTP 流式传输、连接生命周期与重连 |
| Tool schema | 工具名、输入/输出 JSON schema、枚举、单位、能力目录与发现规则 |
| 完整/增量态势 | 初始化全量、增量事件、事件序号/去重键、缺口补齐和快照一致性 |
| 输入字段 | 节点 ID/位置/健康/能力/资源/时间窗；任务目标/优先级/需求；编队/角色；航迹；环境约束；坐标与时间基准 |
| 输出字段 | proposal、scope、task/formation/node/route Delta、validation/limitations、trace 摘要、timing、commit receipt |
| Version 语义 | expected_version、base digest、事件幂等、proposal_id、冲突返回、回滚与重试责任 |
| 同步/异步 | deadline、任务句柄、进度/取消、完成通知、断线查询；模型与 fallback 预算 |
| 鉴权 | 身份/租户、工具级权限、凭据传递与保管、提交授权、审计需求 |
| 错误码 | 无模型、超时、限流、无可行方案、验证失败、过期版本、部分能力不支持、是否可重试 |
| 地址与网络 | 内外网部署地址、域名/端口/TLS、代理/防火墙、模型出口、允许访问的服务与部署硬件 |

下一阶段 MCP 可复用 DemoApplicationService 的 load/inject/state/start/commit/get_run 用例，或继续通过原 primitive 供 Policy 编排；事务提交和独立 Validator 必须保留，不能把 deterministic baseline 固化为唯一控制入口。
