# Phase 2 完成报告

范围：EventInjector + Task Reconstruction Dependency Graph + Impact Propagation + Task Assessment。
本阶段没有任务分配 Solver、编队重组、航迹重规划、LLM、前端或完整重构引擎。

## 1. Phase 1 检查与兼容性

已阅读架构、全部领域/事件/API 模型、SC01 JSON、所有 Phase 1 测试与实际启动回归测试。
SC01 真实关系为 **U17 → F03 → T03**、**U21 → F04 → T04**，不使用提示词中的 F02 示例关系。
保持 Phase 1 的数据模型、SC01 文件、场景加载/查询接口及 InjectEventRequest 字段不变。

必要的阶段升级：

1. `/event/inject` 从 501 升级为执行接口。原 Phase 1 测试中的该条 501 断言移除，
   由新测试验证成功、错误、幂等及不部分修改；其余未实现接口的 501 断言保留。
2. health.implemented_phase 变为 2；重构能力仍为 false，应用版本变为 0.2.0。
3. 初版架构拟限制 pending 时再次注入，本阶段按顺序事件要求改为保存逐事件版本回执。
   后续引擎必须合并未处理事件的影响，不能忽略此前仍未恢复的任务。
4. 引入 Python 3.10 兼容的 NetworkX 3.4.2，保留已修复的 WebSocket 依赖。

## 2. 新增与修改文件

| 文件 | 内容 |
|---|---|
| `backend/app/models/phase2.py` | 事件回执、图实体/边、影响结果、五类检查、决策、trace 和计时 schema |
| `backend/app/events/injector.py` | 8 类 handler、原子纯函数 apply_event、类型化错误 |
| `backend/app/graph/dependency_graph.py` | NetworkX 唯一封装点，建图、查询、统计、可序列化子图 |
| `backend/app/graph/impact_propagation.py` | before/after delta 与关系感知有限传播链 |
| `backend/app/assessment/facts.py` | 有效能力、任务窗口可用性、连续线段与闭矩形相交 |
| `backend/app/assessment/engine.py` | 只评估受影响任务；显式 full_check 调试模式 |
| `backend/app/assessment/service.py` | Phase 2 流程编排、trace、阶段计时 |
| 上述新包的 `__init__.py` | 包边界 |
| `backend/app/scenarios/manager.py` | 会话锁、版本比较、事件幂等、逐事件历史回执 |
| `backend/app/models/api.py` | 阶段标识、AssessmentRequest |
| `backend/app/api/routes.py`、`main.py` | 注入/评估路由和明确错误映射 |
| `backend/tests/test_phase2_events.py` | 状态变换、错误原子性、顺序、幂等、并发版本冲突 |
| `backend/tests/test_phase2_analysis.py` | 初态、局部性/完整性、能力冗余、图路径、五类约束、几何边界 |
| `backend/tests/test_phase2_api.py` | Load/Inject/Assessment、会话隔离、HTTP 错误与幂等 |
| `backend/tests/test_api.py` | 更新已实现注入接口的阶段预期 |
| `backend/tests/test_server_startup.py` | 真实 Uvicorn TCP 测试加入注入/评估 |
| `scripts/demo_phase2.py` | SC01 顺序事件、两节点冗余案例、JSON 证据和计时采样 |
| `pyproject.toml`、`requirements-dev.lock` | 依赖与版本 |
| `README.md`、`docs/architecture.md`、本报告 | 运行方法、设计与验收 |

运行产物 `artifacts/phase2-evidence.json` 包含真实结构化案例与逐次计时样本，由示例生成，
不作为手写测试期望值；运行产物目录被 .gitignore 排除。

## 3. 事件执行与会话语义

`apply_event(state,event) -> EventApplicationResult` 不修改输入；在深复制状态上调用
handler，重新校验完整状态后返回新快照。version 恰好 +1，clock 更新至事件时间。
变化记录同时保存 namespaced entity、变更字段、before/after 实体值和版本/时间变化。

