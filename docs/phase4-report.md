# Phase 4：全局约束验证与事务化重构引擎

日期：2026-09-24。Phase 4 已完成确定性任务重构闭环，停止在本阶段。
模型驱动的 Agent 编排将在 Phase 5 实现，目前系统仍不依赖任何大模型 API。
没有接入 Agent Harness、RAG 或前端。

架构补充：确定性引擎保留为 baseline / model-API fallback / regression test engine /
Agent Policy 对比基线，不是未来唯一控制入口。新增 `models/primitives.py` 与
`reconstruction/primitives.py`，以薄封装独立暴露十项 Phase 2–4 能力；新增
`backend/tests/test_primitives.py` 证明不经过 baseline 也可局部试探、验证并提交。
没有修改 GlobalConstraintValidator、ProposalMaterializer 或事务/版本/回滚/幂等逻辑，
也没有重写 Phase 3。接口及调用边界见 [独立能力接口](primitive-interfaces.md)。

## 交付文件

| 文件 | 内容 |
|---|---|
| `backend/app/models/phase4.py` | ValidationResult、Coverage、指标、CommitReceipt、ReconstructionResult、请求 schema |
| `backend/app/reconstruction/materializer.py` | 严格解释 typed delta、私有候选状态、before image 与成员/角色一致性 |
| `backend/app/reconstruction/validator.py` | 独立全局硬约束、事实/范围/delta/成本完整性与回归验证 |
| `backend/app/reconstruction/store.py` | SessionRecord，单次发布的状态/历史/幂等/结果单元 |
| `backend/app/reconstruction/engine.py` | 事务化提交与确定性端到端编排；旧无状态输入兼容 |
| `backend/app/models/primitives.py`、`reconstruction/primitives.py` | 十项独立确定性能力的薄封装与类型化结果，不包含高层策略 |
| `backend/app/models/phase3.py`、`reconstruction/cost.py`、`planner.py` | 中性资源成本、结构优先排序、显式 scope 扩展及理由 |
| `backend/app/assessment/facts.py`、`reconstruction/routes.py` | 将点到折线路径距离提取为纯几何事实，Validator 独立重算偏离指标 |
| `backend/app/scenarios/manager.py` | 事件和重构共同的原子发布单元、验证/提交/结果查询入口 |
| `backend/app/models/api.py`、`api/routes.py`、`main.py`、`pyproject.toml` | Phase 4 API、错误状态、版本 0.4.0 |
| `backend/tests/test_phase4.py`、`test_phase4_api.py` | 对抗性验证、闭环、回滚、并发、幂等与 API 测试 |
| `backend/tests/test_primitives.py`、`docs/primitive-interfaces.md` | 绕过 baseline 的能力组合测试、接口契约与未来 Agent 控制边界 |
| `backend/tests/test_api.py`、`test_phase2_api.py`、`test_phase3_api.py`、`test_server_startup.py` | 更新旧 501 边界断言，保留无状态输入语义，真实 TCP 验证提交 |
| `scripts/demo_phase4.py` | 四个真实案例、重新评估、20 次性能样本及 JSON 导出 |
| `scripts/demo_phase1.py` | 保留加载/读取冒烟，不再断言已实现接口返回 501 |
| `README.md`、`docs/architecture.md`、`docs/phase3-report.md` | 当前用法、架构基线 v4、历史报告状态说明 |

没有新增依赖，也未修改场景节点数据来强迫选中固定备用节点。

## 成本修正

现有 domain 有任务资源需求 `resource_required` 与节点剩余资源，但没有此次重构的真实
增量消耗/转场能耗模型。重构不改变任务需求，因此不能把新成员的剩余容量当成代价。
采用允许的中性方案：`resource_cost=0`、`resource_usage_cost=0`，不虚构能耗。
实际资源承诺/池容量仍由独立硬约束报告，不能通过降低代价绕过资源不足。

字典序固定为：

