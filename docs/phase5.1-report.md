# Phase 5.1 完成报告

日期：2026-09-24。实现范围止于 Phase 5.1；没有进入 Phase 6，没有新增前端、MCP、RAG 或多 Agent。

本阶段保留模型对任务、策略、具体选项、scope 和反馈重规划的决定权，减少查询与验证/提交的机械调用。
默认运行优化策略，原 P5 控制器和原 P4 baseline 继续独立存在。真实测量与阶段结论见下方统计部分。

最终优化配置 70/70 次真实闭环成功，A/B/C 的模型调用分别稳定为 1/2/1 次，D 为 2–5 次。
B 的 20 次扰动都等于 baseline；D 已显著减少调用，但尚不能保证 2–4 次。四类最终 p95 均高于 5 秒，
主要时间消耗在云 API。完整回归 262 项通过；另完成 35 次真实消融和 7 次预算模式冒烟，后者保留一次 fallback 失败。

## 1. 对 Phase 5 的复盘

编码前读取了原 [报告](phase5-report.md)、[架构](agent-architecture.md)、
[真实证据](evidence/phase5-real.json) 和 [Mock 证据](evidence/phase5-mock.json)。原始 JSON 未改写。

| P5 案例 | 调用数 | 单次总时延 | 暴露的问题 |
|---|---:|---:|---|
| A 单故障 | 5 | 21.78 s | inspect、候选查询、验证、提交占用多个模型回合 |
| B 双故障 | 5 | 18.65 s | T04 选择 L3；baseline 两次 L1 的扰动更小 |
| C route-only | 3 | 10.29 s | 规划后的验证和提交也需要模型 |
| D 反馈重规划 | 14 | 62.36 s | 理由说 U2，index 却执行 U1；状态变化后 index 失效 |

这些是原模型的各一次观测，不能作为相同环境下的统计对照。新 benchmark 使用重复测量，失败也进入分母。
全部批次及原始文件校验值见 [证据索引](evidence/phase51-index.json)，
旧算法/原始证据校验和测试结果见 [验证记录](evidence/phase51-verification.json)。

## 2. 文件与接口

以下路径除特别说明外相对 `backend/app/`；完整设计见 [策略优化说明](agent-policy-optimization.md)。

| 文件 | 变更 |
|---|---|
| `agent/policy_models.py` | ReconstructionOption、PolicyAction、DecisionSnapshot、PolicyDecisionRecord、PromptSizeMetrics |
| `agent/options.py` | 独立有界选项准备、版本绑定、缓存、真实约束效果及字典序支配判断 |
| `agent/decision_snapshot.py` | 只读摘要与增量 context；当前/历史反馈归属 |
| `agent/policy_tools.py`, `policy_prompt.py` | 语义选项工具、严格字段组合、理由一致性诊断和策略指令 |
| `agent/policy_router.py` | Fast/Strong 模型路由，不选择重构策略 |
| `agent/optimized_orchestrator.py` | 模型策略循环、finalize、预算、显式 fallback 与审计 |
| `agent/prompt_metrics.py`, `providers/{base,openai_compatible,schema}.py` | 请求大小分项、去重复 schema、严格响应 schema |
| `agent/{workspace,tool_registry,action,models,config,orchestrator}.py` | 工作修订/失败内容关联、工具兼容、结果/配置/原 P5 统计兼容 |
| `api/routes.py`, `models/api.py`, `main.py` | 选择策略 profile/运行模式，保存完整结果；版本 0.5.1 |
| `config/agent.json`, `.env.example`, `pyproject.toml`（仓库根） | 默认优化策略、可配置模型/预算；没有新增运行依赖 |
| `scripts/benchmark_phase51.py`（仓库根） | 连续重复试验、独立 baseline、消融、原始 trace、可重算统计 |
| `backend/tests/test_phase51_{policy,api_metrics}.py` | 新增回归测试 |
| `README.md`, `docs/{architecture,agent-architecture,agent-policy-optimization}.md` | 当前边界、配置、运行及设计说明 |

P2–P4 核心算法以及 P5 两份原始证据共 22 个被保护文件的 SHA-256 校验一致。
GlobalConstraintValidator、ProposalMaterializer、版本/摘要保护、事务 Commit、Rollback 和 Idempotency 沿用原实现。

### 稳定选项与 Snapshot

