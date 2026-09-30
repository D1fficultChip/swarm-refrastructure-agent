# Phase 5 完成报告

日期：2026-09-24。完成 Phase 5，停止在该阶段；未进入 Phase 6。

系统已具备 **模型逐步选择 primitive tool → 私有 Proposal → 独立全局验证 → 结构化反馈重规划 →
事务提交**。真实阿里云 `qwen3.8-max` 已完成四个在线案例，均无 fallback。
模型有过更大扰动、重复坏选择以及审计理由与参数不一致；保护边界成功拒绝了非法选择和全局违规。
这次交付证明闭环可用，不证明 Agent 比确定性 baseline 更优。

## 1. 文件与架构变更

| 范围 | 新增/修改文件 | 作用 |
|---|---|---|
| Agent 合同 | `backend/app/agent/models.py`, `config.py`, `security.py`, `__init__.py` | 状态/动作/观察/轨迹/结果、预算、脱敏 |
| 模型 | `agent/providers/base.py`, `mock.py`, `openai_compatible.py`, `schema.py`, `__init__.py` | 可替换 Provider、显式 Mock、真实 HTTP、严格响应 schema |
| 决策上下文 | `agent/context_compiler.py`, `prompt.py` | 确定性摘要、预算截断、系统指令与领域文本边界 |
| 工具与工作区 | `agent/action.py`, `tool_guard.py`, `tool_registry.py`, `observation.py`, `workspace.py` | 类型化工具、权限状态机、复用 P2–P4、私有 Proposal |
| 循环与对比 | `agent/orchestrator.py`, `evaluation.py` | 有界动态控制、反馈/stale/fallback、独立会话比较 |
| API | `backend/app/api/routes.py`, `main.py`, `models/api.py` | Agent POST / run GET、可注入 Provider、phase=5 |
| 配置/依赖 | `config/agent.json`, `.env.example`, `.gitignore`, `pyproject.toml` | Qwen 默认配置、凭据忽略、httpx 成为运行依赖、版本 0.5 |
| 演示 | `scripts/demo_phase5_agent.py`, `phase5_scenarios.py`, `model_env.py`, `run_phase5_server.py` | 四案例、诊断场景、本地凭据启动 |
| 测试 | `backend/tests/test_phase5_agent.py`, `test_phase5_boundaries.py`, `test_phase5_provider.py`, `test_phase5_api.py` | 控制流、保护边界、transport、API |
| 文档/证据 | `docs/agent-architecture.md`, 本报告, `docs/evidence/phase5-real.json`, `phase5-mock.json`, `README.md`, `docs/architecture.md` | 设计、完整审计数据、运行方法 |

P1–P4 的核心算法文件未重写。GlobalConstraintValidator、ProposalMaterializer、版本/摘要保护、
事务 Commit、Rollback 语义和 Idempotency 保持原实现。

`DeterministicReconstructionEngine` 继续作为 baseline / fallback / regression engine。
正常 Agent 工具注册表没有完整 reconstruct()，也没有 Python 自动 L1→L2→L3。
L1/L2/L3 工具各只调用本层 primitive，模型选择下一工具和 option_index。

## 2. 关键接口与设计

完整设计见 [Agent 架构](agent-architecture.md)。

