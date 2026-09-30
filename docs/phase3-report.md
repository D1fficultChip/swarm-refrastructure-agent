# Phase 3：面向最小扰动的分层增量重构

> 本文保留 Phase 3 交付时的历史记录。当前 Phase 4 已修正成本顺序和资源成本语义，
> 并实现全局验证、提交与 `/reconstruct`；最新行为见 [Phase 4 报告](phase4-report.md)。

本阶段输出 ReconstructionProposal，**尚未由 Phase 4 全局 Validator 验证或 commit**。
没有 LLM、Agent Harness、RAG、前端或真实飞控。初态/事件与甲方适配边界保持不变。

## 1. 代码与兼容性

编码前核对了 architecture、Phase 2 报告、领域/结果模型、TRDG、传播、评估器、SC01
及已有测试。真实关系始终为 U17→F03→T03、U21→F04→T04。没有修改 Phase 1 领域模型。

| 新增/修改文件 | 职责 |
|---|---|
| `models/phase3.py` | TaskRequirement、Scope、Candidates、Delta、Proposal、Trace、计时 |
| `reconstruction/scope.py` | 当前约束对账、历史来源、保护任务、求解顺序 |
| `reconstruction/candidates.py` | 逐层过滤与拒绝证据、距离排序 |
| `reconstruction/reservations.py` | 私有方案的节点归属、时间和资源池预留 |
| `reconstruction/formation.py` | 原位修复/新编队的能力组合求解 |
| `reconstruction/task.py` | 已有编队直接接替的局部可行性 |
| `reconstruction/routes.py` | 局部路线检测、二维网格 A*、连续碰撞检查 |
| `reconstruction/cost.py` | 最终 delta、角色变化、成本分项与字典序 |
| `reconstruction/planner.py` | 层次控制、跨任务回溯、Proposal 构建 |
| `reconstruction/common.py` | 私有 delta 物化、局部评估、状态摘要、唯一 ID |
| `reconstruction/config.py`、`config/phase3.json` | 集中预算和路线代价配置 |
| `scenarios/manager.py`、`models/api.py`、`api/routes.py`、`main.py` | 实验 propose API、版本保护，保留现有接口 |
| `backend/tests/test_phase3.py`、`test_phase3_api.py` | 核心算法、竞争回溯、API 不提交/版本竞争 |
| `backend/tests/test_server_startup.py` | 真 Uvicorn 增加 propose → 观测态不变检查 |
| `backend/tests/test_phase2_api.py` | 阶段元数据断言由等于 2 改为至少支持 2，原功能断言不变 |
| `scripts/phase3_scenarios.py`、`demo_phase3.py` | 真实输入案例、Proposal/计时证据 |
| `pyproject.toml`、README、architecture、本报告 | 版本 0.3.0、启动与接口/设计说明 |

上表未写前缀的业务路径均位于 `backend/app/`。没有新增第三方依赖。

## 2. Scope 与 TaskRequirement

`TaskRequirement.from_task` 复制 Task 的能力、资源、最低数量、窗口、起终点、优先级，
不保存原编队能力节点，不持久化为第二份业务真值。重新分配的变量可以改变，需求不变。

Scope 入口显式使用当前 State 重新检查所有任务一次，识别仍存在的 FAIL/DEGRADED；
历史回执只提供事件来源。这样即使只传最新回执或历史缺失，也能发现 T03/T04 两个缺口。
如果禁区已经移除，旧 ROUTE_CONFLICT 不再进入 Scope。非活动任务不参与重构。
Phase 2 的局部事件评估机制没有改变。

Scope 包含待处理任务、编队、不可用节点、潜在路线、当前 gap、保护任务、资源预留、
state_version、source_events。按 FAIL 数降序→priority 降序→deadline 升序→ID 排序。
这里不将 severity/priority/deadline 混成不可解释评分。

## 3. CandidateGenerator

候选输入是任务需求、当前 gap、Scope、包含现有分配的 ReservationLedger。
只需补当前缺口，不需要单节点承担所有任务能力；当缺数量或资源时，相关性筛选允许
为数量/资源贡献入选。每阶段输出 remaining 数量和首个拒绝原因。

顺序：状态→能力/数量/资源相关性→可用窗口→任务时间冲突→资源可用性→保护任务→静态成员归属。
距离用于排序与成本，不作无根据的硬阈值。候选记录 nominal×health 有效能力、资源容量、
当前分配、距离、switch_cost、gap 贡献、eligibility_reasons。