模型复制当前 `option_id`，不再使用数组下标。ID 绑定世界版本/摘要和单调工作修订/摘要以及 scope。
同一修订重复查询稳定；apply、scope 改动、discard、refresh 或新事件使旧 ID 返回 `STALE_OPTION`。
模型理由不能重写选中 ID 的含义。原 P5 profile 为兼容仍保留下标接口，新 profile 工具表已经移除。

`DecisionSnapshotBuilder` 只读汇总，不调用 Solver。独立 `OptionCatalog` 有界预取已有 P3 三类 primitive 结果，
缓存同一修订，明确统计 Solver 次数和 option_generation_ms；并非免费获得所有候选。
模型可以查询未显示选项、直接选 L2/L3、先处理任意任务，或只调用航迹工具。

### 支配保护与模型权力

同一决策节点、同一目标任务中，A 对已评估硬目标缺口不劣于 B、保留的其他未恢复任务备用机会不劣于 B，
且真实累计扰动向量字典序严格更小时，B 才被标为 DOMINATED。
优化模式拒绝 B 并给出 better_options。没有“只要有 L1 就禁止 L3”的规则。
备用机会包含窗口资格、资源、能力和唯一候选身份；它是有限选项上的保守摘要，不是全局未来可行性证明。
尚未规划的航迹成本不估造，单独报告 requires_replan。

### 循环、自动验证与反馈

```text
只读 Snapshot → Router 选择模型 → 模型选择 option/tool + 可选 scope + finalize
→ 类型/版本/scope/支配检查 → 私有执行
→ 若模型请求 finalize：原 Global Validator
→ FAIL：真实结构化反馈回到模型
→ PASS：原事务 Commit（锁内仍会重新验证）
```

自动化只承担不可绕过的校验和模型请求完成后的机械提交，没有自动替换失败策略。
`AUTO_COMMIT_AFTER_VALIDATION=false` 返回 VALIDATED_NOT_COMMITTED，不修改观测世界。

失败方案用 working 内容摘要关联，单独扩 scope 不会丢失替换语义；无实际 delta 的选项不再伪装成修复。
换成新方案后，旧 FAIL 保留在审计中但标注 `applies_to_current_plan=false`，新方案显示 NOT_VALIDATED。
Delta Context 只保留这个旧验证的 SUPERSEDED 历史引用，移除不再适用于当前方案的失败明细。
这是减少过时信息对决策的干扰，模型仍自行选择何时完成验证；不保证模型不会无效查询。

### 模型、请求压缩、预算