| 事件 | 最小事实变换 | 拒绝条件示例 |
|---|---|---|
| NodeFailure | status=FAILED | 节点不存在、已 FAILED |
| NodeDegradation | status=DEGRADED、降低 health | 已 FAILED、health 没有降低 |
| TaskAdd | 增加完整 ACTIVE 任务，可无分配 | ID 已存在、非法引用、越界 |
| TaskCancel | status=CANCELLED，保留分配作审计 | 非 ACTIVE |
| TaskPriorityChange | 更新 priority | 非 ACTIVE、优先级未改变 |
| RestrictedAreaAdd | 增加受限矩形 | ID 已存在、越界 |
| RestrictedAreaRemove | 移除受限矩形 | 不存在、不是受限区 |
| TargetMove | 仅改 task.target | 非 ACTIVE、坐标未变、越界 |

NodeFailure 不修改 Task.status，不移除编队成员，不改路线。失败没有部分写入。
目标不存在 404；版本、时序、ID 内容或状态转换冲突 409；事件造成非法状态 422。
请求 schema 非法仍由 FastAPI 返回标准 422 校验错误。

同一 session 内锁覆盖版本校验、事件应用、图/评估、结果保存，避免并发丢更新。
同 event_id 同规范化内容重试返回原回执并置 replayed=true，先于版本检查处理；
回执为历史版本，绝不把会话回滚。新 ID 对已 FAILED 节点再次失败明确拒绝。
相同时间按取得会话锁的顺序执行；晚到的更早时间拒绝，不提供重排缓冲。

`POST /assessment` 读取指定事件或最新已保存结果；没有事件时 404。
全量初态检查使用 Python API 的 `full_check=True`，不是每次注入的隐藏步骤。

## 4. TRDG 实体与边

实体命名：task:T03、formation:F03、node:U17、route:R03、environment:Z01。
能力区分全局类别 capability:relay、节点供给 capability:node:U17:relay、
编队供给 capability:formation:F03:relay。所有供给实例仍属于 Capability 实体类型。

| relation | 存储方向 | amount/其他属性 | 传播用途 |
|---|---|---|---|
| TASK_ASSIGNED_TO_FORMATION | task → formation | 无 | 反向找依赖任务；添加/取消时定位共享资源 |
| FORMATION_CONTAINS_NODE | formation → node | role | 反向找受影响编队 |
| NODE_PROVIDES_CAPABILITY | node → scoped capability | 有效供给量 | 标记改变的节点供给 |
| FORMATION_PROVIDES_CAPABILITY | formation → scoped capability | 聚合供给量 | 编队内改变的能力 |
| CAPABILITY_CONTRIBUTES_TO | node capability → formation capability | 成员供给量 | 显式供给证据边 |
| CAPABILITY_HAS_TYPE | scoped capability → category | 无 | 分类边，禁止用于影响扩散 |
| TASK_REQUIRES_CAPABILITY | task → formation scoped capability | 需求量 | 反向找能力需求任务 |
| TASK_USES_ROUTE | task → route | 无 | 环境反向找任务、目标变化正向找路线 |
| ROUTE_INTERSECTS_CONSTRAINT | route → environment | 无 | 新/旧环境变化的几何依赖 |

未分配任务的需求指向类别标签，等待后续分配。图的 amount 根据状态与 health 计算，
具体任务窗口的供给由评估器重新计算，不把图缓存当作约束结论。