```text
task_reassignment_count
new_formations
formations_changed
formation_membership_changes
route_change_count
node_switch_count
distance_cost
resource_usage_cost (= 0)
```

测试验证同距离时丰富资源候选不受惩罚，以及“保留路线但距离更远”的方案仍优于
相同成员结构却额外改路线的方案。原有搜索策略和预算保留，仍不保证全局最优。

## 三个独立边界

```text
HierarchicalReconstructionPlanner.propose(state, receipts)
  → ReconstructionProposal（局部 FEASIBLE 不是全局合法）

GlobalConstraintValidator.validate_proposal(observed_state, proposal)
  → ValidationResult（独立计算，完全不写状态）

TransactionalReconstructionEngine.commit(session_id, proposal)
  → ReconstructionCommitReceipt（在锁内重新验证后原子发布）
```

Materializer 只解释 delta；不重新求解、不补字段、不修正错误方案。它从深复制的观测态
构造候选；检查 before image、目标 ID、增删节点集合、重复 delta、节点 role/ownership
与 formation delta 一致性，最后运行 ScenarioState 结构验证。

Validator 不调用 Phase 3 局部可行性检查或 Phase 2 Assessment 来决定结果。
复用的计算只涉及状态摘要、能力供给、成员可用性和纯连续几何；编排和业务判定独立。
测试将 Planner 与 Assessment 入口替换为抛异常函数，验证正常 Proposal 仍能独立验证。

| 可变 Planning Variables | 不可变 Observed Facts / 约束输入 |
|---|---|
| Task.formation_id、Task.start（规划参考起点）、Task.route_id | Task.target、需求、最低节点数、资源需求、优先级、窗口、生命周期 |
| Formation.node_ids、roles；新增编队 | 已有编队的 minimum_capabilities |
| Route.waypoints；新增路线 | 全部 Node 字段：status、health、capability、resources、position、availability、risk |
| 成功提交时由引擎递增 version | scenario clock、environment、bounds、capability definitions、seed、场景 ID、事件历史 |

Proposal 类型没有“修改节点事实/删除环境”入口，额外字段在 API 层 422。
Validator 另比较事实投影和实际 delta，防止 Materializer 合约被破坏时出现隐藏修改。
测试覆盖伪造 Materializer 修改状态/目标/时钟/环境，以及隐藏路线变化。

## 全局约束与 Coverage

| 类别 | 已实现检查 |
|---|---|
| preconditions | base_state_version 与 base_state_digest；不匹配立即 FAIL、停止物化 |
| structure | ID/引用/成员/角色/有限坐标/地图边界/路线结构，Pydantic 复核 |
| observed_facts | 只允许规划字段变化；禁止修改需求或环境事实 |
| membership | 全部编队静态唯一归属；ACTIVE 编队无 FAILED 成员；禁止显式新加入失败节点/分派新角色 |
| capability | 全部 ACTIVE 任务有效供给与需求；DEGRADED 按 health 缩放 |
| formation | 任务最低人数、编队最低能力、成员可用窗口 |
| resource | 单任务可用池、同编队全部 ACTIVE 任务累计承诺 |
| time_window | 半开窗口冲突、完整成员可用窗口、deadline > clock |
| route | 全部 ACTIVE 任务路线存在、精确起终点、地图边界、连续线段与闭合受限矩形碰撞 |
| scope | 实际修改在声明范围内；扩展对象有 namespaced scope_expansion_reason |
| metadata | 实际 Delta、成本分量与字典序、策略层级/名称、能力说明、路线长度/偏离/重规划声明、恢复声明 |
| regression | scope 外原本通过的任务不得回归失败，附 UNRELATED_TASK_REGRESSION |

Scope 不能靠自己声明“原本受影响”绕过规则：Validator 从 observed state 独立确定原有
违规/退化任务及其相关编队、节点、路线。新增备用节点/目标编队/新路线需列入 scope 并给
扩展理由。Planner 已输出这些字段。范围扩展并不豁免全局硬约束。