首版只抽取未属于任何编队的自由节点；不会拆借正常或其他受影响编队。整个已有编队
由 Level 2 考虑。该限制遵循 Phase 1 的静态唯一成员关系，可能遗漏允许动态转场/借用时
的方案，因此 INFEASIBLE 只针对本阶段声明的策略空间。

## 4. Solver 选择、分层和硬约束

选择确定性候选子集搜索 + 跨任务有界回溯。SC01 实际候选只有 4 个中继节点，
不需要为此新增 CP-SAT 大型依赖；算法边界保持可替换。

Level 1：保留任务、编队身份和所有健康成员，移除 FAILED/零有效健康成员，
从候选补足能力/数量/资源。编队最低能力和共享该编队的任务需求一起考虑。
Level 2：原位没有完整后续方案时，枚举其他已有编队；成员组成不变，检查能力、
资源池、窗口和已有任务承诺，不破坏受保护任务。
Level 3：从自由节点组合新编队；任务改指向新编队，原编队不被偷偷拆解。
新编队的任务需求仍来自 Task，不继承旧编队独有的最低能力约束。

组合变量等价于每个候选一个二元选择 x_u。实际对候选子集求解：

```text
已有可用供给 + Σ x_u × candidate_supply ≥ required_supply
已有可用人数 + Σ x_u ≥ minimum_nodes
已有可用资源 + Σ x_u × resource ≥ required_resources
任务与编队窗口不冲突；availability 覆盖任务窗口
同编队全部 ACTIVE 资源承诺 ≤ 资源池
失效成员不进入新/修复后的活动组成
受保护任务和已解决任务不得产生新的已实现硬约束失败
```

初步代数筛选后，在私有状态上重算 Phase 2 已实现约束作本地复核。
此处显式全量检查以防破坏受保护任务，不冒称 Phase 4 全局 Validator；未评估项依然存在。
TaskReconstructor/FormationReconstructor 只返回 LocalOption/delta，上层合并到私有快照。

## 5. 最小扰动目标与搜索边界

先恢复已实现硬约束，完整分支之间使用 Python tuple/list 顺序语义的字典序成本：

```text
task_reassignment_count
new_formations
formations_changed
formation_membership_changes  # 加入 + 移出
node_switch_count             # 成员归属改变，包括失败成员退出
distance_cost
resource_cost
route_change_count
```

没有把数量与距离用任意权重相加；再远的原位可行修复也不会仅因距离较大输给任务重分配。
distance_cost 为新增成员到集合参考点距离，加任务参考起点变化距离；不代表飞行时间。
resource_cost 为新增成员带入方案的 resource_remaining 容量，是占用容量而非消耗仿真。
角色变更单独记录，单纯角色名称变化不计为节点归属切换。

配置：单次组合最多 4096 个子集、32 个可行选项、联合最多 512 个搜索节点。
按基数从小到大枚举，省略已可行集合的额外超集；从一个分支退出就丢弃其私有状态/预留。
先比较本层多个选项的完整后续解；若本层所有已搜索后续都失败才扩大层级。
预算耗尽后的扩大必须记录 SEARCH_LIMIT_ESCALATE_NOT_PROVEN_INFEASIBLE。

这是有界层次搜索，不是全局最优证明：`optimality_proven=false` 始终成立。
`search_complete=true` 仅表示配置策略没有被预算截断，不表示穷举了所有替换/借用/调度。
剪去超集、保留健康成员、不拆借其他编队和顺序层级控制都限制了搜索空间。

## 6. 预留与多任务竞争

ReservationLedger 根据每个分支的私有状态重建成员归属、各任务时间窗口、编队资源承诺。
静态唯一成员归属比“只禁止窗口重叠”更严格，避免一个节点同时出现在两个编队。
候选已被先前分支选中后，从下一任务候选中排除。

若第一任务占用第二任务唯一能力节点导致失败，回溯撤销第一任务选择，尝试其他组合。
测试构造一架多能力节点与一架单能力节点：局部最近选择会阻塞第二任务，最终搜索
把单能力节点给第一任务，多能力节点留给第二任务，两者都满足。
资源预留仅记录计划池承诺，不扣减观测态节点资源。