本次在线使用 fast=`qwen3.8-flash`、strong=`qwen3.8-max`，temperature=0，enable_thinking=false，严格 JSON Schema。
模型名来自配置/环境变量；FAST_POLICY_MODEL / FAST_MODEL_NAME 与 STRONG_POLICY_MODEL / STRONG_MODEL_NAME 均支持，
也可指向同一模型。选型及格式参考 [阿里云 Flash 文档](https://help.aliyun.com/zh/model-studio/qwen3-8-flash)
和 [结构化输出](https://help.aliyun.com/zh/model-studio/qwen-structured-output)，已实际调用验证。

复杂共享候选、scope 扩展、Validator 反馈、多非支配策略及连续非法动作会升级 Strong；Router 不输出策略。
请求只保留当前必要事实、选项和最近 observation；首轮静态任务说明后续省略。
工具短说明和 response_format 中的严格参数 schema 分开，避免重复嵌入整个 schema。
PromptSizeMetrics 分开记录 system、tool、action schema、context、history 和估计 token；云 usage 是实际 token 来源。

| 模式 | 实际边界 |
|---|---|
| adaptive_agent | 正常模型策略；总预算 120 s，单决策准备+模型预算 15 s；默认不 fallback |
| bounded_agent | 默认最多 2 次模型尝试、5 s 策略预算；未完成则在剩余总预算中显式 baseline fallback |
| deterministic_realtime | 直接 baseline，零模型调用；不冒充 Agent 成功 |

超时模型动作不应用；已进入的有界求解/事务不强杀。预算是合作式限制，非硬实时 SLA。
bounded 的 5 秒只约束策略段，最终总耗时包含 fallback，后者也可能失败。
结果分列 pure_agent_success、fallback_success、overall_success 和各段耗时。

## 3. 评测方法与证据完整性

四类输入沿用 P5：SC01 U17、SC01 U17+U21、route-only、Validator 反馈诊断。
每次新建独立会话，并在相同初始世界 digest 上运行独立 baseline。D 在所有 profile 中都注入同一个
由 P3 生成的局部可行诊断 Proposal，构造耗时在 Agent 计时外；不声称这个坏初始方案是模型原创。

正式优化配置 C 预设 20/20/20/10，共 70 次；真实消融 B 预设 10/10/10/5，共 35 次。
离线 A/B/C 各 70 次用于协议和调用数对照，不能评价真实模型智能。
A=original；B=snapshot（稳定 ID、显式验证/提交、不拒绝 dominated）；C=optimized（增加 Guard 和自动 finalize）。
B/C 共用压缩 context 和 Fast/Strong router，所以它们不是对 Router 的单独消融。

每条 trial 包含基线、实际动作、选项集、真实观察、decision record、模型 usage 和全部耗时。
manifest 保存预设样本数、配置、源码 hash 和开始/结束时间。既有输出目录拒绝覆盖。
其中 config 是加载的基础配置，实际运行按顶层 profiles/execution_mode 覆盖；评测正常路径关闭可选 fallback，
bounded 模式仍按其显式预算语义触发 fallback。
所有成功和失败都纳入总体 p50/p95，p95 使用 nearest-rank；纯 Agent 成功耗时另列。
云 API 网络与推理在客户端只能合计为 model_total_ms，不能凭空拆成两个独立实测值。

扰动只在双方均提交时按原向量字典序比较，不求和；任何一方未提交则 not_comparable。
最终验证成功率按 trial 的最终 validation_status 统计；invalid/rejection 按模型决策次数统计；
DominatedChoiceRate 按尝试选择选项次数统计，即使 B 不阻止选择，也记录是否选中当时被支配的选项。
Mock 的实际 API token 为零，字符估计不冒充云 token。

### 开发期发现的失败，全部保留

| 批次 | 真实结果与用途 |
|---|---|
| `phase51-online-pilot` | A 成功，B/C/D 未成功；发现动作字段组合歧义，补充精确提示和 profile 独立指令 |
| `phase51-schema-pilot`, `phase51-finalization-pilot` | 字段/最终化修复后的 B、C、D 冒烟；不是正式统计 |
| `phase51-online-c` | 完整 70 次，A/B/C 60/60，D 5/10；暴露 scope-only 改 proposal_id 后丢失失败方案关联 |
| `phase51-lineage-pilot` | D 3/3 成功，但调用数 12/4/4；补充具体 schema 错误字段反馈并要求简短理由 |
| `phase51-schema-details-pilot` | D 1/1、2 次调用的冒烟 |
| `phase51-online-c-final` | 完整 70 次，A/B/C 60/60，D 6/10；部分已换 U2 却把历史 FAIL 当成当前失败，反复 refresh |
| `phase51-online-c-verified` | 完整 70 次，A/B/C 60/60，D 8/10；仅加历史标记仍有两次未提交、一次 10 次调用 |
| `phase51-online-c-release` | 进一步从当前 Delta Context 移除过时失败明细后，全新完整 70 次；正式最终版本统计来源 |

另保留早期 `phase51-pilot-a.json`。上述失败没有删除，也不并入最终版本成功率来混淆不同实现。
最后一轮是重新按预设样本数连续执行全部案例，不是抽取成功样本替换失败样本。

## 4. 测试与边界

完整回归 **262 passed**：原 Phase 1–5 的 227 项继续通过，新增 35 项。
保留一条现有 Starlette/httpx 依赖弃用提示。执行命令：

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
```

覆盖稳定 ID、陈旧版本/修订和 discard 不复活、只读确定性 Snapshot、context 截断、真实支配与非支配高层策略、
共享唯一候选保护、拒绝后模型重选、reason/argument 诊断、自动验证提交及关闭自动提交、Validator 反馈重规划、
scope-only 失败关联、替换后历史反馈、零效果选项、Fast/Strong、预算、fallback、并发 stale、API 与统计失败分母。

成功通常为 PASS_WITH_LIMITATIONS：travel_time、transfer_time、dynamic_energy、formation_geometry、
dynamic_scheduling 仍未评估。没有扩大 P4 的保证范围。
模型滚动别名、有限固定案例、开发机及云服务波动限制外推；temperature=0 也不保证每次策略相同。
理由一致性是节点 ID 的文字诊断，否定或历史提及可能误报；没有提到 ID 则未评估，执行始终以结构化参数为准。
内存存储、单 worker、非流式 API 等原有边界不变。

## 5. 重复基准统计与结论

本节在所有预设在线试验完成后根据保存的原始证据生成。

### 5.1 最终配置 C：70 次真实运行

[原始逐条证据](evidence/phase51-online-c-release/trials.jsonl) · [统计](evidence/phase51-online-c-release/summary.json) · [配置与源码版本](evidence/phase51-online-c-release/manifest.json)

| Case | n | 纯 Agent 成功 | 最终验证成功 | 调用 p50/p95 | 总时延 p50/p95 (s) | 最大 (s) | 成功且 <5s |
|---|---|---|---|---|---|---|---|
| A | 20 | 100.0% | 100.0% | 1.00 / 1.00 | 4.12 / 8.52 | 8.57 | 60.0% |
| B | 20 | 100.0% | 100.0% | 2.00 / 2.00 | 7.61 / 13.42 | 13.88 | 0.0% |
| C | 20 | 100.0% | 100.0% | 1.00 / 1.00 | 2.97 / 8.00 | 8.06 | 60.0% |
| D | 10 | 100.0% | 100.0% | 3.00 / 5.00 | 8.31 / 18.15 | 18.15 | 0.0% |

| Case | 算法核心 p50/p95 (ms) | API 累计 p50/p95 (s) | 工具调用 p50/p95 | Solver p50/p95 | 验证重试 p50/p95 |
|---|---|---|---|---|---|
| A | 133.74 / 143.73 | 3.97 / 8.37 | 3.00 / 3.00 | 3.00 / 3.00 | 0.00 / 0.00 |
| B | 369.16 / 378.68 | 7.21 / 13.02 | 4.00 / 4.00 | 9.00 / 9.00 | 0.00 / 0.00 |
| C | 6.73 / 7.61 | 2.96 / 7.96 | 3.00 / 3.00 | 1.00 / 1.00 | 0.00 / 0.00 |
| D | 19.44 / 32.10 | 8.28 / 18.11 | 6.00 / 10.00 | 6.00 / 12.00 | 1.00 / 1.00 |

算法核心包含候选准备、context 编译、工具、验证及提交；总时延还包括动作解析、工作区/审计开销。API 累计是每次请求的网络与服务端耗时之和，不是单次调用时延。

| Case | Invalid action | Dominated 拒绝 | DominatedChoiceRate | 理由警告数 | Equal / better / worse / 不可比 | Input / output tokens（全组） |
|---|---|---|---|---|---|---|
| A | 0.0% | 0.0% | 0.0% | 0 | 20 / 0 / 0 / 0 | 53354 / 1847 |
| B | 0.0% | 0.0% | 0.0% | 0 | 20 / 0 / 0 / 0 | 142243 / 3764 |
| C | 0.0% | 0.0% | 未选择 option | 0 | 20 / 0 / 0 / 0 | 26660 / 1355 |
| D | 0.0% | 0.0% | 0.0% | 6 | 0 / 0 / 0 / 10 | 54124 / 2599 |

本组正常 Agent 模式关闭 fallback；fallback rate 与 fallback success 均为 0，overall success 等于 pure Agent success。D 的 baseline 无法提交，因此不把其未提交低成本向量当作更优解。

实际模型路由次数：

| Case | 模型调用分布 |
|---|---|
| A | {"qwen3.8-flash": 20} |
| B | {"qwen3.8-max": 20, "qwen3.8-flash": 20} |
| C | {"qwen3.8-flash": 20} |
| D | {"qwen3.8-max": 30} |

### 5.2 真实 B/C 消融

[B 原始证据](evidence/phase51-online-b-final/trials.jsonl) · [B 统计](evidence/phase51-online-b-final/summary.json)。B 为 35 次、C 为 70 次，按不同配置连续运行，没有并发压测；样本数量不同，云时段波动未控制。

| 配置 | Case | n | 成功率 | 模型调用 p50/p95 | 时延 p50/p95 (s) | Invalid | 被支配选择率 | 扰动 worse |
|---|---|---|---|---|---|---|---|---|
| B | A | 10 | 100.0% | 4.50 / 8.00 | 11.67 / 28.66 | 15.2% | 0.0% | 0 |
| B | B | 10 | 100.0% | 4.00 / 9.00 | 10.46 / 22.07 | 8.2% | 0.0% | 0 |
| B | C | 10 | 100.0% | 5.50 / 11.00 | 13.94 / 25.96 | 17.2% | N/A | 0 |
| B | D | 5 | 100.0% | 5.00 / 8.00 | 11.97 / 18.94 | 3.7% | 0.0% | 0 |
| C | A | 20 | 100.0% | 1.00 / 1.00 | 4.12 / 8.52 | 0.0% | 0.0% | 0 |
| C | B | 20 | 100.0% | 2.00 / 2.00 | 7.61 / 13.42 | 0.0% | 0.0% | 0 |
| C | C | 20 | 100.0% | 1.00 / 1.00 | 2.97 / 8.00 | 0.0% | N/A | 0 |
| C | D | 10 | 100.0% | 3.00 / 5.00 | 8.31 / 18.15 | 0.0% | 0.0% | 0 |

B 同样看到真实成本并使用新的语义接口。即使两组都没有被支配选择，也只能说明这些试验未触发坏选择，不能单凭结果证明 Guard 的独立因果收益；强制坏选项的回归测试验证了 Guard 的实际阻止作用。

### 5.3 Mock 完整 A/B/C 消融

[210 条原始证据](evidence/phase51-mock-ablation/trials.jsonl) · [统计](evidence/phase51-mock-ablation/summary.json)。每个 profile 为 20/20/20/10，全部成功，API tokens=0。Mock 脚本用于验证机制，不声称它是模型决策。该批次在最后的历史反馈标注修复之前运行，正式版本由完整回归和上述真实基准验证。

| 配置 | Case | 模型调用 p50/p95 | 时延 p50/p95 (ms) | Solver p50/p95 |
|---|---|---|---|---|
| A | A | 5.00 / 5.00 | 81.90 / 93.29 | 1.00 / 1.00 |
| A | B | 8.00 / 8.00 | 134.68 / 139.60 | 2.00 / 2.00 |
| A | C | 4.00 / 4.00 | 16.93 / 17.29 | 1.00 / 1.00 |
| A | D | 8.00 / 8.00 | 37.31 / 63.29 | 1.00 / 1.00 |
| B | A | 3.00 / 3.00 | 178.62 / 189.91 | 3.00 / 3.00 |
| B | B | 4.00 / 4.00 | 408.61 / 454.49 | 9.00 / 9.00 |
| B | C | 3.00 / 3.00 | 15.22 / 15.65 | 1.00 / 1.00 |
| B | D | 4.00 / 4.00 | 30.52 / 71.74 | 6.00 / 6.00 |
| C | A | 1.00 / 1.00 | 140.75 / 186.72 | 3.00 / 3.00 |
| C | B | 2.00 / 2.00 | 400.14 / 413.87 | 9.00 / 9.00 |
| C | C | 1.00 / 1.00 | 11.37 / 12.03 | 1.00 / 1.00 |
| C | D | 2.00 / 2.00 | 27.10 / 84.16 | 6.00 / 6.00 |

没有云调用时，Snapshot 候选预取可能比原脚本路径更慢；这与线上减少模型回合带来的收益不矛盾。原脚本 Case B/C/D 调用数为 8/4/8，不等于历史真实模型的 5/3/14，不能混用为同一个对照组。

### 5.4 请求大小与 token

下表是每个模型决策的平均字符数（不是 token）；A 使用完整 Mock 原始 profile 的实测请求大小，B/C 使用真实请求。action schema 单独列出，原 A 的 system 内还有一份重复 schema。

| 配置/Case | system | tool | action schema | context | history | 估计 input tokens/调用 |
|---|---|---|---|---|---|---|
| A/A | 5319 | 4647 | 2852 | 3979 | 2983 | 6593 |
| A/B | 5319 | 4647 | 2852 | 4807 | 4843 | 7489 |
| A/C | 5319 | 4647 | 2852 | 1936 | 562 | 5105 |
| A/D | 5319 | 4647 | 2852 | 2996 | 2563 | 6126 |
| B/A | 3118 | 1777 | 2825 | 1471 | 372 | 3190 |
| B/B | 3118 | 1777 | 2825 | 2933 | 376 | 3678 |
| B/C | 3118 | 1777 | 2825 | 856 | 333 | 2971 |
| B/D | 3118 | 1777 | 2825 | 1414 | 460 | 3199 |
| C/A | 3329 | 1777 | 2825 | 4001 | 0 | 3986 |
| C/B | 3329 | 1777 | 2825 | 6028 | 193 | 4722 |
| C/C | 3329 | 1777 | 2825 | 1079 | 0 | 3012 |
| C/D | 3329 | 1777 | 2825 | 1732 | 468 | 3379 |

| 配置/Case | Input tokens/运行（均值） | Output tokens/运行（均值） | 全组 Input | 全组 Output |
|---|---|---|---|---|
| B/A | 7857.0 | 309.1 | 78570 | 3091 |
| B/B | 11134.5 | 358.2 | 111345 | 3582 |
| B/C | 8142.5 | 368.2 | 81425 | 3682 |
| B/D | 8998.2 | 351.6 | 44991 | 1758 |
| C/A | 2667.7 | 92.3 | 53354 | 1847 |
| C/B | 7112.1 | 188.2 | 142243 | 3764 |
| C/C | 1333.0 | 67.8 | 26660 | 1355 |
| C/D | 5412.4 | 259.9 | 54124 | 2599 |

token 来自成功返回的云 usage；异常未返回 usage 的请求无法补算实际 token。历史 P5 单次 total_tokens 为 A 28,537、B 40,136、C 11,442、D 75,801；模型及调用次数都变化，下降不能完全归因于 schema 压缩。

### 5.5 Baseline / deterministic_realtime

[70 条独立模式证据](evidence/phase51-deterministic-realtime/trials.jsonl)。零模型调用，fallback_used=false；这是显式 baseline 模式。其纯算法时间用总执行时间表示，不采用未记录候选预取字段的零值。

| Case | n | 提交成功 | 总时延 p50/p95 (ms) | 模型调用 |
|---|---|---|---|---|
| A | 20 | 100.0% | 43.98 / 59.83 | 0 |
| B | 20 | 100.0% | 92.15 / 100.18 | 0 |
| C | 20 | 100.0% | 7.51 / 7.78 | 0 |
| D | 10 | 0.0% | 7.61 / 8.08 | 0 |

A/B/C 上，Agent 在本批样本得到相同扰动，明显增加时延；不存在“Agent 一定优于 baseline”的结论。D 上 baseline 停在全局 FAIL，模型可根据真实反馈选择窗口覆盖更完整的 U2；价值在控制流程与反馈重规划，不是把更低但不可提交的成本算作收益。

### 5.6 Bounded 在线预算冒烟

[全部原始记录](evidence/phase51-bounded-online/trials.jsonl)。仅 2/2/2/1 个样本，用于展示预算与 fallback 标记，不作为成功率估计。

| Case | n | 纯 Agent 成功 | fallback 成功 | overall 成功 | fallback 使用 | 策略段 p50/p95 (s) | 总时延 p50/p95 (s) | fallback 耗时 p50/p95 (ms) |
|---|---|---|---|---|---|---|---|---|
| A | 2 | 100.0% | 0.0% | 100.0% | 0.0% | 3.84 / 4.88 | 3.84 / 4.88 | 未触发 |
| B | 2 | 0.0% | 100.0% | 100.0% | 100.0% | 5.03 / 5.04 | 5.12 / 5.12 | 81.95 / 82.15 |
| C | 2 | 100.0% | 0.0% | 100.0% | 0.0% | 2.51 / 2.96 | 2.51 / 2.96 | 未触发 |
| D | 1 | 0.0% | 0.0% | 0.0% | 100.0% | 5.04 / 5.04 | 5.04 / 5.04 | 4.00 / 4.00 |

fallback 成功不计入纯 Agent 成功。D 的原 baseline 无法修正全局失败，预算耗尽后允许出现 FALLBACK_FAILED；这份记录保留该边界。

### 5.7 实际动作与审计

**Case A，连续试验 #1**（只用于展示动作；全部样本均已计入统计）：

```text
1. qwen3.8-flash: SELECT_OPTION O_T03_466d22d6722f finalize=True
   members add=['U59'], remove=['U17']; strategy=IN_PLACE_REPAIR
   apply_reconstruction_option → APPLIED
   validate_proposal → PASS_WITH_LIMITATIONS
   commit_validated_proposal → SUCCESS
   result: commit_validated_proposal → SUCCESS
```

**Case B，连续试验 #1**（只用于展示动作；全部样本均已计入统计）：

```text
1. qwen3.8-max: SELECT_OPTION O_T03_8a145d22cf86 finalize=False
   members add=['U59'], remove=['U17']; strategy=IN_PLACE_REPAIR
   result: apply_reconstruction_option → APPLIED
2. qwen3.8-flash: SELECT_OPTION O_T04_c8d01463089c finalize=True
   members add=['U47'], remove=['U21']; strategy=IN_PLACE_REPAIR
   apply_reconstruction_option → APPLIED
   validate_proposal → PASS_WITH_LIMITATIONS
   commit_validated_proposal → SUCCESS
   result: commit_validated_proposal → SUCCESS
```

**Case C，连续试验 #1**（只用于展示动作；全部样本均已计入统计）：

```text
1. qwen3.8-flash: CALL_TOOL replan_route finalize=True
   replan_route → FEASIBLE
   validate_proposal → PASS_WITH_LIMITATIONS
   commit_validated_proposal → SUCCESS
   result: commit_validated_proposal → SUCCESS
```

**Case D，连续试验 #1**（只用于展示动作；全部样本均已计入统计）：

```text
1. qwen3.8-max: FINALIZE_PROPOSAL  finalize=True
   validate_proposal → FAIL
   result: validate_proposal → FAIL MEMBER_UNAVAILABLE,UNRELATED_TASK_REGRESSION
2. qwen3.8-max: CALL_TOOL get_current_state_summary finalize=False
   result: get_current_state_summary → OK
3. qwen3.8-max: SELECT_OPTION O_T03_3219d1b79e1c finalize=True
   scope: task:T06
   members add=['U2'], remove=[]; strategy=IN_PLACE_REPAIR
   request_scope_expansion → OK
   apply_reconstruction_option → APPLIED
   validate_proposal → PASS_WITH_LIMITATIONS
   commit_validated_proposal → SUCCESS
   result: commit_validated_proposal → SUCCESS
```

更改理由与参数的执行权测试验证：理由写 U2、结构化选项指向 U1 时，不按文字偷偷换节点，而记录警告。在线出现的警告需结合实际选项及否定/历史语境阅读，不能直接等同于真正执行错节点。

D 的真实一致性例证：试验 #2 第 2 步，option_id=`O_T03_3219d1b79e1c`，新增 `['U2']`；理由为“O_T03_e3fdf0c7e929 failed leaving T06 MEMBER_UNAVAILABLE; O_T03_3219d1b79e1c uses U2, remaining empty. Expand T06 scope.”，诊断 consistency=true。

D 的警告例证：试验 #3 第 2 步，理由“Replace plan with U2 (avoids T06 U1 conflict); expand scope to T06 for remaining violation.”。完整选项与参数见原始 trace；字面匹配不理解否定或历史引用。

### 5.8 Q1 / Q2 / Q3 与阶段判断

**Q1：明显高扰动 L3 是否改善？** 本次 Case B 20 次，成功率 100.0%，与 baseline 扰动相同 20 次、worse 0 次；调用 p50/p95=2.00 / 2.00。改善已经在指定样本上出现，Guard 按真实效果与成本拒绝坏选项，不硬编码 T04 必须 L1。

**Q2：option_index 和重复坏选择是否解决？** 新工具表已取消 index，陈旧 ID 有显式反馈。最终 D 成功率 100.0%，调用 p50/p95=3.00 / 5.00，总时延 p50/p95=8.31 / 18.15 s；原始 P5 单次为 14 次、62.36 s。scope-only 关联和旧反馈归属问题有独立回归覆盖；开发期失败批次仍保留。结果只支持这些案例的改善，不保证模型永不重复或无效查询。

最终 D 的终止码分布：`{"SUCCESS": 10}`。成功率与中位数不能掩盖最大调用数 5；2–4 次是理想目标，不是系统保证。

**Q3：距离真实 <5s 还差多少？** A 的 p50/p95=4.12 / 8.52 s，最大 8.57 s；C 为 2.97 / 8.00 s，最大 8.06 s。B 的 p95 比 5 秒多 8.42 s；D 多 13.15 s。不能宣称全场景或每次请求 <5s。主要瓶颈是云 API 网络/推理；若需要更稳定低时延，应继续评测更快云模型或本地 Policy，或明确选择 bounded Agent + baseline。后者不能保证 D 这类反馈任务成功，也不是严格 5 秒总 SLA。

**阶段判断：** 已具备进入 Phase 6 的接口、审计和可复现实验基础；尚不具备全场景低于 5 秒、全局最优或真实物理任务完备性的保证。本轮停在 Phase 5.1，等待下一步指令。