资源是无补给模型的抽象 stock：同一编队不重叠窗口的 ACTIVE 任务也累加承诺。
节点剩余资源只是观测容量，commit 不模拟扣减；没有单节点分摊或物理电量模型。
时间窗口相邻可以复用编队，但转场时间未知。

旧闲置编队可能保留历史 FAILED 成员，且不参与任何 ACTIVE 任务；这是允许的历史计划。
将 FAILED 节点新加入任何编队或分派新 role 会直接失败。
Phase 2 的冗余失效可以给 KEEP；全局 Validator 更严格，不允许 ACTIVE 编队保留失败成员。
当前 Planner 不保证为所有这种“局部 KEEP、全局更严格”的情况自动生成清理方案，可能明确
返回不可提交的 FAIL，而不会放宽 Validator。

ValidationResult 包含：validation_id、proposal_id、base_state_version、candidate_state_digest、
status、hard_failures、warnings、not_evaluated、validated_constraints、per_task_results、
per_formation_results、route_results、global_invariants、coverage、metrics、validation_time_ms。
逐项检查的 code 是固定约束标识，需结合 passed；真正失败集合取 hard_failures。
同一编队共享检查在多个任务下展示，hard_constraint_count 按 category/code/subject 去重。

| 状态 | 语义 |
|---|---|
| PASS | 所有适用硬约束通过，无关键未评估项；例如没有 ACTIVE 任务的合法无变化方案 |
| PASS_WITH_LIMITATIONS | 已实现硬约束全部通过，仍有当前未建模内容；允许在 Demo 中提交 |
| FAIL | 任一已实现硬约束违反；禁止提交 |

有 ACTIVE 任务时，travel_time、transfer_time、dynamic_energy、formation_geometry、
dynamic_scheduling 为 NOT_EVALUATED，SC01 因此不是无条件 PASS。
Coverage 的 VALIDATED 表示该类已执行，不意味着通过；前提/结构短路后的未执行类标 NOT_RUN。
无 ACTIVE 任务时上述物理项为 NOT_APPLICABLE。计数包括验证任务/编队/路线、硬检查/失败、
warning 和 NOT_EVALUATED 数。

## 提交、版本、幂等与回滚

同一 RLock 串行化事件和重构提交（跨会话也串行，适用于本地 Demo）。事务顺序：

1. 深复制/规范化 Proposal 并计算排序键规范化内容 SHA-256。
2. 取得当前 SessionRecord；先查询已提交 proposal_id。相同内容返回原回执 replayed=true；
   内容不同返回 409 PROPOSAL_ID_CONFLICT。历史回执重放不会覆盖后续新事件。
3. 检查当前 version 与精确状态 digest，任何不匹配返回 409 STALE_PROPOSAL。
4. 锁内重新执行 Global Validator，FAIL 返回 422 VALIDATION_FAILED 与详细验证结果。
5. 再次严格物化，核对 candidate digest，将候选 version 设置为 observed.version+1。
6. 完整构建新快照、回执、幂等映射、完整结果及返回副本；最后一次替换会话记录。

事件历史与所有观测事实保留。候选校验、Schema、审计构造或复制异常发生时，旧记录不变。
没有“先写编队，再写路线”的阶段性可见状态。无变化合法 Proposal 显式提交也递增一次版本。
当前为进程内原子性，不含数据库/WAL/跨进程事务；重启清空会话、回执与轨迹，只运行一个 worker。

Receipt 包含 commit_id、reconstruction_id、proposal_id、validation_id、base_version、
committed_version、applied_deltas、validation_status、validation_summary、committed_at（UTC）、
candidate_state_digest、committed_state_digest、replayed、commit_ms。
验证结果摘要对应增版本前候选，committed_state_digest 对应增版本后的新状态。

## API 与 Phase 5 工具接口