- **ModelProvider**：统一 generate_action 合同，真实 HTTP 和显式 Mock 可替换。模型默认为
  `qwen3.8-max`，temperature=0，enable_thinking=false，云端严格 JSON Schema + 本地严格参数校验。
  `MODEL_NAME` 可切换；当前账号已实际完成模型列表与生成请求验证。选型依据
  [阿里云模型文档](https://help.aliyun.com/zh/model-studio/text-generation-model)。
- **Context Compiler**：当前缺口、任务/编队/候选摘要、共享关系、最近 observation、验证反馈、版本与预算。
  FAIL 优先，其次优先级和截止时间；同输入得到同摘要。14,000 字符限额，显式截断，不灌入整个世界。
- **AgentState / Workspace**：本次策略状态与 ScenarioState 分离。working 只在私有副本上改动；
  规范化 delta/cost/proposal 沿用 P3/P4。每次改动换 proposal_id、清除旧 PASS。
- **AgentAction / Tool Guard**：严格 action_type/tool_name/arguments/targets/reason/evidence；
  unknown tool、额外字段、错误类型、虚构实体、范围外修改和假 PASS 均拒绝。
  只有真实 PASS + proposal ID + 内容 hash 匹配才能请求 commit。
- **Observation / Loop**：每轮模型调用后执行一个动作，再把真实返回编入下轮。允许直接 L2/L3、
  route-only 路径；失败没有自动升级。非法动作有结构化修正机会，但受连续错误和总预算限制。
- **Validator Feedback / Scope Expansion**：全局失败的 codes/subjects/details 回到 context。
  `request_scope_expansion` 显式记录 entity/reason/source_validation_id；可 discard 后重新选择。
- **Stale**：新事件使旧 Proposal 失效；Guard 只允许 refresh。刷新废弃旧方案/PASS，重建当前 scope，
  由模型重新决策，不能重放过期 delta 到新状态。
- **Fallback / Termination**：模型错误、连续坏输出、步骤/调用预算或致命工具错误可配置 fallback，
  默认关闭；总时间已耗尽不再启动 fallback。正常模型 USE_FALLBACK 动作被拒绝。
  成功、无须重构、策略无解、各预算终止、模型/工具错误、stale 上限、fallback 成败均有独立终止码。
- **Trace**：每步上下文 hash/大小、模型名/延迟/usage、action、工具参数、真实观察、完整验证反馈、
  版本/proposal ID；不保存完整 prompt、HTTP headers、密钥或隐藏推理。

原 P4 Proposal 的 `strategy_name` 遗留标签保留以满足现有严格合同；控制者以外层
`trace.policy_mode` 和 `AGENT_SELECTED_PRIMITIVE_OPERATIONS` 为准。没有把 Agent 结果伪装成基线调用。

## 3. 四个真实在线案例

以下来自一次四案例在线运行，完整结果保存在 [phase5-real.json](evidence/phase5-real.json)。
每个案例用独立会话，与另一个相同初始 digest 的 baseline 会话比较。真实运行没有为了挑最好结果而重试案例。

| 案例 | 版本 | 动作/模型调用 | Solver 调用 | 验证次数 | 结果 |
|---|---|---:|---:|---:|---|
| A：SC01 U17 | 1 → 2 | 5 | 1 | 1 | SUCCESS / PASS_WITH_LIMITATIONS |
| B：SC01 U17 + U21 | 2 → 3 | 5 | 3 | 1 | SUCCESS / PASS_WITH_LIMITATIONS |
| C：纯航迹禁区冲突 | 0 → 1 | 3 | 1 | 1 | SUCCESS / PASS_WITH_LIMITATIONS |
| D：Validator 反馈重规划 | 0 → 1 | 14 | 3 | 3 | SUCCESS / PASS_WITH_LIMITATIONS |

### A：简单原位修复

实际模型动作：

```text
inspect_task_state(T03)
generate_candidates(T03)
try_in_place_repair(T03, option_index=0)
validate_proposal → PASS_WITH_LIMITATIONS
commit_validated_proposal → SUCCESS
```

F03 移出失效 U17、加入 U59；任务归属和航迹不变。观测节点 U17 仍 FAILED，未伪造恢复事实。
与 baseline 的扰动向量相同，baseline 明显更快。

### B：双故障及真实策略差异

```text
try_in_place_repair(T03, 0)
try_formation_reconstruction(T04, 0)
replan_route(T04)
validate_proposal → PASS_WITH_LIMITATIONS
commit_validated_proposal → SUCCESS
```

T03 用 U59 原位修复；T04 改派至 `F_NEW_T04`，新成员 U39/U40/U46/U47，并重规划航迹。
没有冲突占用同一备用节点；所有硬约束通过。但模型跳到 L3，产生一个新编队、一次任务改派、一次航迹变更；
确定性基线能做两次 L1，扰动更小。此案例验证模型可自主选择路径，同时暴露其最小扰动决策仍有不足。

### C：只调用相关航迹工具

```text
replan_route(T0)
validate_proposal → PASS_WITH_LIMITATIONS
commit_validated_proposal → SUCCESS
```

模型根据当前 route violation 直接规划，连额外 detect 查询也没有调用。
candidate / repair / task reassignment / formation reconstruction 调用次数均为 0。
工具内部先用真实几何检测 route impact，再调用原 A*；模型不提供任意航路点。

### D：真实全局失败 → 扩范围 → 更换方案

输入是明确标注来源的**诊断初始 Proposal**：由原 L1 primitive 产生局部可行方案，
把 U1 加到 T03/T06 共享的 F0。U1 窗口 [0,100] 可以服务 T03，但不能覆盖 T06 的 [200,300]。
P2 仍可由原 U0 满足 T06 能力；P4 要求编队每个成员均覆盖任务窗口，因此真实全局校验拒绝。
没有 mock Validator，也没有宣称这个初始方案是模型生成的。seed 构造不计入 Agent 步数/计时。

实际轨迹：

```text
 1 validate_proposal → FAIL: MEMBER_UNAVAILABLE, UNRELATED_TASK_REGRESSION
 2 inspect_task_state(T06)
 3 request_scope_expansion(task:T06, reason, source_validation_id)
 4 discard_working_proposal
 5 get_current_state_summary
 6 generate_candidates(T03)
 7 try_in_place_repair(T03, 0) → 仍选 U1
 8 inspect_task_state(T06)
 9 try_in_place_repair(T03, 1) → ACTION_REJECTED: OPTION_INDEX_OUT_OF_RANGE
10 validate_proposal → FAIL: MEMBER_UNAVAILABLE
11 discard_working_proposal
12 try_in_place_repair(T03, 1) → 选 U2，窗口 [0,400]
13 validate_proposal → PASS_WITH_LIMITATIONS
14 commit_validated_proposal → SUCCESS
```

第 7 步模型理由说选 U2，实际参数却是 option 0；系统按参数运行并相信工具事实，不能拿自然语言理由当修复成功。
第 9 步在已经应用 U1 的 working 状态中直接改 index，被拒绝；模型后来先 discard 再改选才成功。
最终 F0 增加 U2，T03/T06 均通过。第一次失败的 validation_id 被记录在 scope 扩展中。

离线版本严格验证“第一方案 FAIL → 第二方案 PASS”，真实模型这次多走一轮，报告保留这个差异。
另有真实资源回归测试：外部 partial Proposal 借用 T06 节点修复 T03，触发
`SHARED_RESOURCE_OVERCOMMITTED / RESOURCE_SHORTAGE / UNRELATED_TASK_REGRESSION`；
模型脚本读取实际反馈、扩展 T06 scope、discard 借用方案、生成候选并改用 U3，第二次校验通过。
P3 正常候选器不会借走正在被占用的节点，所以这个资源场景明确采用外部 Proposal 输入审查，未削弱工具规则。

## 4. 性能与初步 baseline 比较

以下是各一次观测，不是 p50/p95 或统计结论。单位 ms，Agent 总时间包含模型网络/推理和所有循环开销。
baseline 是纯确定性核心，未含模型或 HTTP；两者时间边界不同但明确记录。

| 案例 | Context | Model | Tools¹ | Validation² | Commit³ | Agent total | Baseline total |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 12.70 | 21717.46 | 18.42 | 10.22 | 11.52 | 21782.78 | 42.38 |
| B | 14.58 | 18501.47 | 74.69 | 9.63 | 32.40 | 18647.34 | 80.80 |
| C | 1.37 | 10275.05 | 2.43 | 2.03 | 2.79 | 10287.64 | 4.88 |
| D | 11.27 | 62316.61 | 16.86 | 7.94 | 3.65 | 62364.96 | 3.94 |

¹ Tools 不重复累计独立 validate/commit；包含 options 失败调用。² 独立验证。
³ Commit 包含事务内强制再验证。各项之和与 total 间还有初始化、动作校验、trace 构建等小量开销。

| 案例 | Agent / baseline 恢复率 | 验证成功 | Agent / baseline Solver 次数 | Agent 验证重试 | Agent / baseline 航迹变更 |
|---|---|---|---|---:|---|
| A | 1 / 1 | 都成功 | 1 / 1 | 0 | 0 / 0 |
| B | 1 / 1 | 都成功 | 3 / 5 | 0 | 1 / 0 |
| C | 1 / 1 | 都成功 | 1 / 1 | 0 | 1 / 1 |
| D | 1 / 0 | Agent 成功；baseline 被 Validator 拒绝 | 3 / 1 | 2 | 0 / 0 |

恢复率根据真实已提交状态中最初违规任务的恢复数计算；未提交的 baseline 坏方案不计恢复。
baseline D 停止在首次全局 FAIL，未实现反馈重规划，状态保持原样；这正是本次诊断要覆盖的控制能力差异。
baseline 的工具表调用数是 0，因为它直接调用 Solver；不能据此说其无成本。
baseline B 的 5 次 Solver 调用来自原有联合搜索，Agent 较少调用却选择了更大扰动，不能只看调用数排名。

扰动按原 lexicographic 向量：
`[任务改派, 新编队, 变化编队, 成员增删, 航迹变化, 节点归属切换, 距离, 中性资源成本]`。

| 案例 | Agent | Baseline |
|---|---|---|
| A | [0,0,1,2,0,2,268.17,0] | 相同 |
| B | [1,1,2,6,1,6,1058.53,0] | [0,0,2,4,0,4,484.49,0] |
| C | [0,0,0,0,1,0,0,0] | 相同 |
| D | [0,0,1,1,0,1,50,0] | 未提交方案 [0,0,1,1,0,1,1,0]，不可作为更优可行方案 |

在线 context 最大序列化长度 13,991 字符，未超过 14,000 上限。
Provider 回传累计 total_tokens：A 28,537；B 40,136；C 11,442；D 75,801。
这些包含每轮重复传入的系统/schema/context，不是输出 token 数或费用估算。

## 5. 测试结果

**227 passed**：原 Phase 1–4 的 178 项继续通过，新增 Phase 5 共 49 项。
保留一条依赖库 Starlette/httpx 的弃用告警，不影响测试结果。

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

覆盖内容：

- A 原位修复；双故障候选互斥；L1 失败后由下一次模型动作转 L2；直接 L2；直接 L3。
- Route-only 用禁止其他 Solver 的测试桩确认零候选/编队调用。
- 实际 Validator 的窗口回归与共享资源回归；反馈回到下一轮模型 context；显式扩 scope；第二方案通过。
- PASS 后插入真实新事件，commit stale，模型 refresh/discard，再修复累计缺口；stale restart 上限。
- 错 JSON、未知工具、无效实体、错类型、额外参数、范围外修改、未验证 commit、内容 hash 篡改。
- 模型 timeout/缺 key/401/429/5xx/坏响应、HTTP 重试预算、非法输出重试、时间/步骤/调用/验证预算。
- 超时/失败/拒绝不发布私有 delta；fallback 成败单独标注；正常模型不可请求 fallback。
- 无须重构零 Solver；伪造“已修复”“已通过”“已提交”声明不改变事实；无 receipt 不能 SUCCESS。
- Context 可重复、字符限额和脱敏；真实 transport 请求契约、隐藏推理丢弃；API 保存/查询 trace、404/409/422。
- 默认 Uvicorn 协议的真实 TCP 启动等原有回归测试继续通过。

[离线四案例证据](evidence/phase5-mock.json) 使用显式 Mock，和 [真实模型证据](evidence/phase5-real.json) 分开保存。
未设置凭据运行真实脚本会明确 `SKIPPED_REAL_MODEL`；本次已使用用户授权的本地专属凭据完成在线验证。

## 6. 当前限制与 Phase 6 建议

当前仍只验证已建模硬约束，典型结果是 PASS_WITH_LIMITATIONS：travel_time、transfer_time、
dynamic_energy、formation_geometry、dynamic_scheduling 未评估。Agent 不能用自然语言补齐这些物理模型。

模型低温度/严格 schema 也不保证策略最优或完全可复现；B 的额外 L3 和 D 的重复坏选择已说明这一点。
候选/选项依赖当前 working 状态，选择替换需 discard；primitive 不支持任意成员拆借或任意状态编辑。
预算为合作式、有界搜索；模型网络耗时占主导，不承诺毫秒级 Agent 端到端响应。

状态、幂等记录和 Agent run 都在单进程内存，重启后消失；只用一个 worker。
同步 API 完成后返回整条 trace，暂无流式执行进度、取消接口、持久化、前端、MCP、RAG 或多 Agent。
真实演示使用滚动模型别名，不将一次成功率当作稳定性统计。诊断 D 的 seed 与 baseline 起点区别已明示，
不把这个专门覆盖反馈能力的案例当成公平、完整的性能排名。

Phase 6 建议固定模型快照和完整配置，构造混合异常、候选争用、共享资源、route/formation 联动等场景，
记录多次运行成功率、方差、最小扰动差距、调用成本和预算耗尽分布；同时比较有/无结构化反馈与 context 预算。
也应检验更清晰的 option 选择接口能否减少 D 中的重复坏选择。**这些仅是建议，本轮没有进入 Phase 6。**

系统现在可以支持以下技术链：状态理解 → 确定性上下文 → 模型策略 → 类型化工具 → 工具事实 →
全局约束反馈 → 模型修正 → 验证后的事务提交。
