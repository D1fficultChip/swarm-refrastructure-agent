# P6.1 技术可解释任务重构控制台改版报告

完成日期：2026-09-27。本轮完成现有 Demo 的实际自查、信息架构调整、中文化与研究内容展示。没有修改后端算法、场景数据、Solver、Validator、Agent Policy 或历史 P6 evidence；没有开发 MCP、RAG、多 Agent。

## 先检查页面，再实施改版

先在真实浏览器操作 SC03、SC08、SC07 的初始、事件、运行、预览、结果、失败反馈、回放和前后/叠加视图，再创建 [demo-ui-audit.md](demo-ui-audit.md)，随后才修改 UI。原截图、页面文本、真实运行和 manifest 保存在 `artifacts/phase61/audit/`。

原问题不是算法没有结果，而是页面把对象与字段当成主线：SC08 的规模、四件异常与三个受影响任务分散，地图点小且语义不明；候选绿色地图与尚未提交的 5/8 同时出现，容易误解；SC07 的反馈必须展开 JSON；未改变的计划几乎没有体现。四项研究技术只有页脚一句话，无法由首次用户直接识别。

## 新信息架构

第一层是问题与结果：推荐场景卡→当前规模与事件摘要→受影响任务、能力缺口和航迹问题→常驻技术链→地图看点与中文因果链→恢复结果、修改与保留→校核结论。

第二层按需展开：任务能力/资源/编队/航迹/时间约束表、策略决策卡、候选筛选漏斗、逐任务保留对比。SC07 的首次失败→反馈→替代→再次通过因果流程始终显性展示。

第三层“技术详情”默认折叠：完整事件/影响 trace、原始工具名、英文理由、option/proposal/validation ID、digest、token、内部计时、全量 JSON。中文化没有删掉审计能力。

首页重点推荐 SC08 综合态势突变、SC07 全局冲突反馈与智能重规划、SC03 双节点失效与共享资源竞争。其他场景在折叠目录和选择器中访问。场景卡的规模、事件数、描述来自现有聚合 API；事件影响在实际注入后计算，不在首页捏造预期数字。

## 中文映射与真实数据来源

统一映射在 `frontend/src/presentation.ts`，内部枚举保持原值。执行模式、分步讲解、一键综合演示、初始/当前/叠加、任务判断、分层策略、工具动作、全局约束分类和未评估项全部中文显示。

| 原展示 | 新主展示 |
|---|---|
| Adaptive / Bounded / Deterministic | 智能体自适应 / 有界智能体 / 实时确定性模式，并说明功能 |
| KEEP / ADJUST / RECONSTRUCT | 当前方案仍有效 / 需要局部调整 / 需要重构 |
| IN_PLACE_REPAIR 等 | 原编队局部修复 / 任务改派 / 编队重组 |
| PASS_WITH_LIMITATIONS / FAIL | 已通过当前已建模约束校核 / 全局校核未通过 |
| MEMBER_UNAVAILABLE / UNRELATED_TASK_REGRESSION | 成员不满足任务可用时间窗口 / 其他任务被方案连带破坏 |
| Before / After / Overlay | 根据当前阶段显示初始方案、态势变化后/重构前、候选或提交后方案、叠加对比 |
| 裸 T03 / F03 / U17 / R05 | 任务 / 编队 / 节点 / 航迹前缀；有真实任务名称时附上名称 |
| travel_time 等 | 真实飞行时间、节点转场时间、动态能耗、编队几何、动态任务调度 |

真实名称不会被改编成新的任务语义；诊断数据中的 `repair` 仅在展示层翻译为“修复”，原名仍在技术详情。自查的首页英文 token 从 36 降到 0，SC08 事件页从 54 降到 2（UTC 时间标记和实际区域标识 Z_DEMO）；这是保存页面文本的同口径词项计数，包含下拉选项，不是盲测分数。

展示数据有明确来源：

| 信息 | 来源 / 处理 |
|---|---|
| 规模与异常 | API 状态对象及 planned_events / event，按实际类型计数 |
| 受影响任务 | ImpactAnalysisResult 的 affected_tasks 去重 |
| 中文因果链 | 每任务选择一条已有 P2 propagation_path，再转换实体/能力名称；没有新建因果边 |
| 需求、可用、缺口 | TaskAssessment 中原始 capability details |
| 当前可行性与恢复数字 | 后端 before_after_metrics，不在前端重新求解约束 |
| 候选漏斗 | proposal.trace.candidate_sets 的真实 stages / remaining；缺失阶段不显示 |
| 改了什么 | 原 proposal 的 task/formation/route changes，明确候选或已提交 |
| 保留了什么 | 重构前后实际任务、关联编队、航迹对象对比；排除受影响任务集合后再统计完整保留任务 |
| SC07 的首次局部通过与全局失败 | 当次 validation.per_task_results、hard_failures、scope_expansions、selected option 和后续 validation |
| 可信结论 | 原 Validator 的规模、分类检查、失败、限制，以及真实现场 commit |