## 7. 路线检测与 A*

RouteImpactDetector 在组成/分配形成后检查：路线缺失、起点变化、目标/终点不符、
当前受限矩形与连续路线线段相交。未失效路线记录 EXISTING_ROUTE_STILL_VALID，
不调用 RoutePlanner；只有 Scope 内任务进入此检测。

L1 不移动 Task.start。L2/L3 的集合参考点明确定义为新可用成员位置均值，
变化写入 TaskAssignmentChange，不把新位置假装成已完成物理转场。

二维网格使用场景 bounds/grid_resolution；8 邻接边及精确起终点连接均用连续闭矩形
线段碰撞检查，边界接触也禁止。端点连接到周围网格点并检查每条连接；不能只检查航点。
欧氏距离作为下界启发式；边成本为：

`edge_length + λ × edge_length × mean_distance_to_old_polyline / grid_resolution`

λ 默认 0.1，可在 config/phase3.json 调整，含义是偏离旧路一个网格间距时每米增加 10% 代价。
没有旧路线时偏离项为 0。没有随机项，不做未经重新碰撞验证的 smoothing。
返回的路线再次核验起终点/边界/每条线段；配置上限为 50000 网格点、25000 次扩展。
NO_GRID_ROUTE 是该离散图无解，不是连续空间绝对不可达证明。

## 8. Proposal、失败语义和接口

Proposal 包含 base version+digest、source_events、scope、策略、任务/编队/节点角色/路线 delta、
局部已恢复/剩余检查、原始成本、solver status/time、search_complete、解释和 trace。
不以一整份无说明 State 代替 delta。内部 preview 只用于求解和测试，不对外提交。

- FEASIBLE：当前声明的局部约束在选定私有方案中满足。
- INFEASIBLE：在未被预算中止的首版策略空间未找到组合；不产生假节点或降低需求。
- PARTIAL：搜索预算导致未完成，或组成已形成但路线/降级约束仍未解决；remaining_constraints 保留。
- ERROR：局部路线输出自检失败等算法错误，不伪装可行结果。

未找到完整组成时可以返回空 delta 和明确未恢复约束，不保证最大化部分恢复数量。
degraded 软状态未消除也作为 remaining 返回，最终不冒称完全恢复。

`POST /api/v1/reconstruction/propose` 输入 session_id/expected_version。锁内取得快照与
事件回执，锁外求解，返回前再次检查版本；中途有新事件返回 409。接口不写 session，
重复请求只产生新的 proposal_id，不改变观测态或事件历史。`/reconstruct` 继续 501。

## 9. 真实运行 Example 1：U17

E001 后版本 1，Scope=[T03]。Task 需求 sensor=2/relay=1/navigation=1，F03 当前
sensor=3/relay=0/navigation=2，唯一能力缺口 relay=1。

过滤：60 → 状态 59 → relay 相关 9 → 窗口 9 → 无冲突 4 → 最终 4。

| 候选 | 到原集合点距离（m） |
|---|---:|
| U59 | 268.173 |
| U47 | 294.742 |
| U53 | 515.024 |
| U41 | 728.157 |

实际选中 U59：IN_PLACE_REPAIR，F03 加 U59、移出 U17，Task T03→F03 不变，
R03 保留且 RoutePlanner 未调用。最终 relay=1，局部能力/资源/数量检查通过。
成本：任务重分配 0、改编队 1、成员增删 2、节点归属变化 2、路线变化 0、
distance=268.173、占用资源容量=100。没有硬编码 U59。

## 10. 真实运行 Example 2：U17 + U21

连续 E001/E002 后版本 2，两个缺口均未恢复，Scope=[T03,T04]，来源包含两个事件。
搜索先考虑 T03，再基于预留后的状态考虑 T04；如果已选择 U59，T04 候选只有
U47/U53/U41，U59 不会再次被选中。

最终：F03 加 U59/移 U17；F04 加 U47/移 U21；T03/T04 归属不变，R03/R04 不变。
两次加入使用不同节点，正常任务、编队和路线保持原值。
总成本：任务重分配 0、改编队 2、成员增删 4、节点归属变化 4、路线变化 0、
distance=484.490、占用资源容量=200。观测态仍保留 U17/U21 FAILED 和原成员关系，等待 Phase 4。

## 11. 真实运行 Example 3：层级升级

