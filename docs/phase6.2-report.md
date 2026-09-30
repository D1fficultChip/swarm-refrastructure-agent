# Phase 6.2：动态任务环境、随机挑战与持续在线任务重构

完成日期：2026-09-28。交付版本 0.6.2。本阶段新增动态任务应用层、约束引导随机生成、连续任务会话、动态控制台和检查点回放；SC01–SC08、P2–P5.1 核心算法、全局校核和事务提交保持原样。

## 交付结果

动态模式默认生成 60 节点、8 任务、6 编队的初始可行场景。服务端时钟驱动编队参考点沿任务航迹运动，成员保留相对偏移，前端每 500 ms 读取运行态。Planning State 和 Runtime State 已分离：动画步进不递增版本；规划相关事件、显式位置同步和 Commit 才产生版本变化。

运行时事件仍进入原 EventInjector，并经过 TRDG、影响分析和任务评估。需要重构时，动态层调用原 `DemoApplicationService`，由现有 baseline 或 Agent Policy 使用 P3 primitives 形成 Proposal，再经 P4 Global Validator 和事务提交。动态层没有实现另一套求解器。

同一 DynamicMissionSession 已自动验证以下序列：运行 → L1 影响但无硬失败 → 继续 → L2 事件 → 重构/提交 → 继续 → 第二个 L2 事件 → 第二次重构/提交 → 继续。最终 8/8 任务可行，无重复节点归属、无部分提交和版本错误。重构期间的新事件进入队列，并在当前事务完成后应用。

## 验证数据

| 验证 | 结果 |
|---|---|
| 随机场景生成 | 50 个默认 seed 全部结构有效、初始任务可行；另验证 30/4、30/8、60/4 高级组合 |
| Seed | 同 seed 完整 `ScenarioState` 相等；不同 seed 的节点位置与任务目标不同 |
| 难度采样 | L1/L2/L3/L4 各 20 个，语义断言全部通过；L4 构造成功 20/20 |
| ID 非写死 | 20 个采样中节点对象覆盖至少 6 个 ID，明确不恒为 U17/U21 |
| 版本隔离 | 连续运动 12s 后规划 version 仍为 0；显式同步后只增至 1 |
| 持续任务 | 3 个事件、2 次重构提交，最终 8/8、成员归属唯一 |
| 并发边界 | 重构期间事件进入等待队列，提交后顺序处理 |
| 动态 HTTP | session/start/step/event/save/timeline/run 全链通过 |
| 前端 | 9 个单元测试、TypeScript 和 Vite production build 通过 |
| 浏览器 | 完整回归 4 项通过、1 项显式在线 SC07 跳过；动态项真实完成 L1 + 两轮 L2 重构 + Checkpoint 回放，无 page error |

最终回归为后端 **289 passed**（1 条既有 Starlette/httpx 弃用警告）、前端 **9 passed**、TypeScript/Vite production build 通过。浏览器标准 SC08、SC03、窄屏一键演示和动态连续任务均通过；SC07 的在线浏览器用例仍需 `DEMO_REAL_AGENT=1` 显式开启，本阶段已通过下述独立真实 Agent 小样本验证模型链。

真实 Adaptive Agent 只做小样本验证，不作为新 benchmark。三个 L3 随机场景全部 COMMITTED、8/8 恢复、`pure_agent_success=true`、未使用 fallback：

| 场景 seed | 事件对象 | 编队修复 | 航迹重规划 | 模型调用 | 总时延 |
|---:|---|---|---|---:|---:|
| 62001 | U15 + Z0001A | F03：U15→U32 | T05 | 2 | 5.679s |
| 62002 | U15 + Z0001A | F03：U15→U32 | T02、T08 | 4 | 10.866s |
| 62004 | U25 + Z0001A | F05：U25→U35 | T08 | 4 | 10.967s |

前两条完整记录在 [agent-smoke.json](../artifacts/phase62/agent-smoke.json)，第三条在 [agent-smoke-seed62004.json](../artifacts/phase62/agent-smoke-seed62004.json)。样本证明不同 seed 能产生不同空间布局、事件对象和重构 Delta；云模型时延仍明显高于 baseline，不据此承诺实时上限。

## 新增模块与接口

- `backend/app/dynamic/clock.py`：确定性仿真时钟。
- `motion.py`：折线弧长插值。
- `scenario_generator.py`：约束引导随机任务生成。
- `event_sampler.py`：已有事件类型与 L1–L4 语义采样。
- `models.py`：Runtime State、Session、Timeline、Checkpoint 和指标。
- `service.py`：运行态、规划同步、事件排队、持续重构和持久化。
- `api.py`：`/api/v1/dynamic/*` REST 接口。
- `frontend/src/DynamicMission.tsx`：动态配置、时钟、地图、进度、TRDG、时间线、指标与回放。
- `scripts/smoke_phase62_agent.py`：真实模型小样本复现入口。

详细状态语义和版本处理见 [动态环境架构](dynamic-environment.md)，现场操作见 [动态演示指南](dynamic-demo-guide.md)。

## 最终问题回答

### Q1：同一个 Demo 是否能够连续运行并处理多次态势变化？

可以。自动测试和真实浏览器均在同一会话处理 3 个事件和 2 次重构，提交后继续推进仿真，而不是结束 Demo。

### Q2：不同 seed 是否真正产生不同节点位置、事件对象和重构结果？

可以。生成测试确认位置与目标变化；20 次采样覆盖多个节点。真实 Agent 中 seed 62001 与 62004 分别使 U15/F03 和 U25/F05 失效，补入 U32 与 U35；不同场景也产生 T02、T05、T08 等不同航迹 Delta。稳定序号 ID 便于审计，但对象选择不固定。

### Q3：同一个 seed 是否可以完全复现？

场景定义、事件采样和确定性 baseline 可以完全复现，双 seed 会显示并持久化。云模型调用还受外部服务行为影响，因此“完全复现”不扩展为逐 token 相同的模型输出；已提交运行可通过 Checkpoint 和结构化记录精确回放，不再次调用模型。

### Q4：节点动画是否不会导致 ScenarioState.version 高频变化和 Proposal stale？

不会。Runtime tick 只更新 `DynamicRuntimeState`。12 秒连续步进保持 version=0 的测试已通过；只有显式规划快照、事件和 Commit 增加版本。

### Q5：两种事件结果是否都支持？

支持。L1 真实走影响传播后判定无硬失败，记录 NO_RECONSTRUCTION_REQUIRED 并继续；L2/L3 会形成硬约束缺口，经 Proposal→Validator→Commit 后恢复并继续。

### Q6：Random Challenge 是否真实使用现有 P2–P5.1 核心？

是。动态服务只负责环境、时钟、采样和应用编排。事件通过现有 EventInjector/P2；重构通过现有 primitives、Agent/baseline、Global Validator 和事务提交。真实 run ID、Proposal、trace、validation 和 commit 均由原核心产生。

### Q7：是否已具备随机挑战录制流程？

已具备。页面支持重新生成不同场景、动态执行、突发事件、真实影响链、在线重构、继续执行、第二事件和历史检查点回放；操作顺序与现有真实浏览器截图已写入动态演示指南。

## 边界

系统仍是二维任务态势动态仿真。未实现无人机动力学、真实转场、碰撞、动态能耗、通信传播或真实任务效能。任务进度是基于时间窗/航迹的展示值。活动会话是单进程内存状态，持久化内容用于审计与回放。本阶段不开发 MCP、AirSim、ROS、3D 或真实飞控。
