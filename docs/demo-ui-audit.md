# P6.1 改版前真实页面自查

本记录先于 UI 改版创建。2026-09-27，访问正在运行的 `http://127.0.0.1:5173/`，使用系统 Chrome / Playwright，1600×1100 窗口，实际操作 SC03、SC08、SC07。前两者实时确定性，SC07 使用真实 Qwen 自适应策略；均经历加载、注入、影响显示、运行、候选预览、现场提交、前后/叠加与历史回放。没有模拟 API 响应。

证据目录：[改版前截图与页面文本](../artifacts/phase61/audit/manifest.json)。SC03 run `44dd3d04-d01c-4087-b99c-cada5e178bc4`；SC08 run `31137d11-d4b7-42b7-b988-1fae3d65f971`；SC07 run `a8a4469b-0940-43f7-8044-ff2d1816e6bf`。SC07 真实 4 次模型调用，首个方案全局校核失败，随后修正成功。截图包括 running、preview 与 replay，非仅最终成功页。

## 中文可读性

从保存的 `body.innerText` 提取去重英文 token（正则 `\b[A-Za-z][A-Za-z_]+\b`）：首页 36、SC08 事件后 54、SC08 结果 98、SC07 展开失败反馈后 201。此数是文本采样词项数，包含下拉选项和单位，不等于首屏可见英文控件数量；最后一项包含手动展开的 JSON。

| 实际词项 | 处理决定 |
|---|---|
| Execution Mode、Adaptive Agent、Bounded Agent、Deterministic Realtime | 主标签中文化，解释模式作用，英文仅技术详情 |
| Run Final Demo、Step Mode、Before、After、Overlay、Demo Replay | 按钮中文化，并说明自动按钮会固定运行综合场景 |
| Engine Trace、Agent Trace、Global Validation、Latency、Delta | 换为重构策略、全局约束校核、执行耗时、实际方案变化 |
| KEEP、RECONSTRUCT、INVALID、READY、COMMITTED、REPLANNED | 显示中文状态与是否真正提交 |
| IN_PLACE_REPAIR、FINALIZE_PROPOSAL、apply_reconstruction_option、get_reconstruction_options | 显示中文技能/决策，原值移到技术详情 |
| FAILED_NODE_ASSIGNED、MEMBER_UNAVAILABLE、CAPABILITY_SHORTAGE、UNRELATED_TASK_REGRESSION | 中文解释约束及对象；错误码保留在详情 |
| PASS_WITH_LIMITATIONS、PASS、FAIL、NOT_EVALUATED | 中文结论与适用边界；状态码仅次级详情 |
| preconditions、structure、membership、scope、time_window、capability、resource、route | 统一中文约束分类 |
| travel_time、transfer_time、dynamic_energy、formation_geometry、dynamic_scheduling | 中文未评估列表 |
| pure_agent_success、fallback_used、overall_success、Model/Solver calls、Lexicographic disruption | 主区解释为智能体自主完成、后备介入、整体结果等，原指标移详情 |
| proposal_id、validation_id、digest、option_id、原始英文 reason、完整 JSON | 仅技术详情；不删除可追溯能力 |
| U17、F03、T03、R05、Z_DEMO 等 | 添加节点/编队/任务/航迹/限制区域前缀；任务名称仅使用真实 domain 名称 |

## 信息层级与五秒判断

首页主要是空白地图和模式开关，没有场景规模、预定异常、影响与技术目的，首次用户无法判断做什么。SC08 事件后有 5/8、2 个失效节点、1 条无效航迹，但分散在左侧；没有醒目的“3 项任务受影响”和事件摘要，需从原始路径和七条错误码归纳。首屏无法直接回答为什么需要重构。

运行中只有等待提示，无技术链当前位置。候选预览时地图已经变绿，底部 After 仍 5/8、变化实体为 0，虽有“尚未提交”小提示，但用户容易理解为数据矛盾。必须把候选预览、现场状态、已提交结果明确分区。

## 地图实际问题

[SC08 事件后](../artifacts/phase61/audit/SC08-event.png)：地图可视范围约 1000 米，点和字很小；几十个节点在初始位置附近重叠。成员细线与长航迹的含义没有明确区分，裸编号与菱形不能说明“平台”和“任务目标”的区别。图例只写“正常/方案变化”，没有说明绿色代表归属改变而非平台飞行。

初始 Before 指初始态，执行后 Before 改指事件后、重构前，只有小字解释。需要中文视图名称、当前看点、关注任务联动和可展开全局地图。不能移动真实坐标来制造漂亮布局。

## 技术架构可见性

| 研究内容 | 原页面可见证据 | 自查判断 |
|---|---|---|
| 1 依赖建模与动态影响评估 | 底部细小英文命名空间路径，约束要展开 JSON | 未充分体现；必须转成可读因果链和中文约束卡 |
| 2 最小扰动分层增量重构 | 成员增删、代价向量 | 仅部分体现；未说明保留了哪些任务归属与航迹，也无候选筛选漏斗 |
| 3 模型驱动闭环智能体 | 英文工具名、英文短理由 | 未充分体现；缺少 Agent/算法/校核边界，SC07 反馈因果必须读 JSON |
| 4 场景验证与可信执行 | 提交按钮与英文约束类别 | 仅部分体现；没有显性研究标签，校核规模与可信提交解释不完整 |

四项技术没有常驻映射；页脚一句技术串联不足以使首次使用者理解研究内容。

## SC08 复杂性与系统作用

真实事件为两个节点失效、一个禁区新增、一项优先级变更。实际直接/传播影响任务集合为 T03/T04/T05；事件后 5/8 可行、能力缺口总量 2、无效航迹 1、失效节点 2。原页面没有把规模、事件、影响和结果组织成一个故事，用户很容易只看到两个红点和一条红线。

[实际结果](../artifacts/phase61/audit/SC08-result.png)能读到 F03 -U17 +U59、F04 -U21 +U47、R05 重规划，但未说明任务归属不变、没有新建编队、其他任务和航迹保持原方案。“Changed tasks 0”还容易被误解为没有起作用。

SC03 同样缺少对共享候选、占用记录及未改内容的解释。[SC07 失败页](../artifacts/phase61/audit/SC07-validator-fail.png)只有展开后的原始反馈能说明 T06 窗口回归；主界面最后显示绿色通过，掩盖了过程中被拒绝的方案。应同时展示首次局部可行、全局失败、扩范围和第二方案通过的真实记录。

## 改版实施顺序

1. 建统一中文展示映射与真实数据投影，保持全部内部枚举、场景、Solver、Validator 不变。
2. 首页场景卡与顶部问题摘要优先，常驻七步技术链和四项研究映射。
3. 地图增加图例、实体前缀、看点和任务关注；真实 P2 路径变为中文因果链，约束详情不再要求读 JSON。
4. 中文决策卡、实际候选漏斗、修改与保留对比、校核规模、中文未评估项；SC07 独立反馈流程。
5. 主层展示问题/影响/结果，二层展示约束/候选/策略，三层保留完整技术详情。
6. 真实浏览器重跑 SC03/08/07，保留前后截图、六问可读性清单与算法/历史证据哈希校验。