## 四项研究内容如何展示

七步链为：态势变化识别→任务影响传播→多约束状态评估→智能重构策略→重构技能执行→全局约束校核→可信方案提交。完成绿色、进行蓝色、失败红色、等待灰色，并有文字标记。

四项研究标签始终出现在场景页：

1. **依赖建模与动态影响评估**：三条中文因果链，明确异常如何传导到能力或航迹问题；局部约束表按需展开。
2. **最小扰动分层增量重构**：真实候选漏斗、分层技能、成员替换、任务/航迹保留与实际范围比例。
3. **模型驱动闭环智能体**：中文决策卡、策略/算法/校核器边界、SC07 反馈重规划；确定性模式明确“不调用模型”。
4. **场景验证与可信执行**：场景、事件、全局检查规模与限制、隔离候选、版本保护与现场提交、真实回放。

模型决定采用哪种策略，确定性算法计算具体方案，全局校核器决定能否提交。页面不把大模型说成直接生成并执行任意状态，也不把后备成功算成纯 Agent 成功。

## SC08 的整体问题与最小扰动

顶部直接显示实际 60 个节点、8 项任务、6 个编队；两次节点失效、一个新增限制区域、一次优先级变化。事件后首屏列出 5/8 可行、失效节点 2、受影响任务 3、能力缺口 2、无效航迹 1，明确列出任务 T03/T04/T05。

中文因果卡显示任务 T03/T04 的中继需求 1、可用 0、缺口 1，以及任务 T05 的航迹冲突。不再要求读 `CAPABILITY_SHORTAGE` 等原码。事件后的历史判断有明确时间标签，避免重构完成后仍把它当成当前问题。

地图说明圆点、菱形、粗航迹、细成员虚线，所有编号带实体前缀。节点同坐标时显示实际数量，没有为了布局改坐标；红色故障节点最后绘制，避免被同位置正常节点遮住。可点击受影响任务关注其编队与航迹。

提交后大区域显示实际退出/加入成员和航迹重规划，并单列“未发生任务改派、未新建编队、5 项未受影响任务保持原方案、7 条航迹保持不变”。任务 T03/T04 可以展开看到归属编队保持、原航迹保持。

恢复结果为 5/8→8/8、能力缺口 2→0、无效航迹 1→0、失效节点 2→2、实际范围 8.54%。数字均取 API 或实际差异投影，不写死在页面。旁边说明“设备故障事实没有被修改，系统通过任务、编队与航迹方案重构恢复任务可执行性”。

## SC07 的反馈重规划

新版真实浏览器在线运行中，模型执行了 3 次调用并成功提交；结果以完整 [SC07 原始运行](evidence/phase61/SC07-run.json) 为准。过程不是编造的示范文本：

```text
原局部工具给出首份诊断方案
→ 首次校核记录显示任务 T03 的已检查约束满足
→ 同次全局检查发现任务 T06 成员时间窗口不满足 / 无关任务回归
→ 拒绝该方案
→ Agent 根据结构化反馈将任务 T06 纳入范围
→ 从算法选项中选择替代方案
→ 后续全局校核通过
→ 用户执行现场提交
```

页面能在不打开 JSON 的情况下看到以上过程，并切换“查看首次校核失败 / 查看后续校核结果”。这两个视图是已发生记录的检查，明确不是假装 API 实时停在某一内部步骤。回放只显示第一条时，不提前宣称尚未展示的后续方案已通过。

## 真实性与提交边界

候选预览在地图和拟修改区域展示，顶部现场指标与恢复区仍为未提交状态；正式提交后才更新实际恢复结果。原始隔离分支求解、Validator、事务化 Commit 和版本语义保持不变。

耗时分为状态分析、候选/提案算法、模型、全局校核、提交和总耗时。模型时间单列；确定性模式写“不使用”。不把候选/提案时间当成所有算法时间，不把可能嵌套的子阶段简单求和，也不宣称云模型 <5s。

SC02 仍部分支持，默认隐藏；SC08 优先级变化仍不代表完成动态任务调度。所有五项未评估物理/调度内容用中文明示。失败、模型不可用和后备介入都保留可见提示。

## 真实浏览器与截图

所有截图读取真实 API，没有手工填充图表数据。