测试输入 SMALL：任务 T0 要求 relay=1，原 F0 另有自己的 navigation=1 最低要求，
但可用节点没有导航能力。F1 已有一个可用中继 U1，未承担其他任务。

L1 不能满足 F0 的导航最低要求 → 明确失败/升级 → L2 直接改为 T0→F1。
F1 不继承 F0 的特殊最低要求；Task.requirements 未改动。成员组成不变。
参考起点由 (0,0) 改为 (10,10)，仅 R0 按新端点重规划。
任务重分配 1，编队成员变化 0，路线变化 1；这是实际求解得到的 TASK_REASSIGNMENT。

额外运行 L3 输入：没有 F1，但自由节点 U1 提供 relay、U2 提供 sensor；T0 要求两项，
原 F0 仍缺无法补齐的导航最低能力。L1→L2 失败后，L3 组成 F_NEW_T0=[U1,U2]，
T0 需求保持不变，参考点 (15,10)，只改变 R0。另有完全无可用能力的输入返回 INFEASIBLE。

## 12. 测试与性能

复现命令：

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m scripts.demo_phase3 --runs 20 --output artifacts/phase3-evidence.json
```

覆盖 A–L：原位修复、非 ID 驱动成本选择、候选时间冲突、Level 2/3、无解、连续故障、
无关任务不变、原位不调用路线规划、禁区仅局部路线修改、连续碰撞与非网格端点、
原位优于整个任务重分配。此外覆盖贪心陷阱、历史缺口消失、预算截断、资源不足、
优先级排序、Proposal round-trip、不提交、版本竞争及实际 Uvicorn HTTP。

最终回归：**115 passed, 1 warning in 3.16s**。保留原 Phase 1/2 的 93 个测试用例，
新增 20 个算法测试用例和 2 个 API/并发版本测试；既有真实启动测试扩展了 propose 检查。
唯一 warning 是原有 Starlette TestClient 对 httpx 的上游弃用提示，未屏蔽。
上述 3.16s 是测试集耗时，不是重构系统延迟。

使用 perf_counter 记录 scope_build、candidate_generation、task_solver、formation_solver、
route_impact、route_planning、proposal_build、coordination、total_phase3_ms。
coordination 包含分支复制、本地检查、成本比较等未落入前述阶段的开销。
solver_time_ms 为联合搜索时间，不含前后 Scope/Proposal 构建；total 为完整领域提案过程。
不含 HTTP、模型、Phase 4 或磁盘导出开销。

本轮开发机 Python 3.10.12、Intel i9-12900HX、Linux x86_64，SC01 连续双故障热运行 20 次：
total p50=41.013 ms、p95=49.594 ms、max=49.883 ms。首轮单故障 11.871 ms、双故障
40.932 ms；Level 2 1.211 ms、Level 3 1.384 ms、无解 0.501 ms。
这些是未隔离系统负载的观察值，不作为测试阈值，**不能用于宣称完整重构 <5 s**。
原始阶段计时/候选/策略/成本见 [JSON 证据](../artifacts/phase3-evidence.json)。

## 13. 当前限制与 Phase 4 接口建议

没有全局最优、无连续空间路径完备性保证；搜索预算及策略空间限制明确返回。
不拆借其他编队，不做真正的节点转场调度、飞行速度、真实能耗、软风险或队形几何。
起点变化是参考点变化，travel_time 仍 NOT_EVALUATED。旧编队失去任务后可能仍有失效
成员或未满足自身最低能力；Proposal 只宣称被分配任务的局部修复，交 Phase 4 决策清理。

尚未实现 GlobalRebuildBaseline，避免影响本阶段主体；预留评测接口为
`GlobalRebuildBaseline.propose(state) -> ReconstructionProposal`，后续统一比较任务变化、
节点切换、路线变化和耗时。本轮不声称已完成全局重建基线对比。

Phase 4 建议：`validate_proposal(observed_state, proposal) -> ValidationResult`，先核验
version+digest，再按 delta 物化私有状态，独立验证全局约束与未评估项策略；
`commit_proposal(session_id, expected_version, proposal_id)` 仅在验证通过和版本仍匹配时提交，
否则保持观测态。不能直接信任 FEASIBLE、替代已发生的事件事实或把未求解任务删除。

本阶段完成后停止，不进入 Phase 4。