| 能力 | HTTP / Python |
|---|---|
| 读取当前观测 | GET /scenario/state；ScenarioManager.get |
| 提案 | POST /reconstruction/propose；ScenarioManager.propose(session_id, expected_version) |
| 只读全局验证 | POST /reconstruction/validate；GlobalConstraintValidator.validate_proposal(state, proposal) |
| 事务提交 | POST /reconstruction/commit；TransactionalReconstructionEngine.commit(session_id, proposal) |
| 确定性后备闭环 | POST /reconstruct；DeterministicReconstructionEngine.reconstruct(session_id, expected_version, commit=True) |
| 读取完整结果/轨迹 | GET /reconstruction/{id}、GET /trace/{id}；ScenarioManager.reconstruction |

路径统一前缀 `/api/v1`。validate/commit 请求是 `{session_id, proposal: 完整对象}`。
客户端先 validate 可用于查看，但 commit 总会重新验证，不依赖旧 validation_id 或客户端状态。
`/reconstruct` 默认 commit=true；commit=false 只生成/验证，返回的 state 仍为观测态。
业务不可行的闭环响应为 200、committed=false 和 Validation FAIL；独立 commit 的拒绝为 422，
版本竞争为 409。原无状态 `{state,event,commit?}` 输入仍接受事件前 state，在私有事务中执行，
返回 session_id=null 和结果状态，不改已有会话。

reconstruction_id 连接 Event → Impact → Assessment → Proposal → Validation → Commit。
GET trace 返回同一结构化完整结果；event_traces 中每个 Phase2Result 携带原事件、影响与评估。
Proposal 始终保留 committed=false / global_validation=NOT_PERFORMED 的原始含义；
真正的提交状态在外层 ReconstructionResult 和 Receipt，不修改原 Proposal 冒充验证凭证。
`/assessment` 仍返回事件当时的历史结果，不是提交后自动重评；演示显式对新状态全量重评。

Phase 5 建议直接给上述接口生成 Pydantic Tool schemas。模型只组织调用、读取失败反馈、
决定重新提案或终止，不获得写状态/降低要求/绕过 Validator 的工具。STALE_PROPOSAL 触发
重新读取与提案；网络重试提交同一完整 Proposal 可获得原回执。

## 真实案例 A–D