封装提供 from_state、query_node、query_neighbors、query_relations、
find_affected_subgraph、export_snapshot、stats；查询返回独立 Pydantic 值。
NetworkX 不暴露给业务模块。before/after 全量建图是当前明确选择，60 节点不需要
复杂图维护；传播后的受影响子图和任务评估保持局部。
内部使用带类型属性的有向多重图，参照 [NetworkX MultiDiGraph 文档](https://networkx.org/documentation/stable/reference/classes/multidigraph.html)。

## 5. 传播算法与边界

传播不是无条件 BFS，不使用任意边权概率：

1. 对照 before/after 确认节点状态/有效能力或任务字段改变。
2. 节点事件沿反向成员边找唯一编队，沿反向分配边找依赖任务；计数、资源和编队
   最低能力也可能变化，所以不能只传播任务明确要求的能力。
3. 改变的能力记录 node → formation → scoped capability → task 的证据路径。
4. 节点事件在任务停止；不把同编队其他成员标为 affected，不自动把任务路线标为 affected，
   不通过类别标签跳到其他编队。
5. 环境添加查询 after 图，移除查询 before 图；连续线段相交才触及路线及依赖任务。
   删除实体及其旧边保留在影响证据子图中，但不重新放回 after state。
6. TaskAdd/Cancel 只触及本任务及共享编队资源的活动任务；不继续扩散至它们的路线。
   TargetMove 触及本任务和独占路线；PriorityChange 只触及本任务。
7. clock 跨越某任务截止时间时，该任务也必须评估。这是时间事实导致的真实影响，
   即使原事件只是备用节点失效，也不能强行隐瞒截止时间影响以追求局部指标。

每条记录包含实体、级别、机器原因、说明、完整路径、关系/方向列表、语义深度。
关系规则限定可走的链；另用重复路径检测、禁止回环、最大语义深度 4 防御。
没有任何规则可通过达到深度上限而任意截掉所需链，当前链最长为 3 条边。

CRITICAL 表示直接事实变化/跨越截止时间；AFFECTED 表示有明确检查依赖；WEAK 为预留
的潜在影响等级，目前不虚构弱影响。等级不是 FAIL，也不在 Assessment 后偷偷改写。

## 6. 五类约束与决策

| 检查 | 实际计算 | 典型 reason code |
|---|---|---|
| capability | 非 FAILED 且窗口覆盖的成员，NORMAL 按 nominal、DEGRADED 按 nominal×health；逐项 required/available/gap | CAPABILITY_SHORTAGE |
| resource | 本任务可用成员资源总和；同编队所有 ACTIVE 任务承诺与成员资源池比较 | RESOURCE_SHORTAGE、SHARED_RESOURCE_OVERCOMMITTED |
| formation | 是否分配、可用成员数、编队 minimum_capabilities | TASK_UNASSIGNED、INSUFFICIENT_ACTIVE_NODES、FORMATION_CAPABILITY_SHORTAGE |
| route | 是否存在、起终点匹配、至少两点、边界范围、连续线段与已知禁区相交 | ROUTE_MISSING、ROUTE_ENDPOINT_MISMATCH、ROUTE_RESTRICTED_AREA_CONFLICT |
| priority_timing | 当前时间未越过固定截止时间；同编队任务窗口不冲突 | TASK_WINDOW_EXPIRED、ASSIGNMENT_TIME_CONFLICT |

剩余可用成员仍处于 DEGRADED、但硬约束满足时，formation 为 DEGRADED，产生
FORMATION_DEGRADED。失效的冗余成员被排除后仍满足数量/能力/资源时，检查保持 PASS，
entity_states 仍真实报告 FAILED/MEMBERS_UNAVAILABLE，不把任务可行性与实体健康混淆。

固定窗口可检查，转场速度/到达时间/优先级调度规则尚无数据与策略，因此 timing.details
中的 travel_time、priority_policy 明确 NOT_EVALUATED；所有结果 evaluation_complete=false。
顶层 priority_timing=PASS 仅表示实际检查的窗口条件满足，不表示未实现子项通过。
取消/已有 ABORTED 任务的五项检查均 NOT_APPLICABLE，不混用 PASS。

- KEEP：活动任务的已实现硬约束全部满足；或已取消任务不再需要执行。
- ADJUST：没有 FAIL，但存在实际 DEGRADED。
- RECONSTRUCT：至少一项 FAIL；只表示需要后续求解，不承诺可恢复或不可恢复。
- ABORT：只反映输入已经是 ABORTED 的生命周期。本阶段不新作出不可恢复判断。

TaskAssessmentEngine 不导入 NetworkX。生产入口只遍历 impact.affected_tasks；建立全局
实体索引和读取共享资源任务不是全量重评估。测试用 full_check 显式检查初态及比较影响完整性。

## 7. 实际 Example A：SC01 U17 与顺序 U21

初态 T01–T08 均 KEEP。E001 为 NodeFailure(U17)，occurred_at=10。

```text
Event E001
  → version 0 → 1；clock 0 → 10
  → node:U17.status NORMAL → FAILED（任务、成员列表、路线不变）
  → node:U17
      --FORMATION_CONTAINS_NODE / REVERSE--> formation:F03
      --FORMATION_PROVIDES_CAPABILITY / FORWARD--> capability:formation:F03:relay
      --TASK_REQUIRES_CAPABILITY / REVERSE--> task:T03
  → affected tasks=[T03], formations=[F03], nodes=[U17], routes=[]
  → relay required=1, available 1 → 0, gap 0 → 1
  → CAPABILITY_SHORTAGE
  → T03 RECONSTRUCT
```

完整 affected_entities：node:U17、formation:F03、capability:node:U17:relay、
capability:formation:F03:relay、task:T03。两条任务传播记录分别说明成员/资源关联和能力关联。

| T03 约束 | 事件前 | 事件后 | 证据 |
|---|---|---|---|
| capability | PASS | FAIL | sensor=3≥2、navigation=2≥1，但 relay=0<1 |
| resource | PASS | PASS | 可用资源 600→500，需求 20 |
| formation | PASS | PASS | 可用成员 6→5，最低 4；navigation 仍满足最低 1 |
| route | PASS | PASS | R03 未变，无禁区 |
| priority_timing | PASS | PASS | clock=10<300，无同编队重叠任务；未实现子项单独标识 |

E002 在最新状态上执行 NodeFailure(U21)，occurred_at=11：version 1→2，
`node:U21 → formation:F04 → capability:formation:F04:relay → task:T04`。
仅 T04 进入第二次评估，中继 available=0、gap=1、RECONSTRUCT，其他四类检查 PASS。
U17 仍为 FAILED，T03 先前缺口仍存在；局部评估并没有隐式修复或遗忘历史记录。

## 8. 实际 Example B：受影响但仍可行

两节点场景 REDUNDANCY：T1 要求 relay=1、min_nodes=1、资源需求 5；F1 包含 U1/U2，
两者各提供 relay=1、资源各为 10，编队最低 relay=1。路线 R1 无禁区且端点正确。

```text
NodeFailure(U1)
  → U1.status NORMAL → FAILED；version 0 → 1
  → node:U1 → formation:F1 → capability:formation:F1:relay → task:T1
  → affected_tasks=[T1]
  → relay available 2 → 1，required=1，gap=0
  → 可用成员 2 → 1，最低 1；资源 20 → 10，需求 5
  → capability/resource/formation/route/priority_timing 均 PASS
  → T1 KEEP，reconstruction_required=false
```

该例的路径、数值与决定来自真实 Pydantic 输出；不是按事件类型拼接决策。
全局 Validator 尚未实现，所以 KEEP 不等于包含转场、真实能耗和失效成员清理的最终合法性证明。

## 9. 测试与运行证据

命令：

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m scripts.demo_phase2 --runs 30 --output artifacts/phase2-evidence.json
```

核心覆盖：SC01 初态、U17/U21 依真实场景关系推导期望、顺序版本、目标缺失、重复失效、
同 ID 重试与内容冲突、并发旧版本、跨会话隔离、8 类 handler、能力冗余、成员计数、
资源与窗口、能力降级、目标移动、添加/取消任务共享资源、禁区添加/移除、线段穿越与
边界接触、截止时间跨越、图证据路径有效性、Pydantic 输出 round-trip。

逐个模拟 SC01 的 60 个节点失效，动态从场景成员/分配关系生成受影响任务期望，
再用显式全量检查比较约束状态变化，验证局部性与完整性。另用评估调用记录证明
正式流程只调用受影响任务评估。真实 Uvicorn 子进程通过本地 TCP 完成加载、注入、评估。

最终结果：**93 passed, 1 warning in 2.70s**，其中保留的 Phase 1 测试用例 49 个、
新增 Phase 2 领域/图/评估测试 35 个、API 测试 9 个。原 Phase 1 最小示例继续通过，
Phase 2 三个实际示例成功生成 JSON。上游 Starlette TestClient 弃用提示仍然明确保留。
测试时间是测试运行耗时，不是重构延迟。

## 10. 性能口径

`perf_counter` 记录 event_application_ms、graph_build_ms、impact_analysis_ms、assessment_ms、
total_phase2_ms。graph_build 包含 before/after 两次建图，total 包含 trace/结果构建；
不含 HTTP 序列化、会话锁等待、持久化磁盘写入、模型或后续 Solver。

示例保存首轮 U17、顺序 U21 和冗余案例各自计时，随后对 60 节点 U17 初态事件重复 30 次；
p95 使用 nearest-rank，原始样本和 Python/平台信息在 JSON 中。是开发机观察值，
没有作为测试硬阈值，不证明完整系统 `<5 s`。

本轮实测环境：Intel Core i9-12900HX、x86_64、Linux 6.8.0、Python 3.10.12；
seed=42，30 次热运行采样，开发机负载未隔离。SC01 图为 163 个图实体、268 条边，
图实体含能力供给实例，并非 163 个物理节点。

| 阶段（ms） | p50 | p95 | max |
|---|---:|---:|---:|
| event_application_ms | 2.043 | 14.587 | 15.996 |
| graph_build_ms | 3.188 | 3.864 | 3.968 |
| impact_analysis_ms | 0.768 | 0.886 | 0.901 |
| assessment_ms | 0.106 | 0.124 | 0.139 |
| total_phase2_ms | 6.373 | 18.259 | 20.293 |

单次示例：首个 U17 14.203 ms、顺序 U21 5.954 ms、两节点冗余 1.434 ms。
各阶段分位数来自不同样本位置，不能将 p95 列直接相加作为总 p95。
完整原始记录见 [本轮 JSON 证据](../artifacts/phase2-evidence.json)；产物可能被清理，
可用上述命令重新生成，重新运行的耗时数值会变化。

## 11. 当前边界与问题

1. 未实现任何方案修复；所有 task/formation/route 都保持原方案，仅事件事实更新。
2. NOT_EVALUATED 项明确存在：转场耗时、优先级调度；软风险阈值、编队几何也未建模。
3. 资源为抽象共享池，没有单节点分摊或消耗仿真；ACTIVE 未完成任务保守计入承诺。
4. Phase 2 的 KEEP 表示已实现约束下可行，不能替代 Phase 4 全局 Validator。
5. reconstruction_required 只对应本事件影响集；连续事件的累计待恢复集合留给后续引擎。
6. 同一进程内存存储，锁串行化，无持久化、TTL、多 worker 或原子 batch 事件。
7. 图全量重建，不是复杂增量缓存；影响传播和任务评估是真正局部的。
8. 当前仍有 Starlette TestClient 对 httpx 的上游弃用提示，不影响实际服务器启动。

## 12. Phase 3 建议接口（仅设计，未实现）

```text
CandidateGenerator.generate(state, task_assessment, affected_scope) -> CandidateSet
TaskReconstructor.solve(state, task_assessment, candidates, reservations) -> TaskReconstructionResult
FormationReconstructor.solve(state, requirements, candidates, reservations) -> FormationReconstructionResult
RouteImpactDetector.detect(before_plan, candidate_plan, environment) -> RouteChangeRequests
RoutePlanner.plan(route_request, constraints) -> RouteReconstructionResult
```

输入复用明确的 capability gap、reason codes、namespaced scope 和 state_version。
候选过滤/最小扰动求解不直接提交状态，失败保持观测态；后续编排需要合并未处理事件回执，
处理资源预留和任务窗口。只在路线真的失效时提出 route_request。
本阶段完成后停止，不进入 Phase 3。