| 检查内容 | 截图 |
|---|---|
| 首页 | [home.png](../artifacts/phase61/browser/home.png) |
| SC08 初始态 | [SC08-initial.png](../artifacts/phase61/browser/SC08-initial.png) |
| SC08 事件后 | [SC08-event.png](../artifacts/phase61/browser/SC08-event.png) |
| SC08 运行中 / 候选预览 | [running](../artifacts/phase61/browser/SC08-running.png)、[preview](../artifacts/phase61/browser/SC08-preview.png) |
| SC08 结果 / 叠加 / 重构前 | [result](../artifacts/phase61/browser/SC08-result.png)、[overlay](../artifacts/phase61/browser/SC08-overlay.png)、[before](../artifacts/phase61/browser/SC08-before.png) |
| SC08 恢复与保留特写 | [恢复](../artifacts/phase61/browser/SC08-recovery-detail.png)、[修改与保留](../artifacts/phase61/browser/SC08-changes-detail.png) |
| SC03 实际候选筛选 | [SC03-candidates.png](../artifacts/phase61/browser/SC03-candidates.png) |
| SC07 首次失败 / 后续通过 | [失败](../artifacts/phase61/browser/SC07-validator-fail.png)、[通过](../artifacts/phase61/browser/SC07-second-pass.png) |
| SC07 可读因果特写 | [失败记录](../artifacts/phase61/browser/SC07-failure-detail.png)、[后续通过](../artifacts/phase61/browser/SC07-pass-detail.png) |
| 回放与窄屏 | [SC08 回放](../artifacts/phase61/browser/SC08-replay.png)、[SC07 回放](../artifacts/phase61/browser/SC07-replay.png)、[窄屏](../artifacts/phase61/browser/SC08-narrow.png) |

原始数据、哈希、截图关联与测试汇总见 [证据索引](evidence/phase61/index.json)。特写截图由本轮真实运行通过回放 API 再渲染，元数据明确来源。

## 测试与复现

后端 **282 个测试通过**，一条既有 Starlette/httpx 弃用警告。前端 **7 个测试通过**，覆盖原始路径不变、实际缺口、ID 改变后不硬编码、跨任务保留统计、候选/提交边界、SC07 不补造后续反馈、中文校核限制；TypeScript 和生产构建通过。

真实浏览器 **4 条通过**：SC08 六问/真实数据/预览/提交/回放；SC03 候选与航迹保留；SC07 真实模型失败反馈闭环；一键综合演示与 760 像素窄屏。首次测试脚本因链接移入折叠技术详情而等待可见角色，已改为读取真实链接 DOM 后通过；未放宽产品验收断言。

```bash
bash scripts/run_demo.sh
# 另一个终端，在仓库根目录：
npm --prefix frontend test
npm --prefix frontend run build
cd frontend
DEMO_REAL_AGENT=1 DEMO_CHROME_PATH=/usr/bin/google-chrome npm run test:e2e
cd ..
DEMO_CHROME_PATH=/usr/bin/google-chrome node frontend/scripts/capture-evidence.mjs
PYTHONPATH=/tmp/cluster-reconstruction-deps:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

在线 SC07 测试需后端按原脚本读取已授权的 `docs/API`；不设置 `DEMO_REAL_AGENT` 时该付费测试跳过，另外三条正常运行。截图脚本重放原 P6 在线证据，输出改到 `artifacts/phase61/replays/`，不覆盖 P6 截图与报告、不新增模型调用。前端无新增依赖。

## 修改文件

`frontend/src/presentation.ts` 为中文词典及真实数据投影；新增 `Narrative.tsx` 为问题摘要、技术链、因果卡、反馈过程、修改/保留与结果等组件。`main.tsx` 调整页面顺序与操作语义，`MapView.tsx` 改善图例、实体标签、同坐标计数及关注任务，`style.css` 使用白底、深色文字和语义颜色。更新前端单元/E2E 测试与证据截图脚本，新增三份要求文档、P6.1 evidence，并更新操作指南与 README。后端业务文件与场景保持不变。

## 七项验收结论

1. **5–10 秒理解 SC08：** 首屏直接给出规模、四类事件、三个受影响任务及缺口；开发侧检查满足信息可见性，尚未进行陌生用户计时盲测。
2. **不打开原始记录解释 T03/T04/T05：** 可以，三条真实中文因果链与需求/可用数值直接显示。
3. **研究技术可见：** 可以，前三项核心研究及第四项可信执行均有常驻标签、技术链和对应证据区。
4. **修改与保留可见：** 可以，成员/航迹变化、任务归属保持、未受影响任务与航迹保留单列。
5. **SC07 反馈重规划可见：** 可以，首次局部满足、全局回归拒绝、扩范围、替代方案、后续通过无需 JSON。
6. **基本中文化：** 已完成主流程中文化，真实标识、时间基准和技术详情原值保留。
7. **适合继续制作正式录屏：** 已具备可讲解页面和分步流程；建议下一步先做实际观众可读性确认。本轮未新增录屏或演示专用模式。

仍有边界：密集或同坐标节点并非高保真仿真；底层整次作业返回，不能准确实时高亮每个内部工具；历史记录展示不等于中间状态动画；语义翻译是有限映射，未知代码保留原始详情；最小扰动不等于全局最优证明。完成本轮后停止。