运行以下命令可重现并导出完整证据：

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m scripts.demo_phase4 --runs 20 --output artifacts/phase4-evidence.json
```

**A：SC01 U17 单故障。** 初态 v0 → E001 v1；事件评估 T03 RECONSTRUCT。
Proposal 为 F03 移除 U17、加入算法选出的 U59；任务分配和路线不变。
独立全局检查 8 个任务、6 个活动编队、8 条任务路线，PASS_WITH_LIMITATIONS。
提交 v2；重新评估 T03 为 KEEP、capability PASS。U17 仍 FAILED，时钟、所有节点事实、
环境、其他任务/路线及无关编队保持原值。

**B：U17 + U21。** E001/E002 依次 v0→v1→v2；联合 Proposal 为 F03 加 U59、F04 加 U47，
分别移除 U17/U21。两个不同备用节点由搜索选出，没有硬编码选择。
全局 PASS_WITH_LIMITATIONS → 一次原子提交 v3；T03/T04 重评 KEEP，能力恢复。
所有任务归属和原路线保持不变，两个失败事实保留。

**C：恶意 Proposal。** 从单故障的有效候选中重新把 U17 放入 F03，重新声明对应 Delta/成本，
仍声称 FEASIBLE。Validator 返回 FAIL：FAILED_NODE_ASSIGNED、MEMBER_UNAVAILABLE。
commit 返回 422 VALIDATION_FAILED；version、完整状态及摘要均不变，健康替代节点也未部分写入。

**D：旧 Proposal。** 在 v1 生成 P1，提交前注入 E002 到 v2。
commit(P1) 返回 409 STALE_PROPOSAL；v2 与两个失效事实原样保留。
另有测试在两个线程中竞争同一版本，验证只允许一个版本变化成功。

## 测试与性能

完整回归命令：

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

本轮测试覆盖 Phase 1–4、独立对抗性方案、真实 Uvicorn TCP 启动和 API。
架构补充后的完整回归结果：**178 passed, 1 warning in 5.03s**（新增 5 项独立能力测试）。
Starlette TestClient 存在上游 httpx 弃用提示，
不影响通过；运行依赖仍使用已锁定版本。

对抗性测试覆盖失败节点重引入/闲置角色、重复归属、能力/人数/编队最低能力不足、
共享资源超额、任务窗口冲突/相邻窗口、可用窗口/截止时间、连续路线穿禁区及触边、端点错误、
不存在编队、范围外任务变化、无理由扩大范围、无关任务回归、before image/角色 Delta 伪造、
成本/策略/能力说明/路线长度与偏离声明伪造、版本/摘要不匹配，以及事实与隐藏路线变更。
事务测试覆盖“有效编队+无效路线”整笔回滚、审计构造异常、同方案并发提交一次、
提交后新事件上的幂等重放、相同 ID 不同内容、事件与提交竞争、双故障联合提交和只读预览。

计时口径：`proposal_ms` 包含 Phase 3 求解；`validation_ms` 为锁内权威验证；
`commit_ms` 包含该次提交锁等待/物化/结果复制/审计准备，扣除验证耗时；
`deterministic_total_ms` 从当前状态捕获至结果准备完成，含协调开销，排除 HTTP 序列化和模型 API。
单独 commit 没有重新提案，保存结果的 proposal_ms 为 0，原生成时间仍在 proposal.trace.timing。
例程另报两事件 Phase2 之和、Phase3 内部总耗时和 `Phase2之和 + deterministic_total`。
这些重叠口径不能再次相加。性能是当前机器上确定性核心的观察值，不是 Agent 系统 `<5s` 验收。

实测环境：Python 3.10.12，Linux 6.8.0-87-generic x86_64 / glibc 2.35，
当前开发环境，SC01 seed=42、60 节点/8 任务/6 编队。20 次双故障样本，每次独立会话；
不关闭 Python GC，p95 采用 nearest-rank。样本以本轮生成的
`artifacts/phase4-evidence.json` 为准，artifacts 目录不纳入版本管理。

| 双故障计时（ms） | p50 | p95 | max |
|---|---:|---:|---:|
| proposal_ms | 41.488 | 58.294 | 58.320 |
| validation_ms | 3.832 | 4.156 | 20.107 |
| commit_ms（不重复计验证） | 12.759 | 30.278 | 31.947 |
| deterministic_total_ms | 77.439 | 79.496 | 79.567 |
| 两次事件 Phase2 之和 | 13.100 | 13.878 | 28.143 |
| Phase3 内部 total（已含于 proposal_ms） | 41.289 | 58.121 | 58.141 |
| 两事件处理核心 + 确定性重构核心 | 90.382 | 93.299 | 93.348 |

案例 A 首次运行：proposal 11.546 ms、validation 3.332 ms、commit 8.854 ms、
确定性总计 25.672 ms。案例 B：39.698 / 3.840 / 13.082 / 59.956 ms。
两案均验证 8 个 ACTIVE 任务、6 个活动编队、8 条任务路线；独立硬检查分别为 133/135 项，
失败 0、warning 0、未建模关键项 5。这些数值包含实际 scope/metadata 检查，随方案内容变化。

## 当前限制

本阶段只保证实现范围内的硬约束；不评估真实飞行/转场时间、动态能耗、队形几何、动态调度、
软风险代价或真实飞控。搜索有预算和静态唯一归属限制，不保证存在解时一定找到。
重构不会自动降低任务需求、删禁区、复活节点、将失败任务标 ABORTED，或部分提交。
存储为单进程内存，没有持久化回放、跨进程一致性、用户鉴权或前端地图；这些不属于本阶段。

Phase 4 完成的是确定性任务重构闭环。模型驱动的 Agent 编排将在 Phase 5 实现，
目前系统仍不依赖任何大模型 API。
