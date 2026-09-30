# Phase 1 完成报告

日期：2026-09-24。范围：仓库检查、完整架构设计、数据模型、SC01 初态、基础 API。
没有进入 Phase 2，也没有实现或宣称完整的重构功能。

## 新增文件

| 文件 | 用途 |
|---|---|
| `docs/architecture.md` | 完整模块依赖、核心模型、接口、八阶段验收、逻辑缺口与调整 |
| `README.md` | 安装、启动、API 调用、测试与场景生成命令 |
| `pyproject.toml` | Python 包信息、依赖范围、pytest 设置 |
| `requirements-dev.lock` | 本轮验证用到的精确依赖版本 |
| `.gitignore` | 排除缓存、环境、密钥文件、后续运行产物 |
| `backend/app/models/domain.py` | 领域模型与结构完整性校验 |
| `backend/app/models/events.py` | 八类事件判别联合、场景定义与事件顺序校验 |
| `backend/app/models/api.py` | API 请求、快照、错误与可用性响应 |
| `backend/app/adapters/io.py` | 输入重新校验与输出深复制边界 |
| `backend/app/scenarios/manager.py` | JSON 目录加载、场景索引、会话隔离与快照读取 |
| `backend/app/api/routes.py` | 场景 API、错误映射、明确返回 501 的后续接口 |
| `backend/app/main.py` | FastAPI 应用工厂与 lifespan 初始化 |
| `scenarios/sc01_node_failure.json` | 显式 60/8/6 初始状态与两个待执行失效事件 |
| `scripts/build_scenario.py` | 使用局部 seed 的确定性场景生成器 |
| `scripts/demo_phase1.py` | HTTP 场景加载/查询/未实现状态最小示例 |
| `backend/tests/conftest.py` | 从正式 JSON 加载的共享测试夹具 |
| `backend/tests/test_models.py` | 领域与事件输入契约测试 |
| `backend/tests/test_scenarios.py` | 可复现性、初态一致性与会话隔离测试 |
| `backend/tests/test_api.py` | API、OpenAPI、错误响应与不修改状态测试 |
| `backend/tests/test_server_startup.py` | Uvicorn 默认协议加载、真实 CLI 启动与 TCP HTTP 回归测试 |
| 各包 `__init__.py` | Python 模块边界 |
| `docs/phase1-report.md` | 本阶段实施与验证记录 |

## 核心实现

- 数据模型与 HTTP 解耦；事件没有任意 dict 载荷。
- 结构层拒绝悬空引用、重复成员、未知能力、非法几何和非有限数值。
- 允许表示失效成员、未分配任务和过期航迹等不可执行状态，保留后续重构输入。
- 编队关系以 `Formation.node_ids` 为唯一事实来源，避免双向字段不一致。
- SC01 的任务窗口不重叠复用编队，初始路线端点一致；备用节点可由 seed 重现。
- 每次加载创建独立 session，所有返回值深复制；请求不直接拼接文件系统路径。
- 未实现接口返回 `PHASE_NOT_IMPLEMENTED`，不生成虚假 PASS 或修改场景。

## 验证结果

执行环境：Python 3.10.12，FastAPI 0.141.1，Pydantic 2.13.5，pytest 8.4.2。
依赖安装在 `/tmp/cluster-reconstruction-deps`，没有修改系统 Python。

```bash
PYTHONPATH=/tmp/cluster-reconstruction-deps:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python3 -m pytest -q
PYTHONPATH=/tmp/cluster-reconstruction-deps:. python3 -m scripts.demo_phase1
```

初次验证结果（启动修复前）：**47 passed, 1 warning in 0.41s**。
其中领域/事件/场景测试 38 项，API 测试 9 项。
这里的 0.41s 是测试执行时间，不是重构耗时，不能用来证明 `<5 s` 指标。

最小示例输出：

```text
Loaded SC01: 60 nodes, 8 tasks, 6 formations
seed=42, state.version=0, planned events=2
State round-trip: PASS
Reconstruction: HTTP 501 (not implemented in Phase 1)
```

测试核查：8 类事件序列化、无效载荷、非法引用、半开时间窗口、坐标范围、
重复编队成员、初始任务能力/数量/资源条件、U17/U21 的唯一中继角色、
固定 seed、全局随机数不受影响、会话和返回快照隔离、HTTP 404/422/501、
OpenAPI 的事件 discriminator。

## 当前未解决问题与阶段边界

1. 事件目标存在性和实际应用留在 P2；当前只校验事件载荷与时间序列。
2. TRDG、Assessment、Solver、Validator、Agent、持久化回放、指标和前端尚未实现。
   四场景中的 SC02–SC04 留到 P6，结果类型在负责阶段按架构契约实现。
3. 单进程内存会话没有持久化或自动过期，仅适合本地短期 Demo；不能多 worker 运行。
4. 暂无完整可行性校验器；SC01 初态一致性由测试检查，不代表生产级任务验证。
5. 尚未选择真实模型、提供 API Key 或进行在线调用，无重构性能数据。
6. 最新 Starlette TestClient 对 httpx 给出一条弃用提示。当前锁定组合全部测试通过，
   该提示未被屏蔽；后续升级测试客户端时需要一起验证兼容性。
7. 当前机器缺少 ensurepip，默认包下载代理拒绝连接；本轮通过隔离目录和直接包源
   完成安装。受限沙箱内 TestClient 跨线程启动阻塞，沙箱外同一测试正常通过。
8. 初始 `.git` 为空只读占位目录，不是有效仓库，本轮未创建提交。

下一阶段边界已确定为 EventInjector + TRDG + Impact Propagation + Task Assessment；
无需重写 Phase 1 的领域关系或 API 框架。

## 启动依赖修复

用户实际执行 Uvicorn CLI 后发现 `ImportError: cannot import name 'ServerProtocol'`。
复现确认：Uvicorn 0.53.0 自动加载 WebSocket 协议时，使用了系统路径下的
`websockets 9.1`；该版本没有它所需的接口。`PYTHONPATH` 只增加包搜索路径，
并不隔离系统包，原来的依赖目录缺少 WebSocket 包。之前的 47 项测试使用
TestClient，绕过了 Uvicorn 的协议初始化，因此不能证明实际启动成功。

定位对照：同一应用使用 `ws=none` 可加载；最小 ASGI 应用使用默认协议仍然失败。
新增的 `Config(...).load()` 子进程回归测试在修复前复现了相同 ImportError。

修复：`pyproject.toml` 增加 `websockets>=13,<16`，锁定文件固定为 15.0.1；
在原 `/tmp/cluster-reconstruction-deps` 目录补装，无需替换系统包。
增加真实 Uvicorn CLI 测试，使用操作系统分配的临时端口，检查 health、docs、
SC01 加载与状态读取，结束后自动关闭服务。README 说明了安装修复与 `--ws none`
临时绕过方式。此修复不改变领域模型或 Phase 1 业务范围。

修复后完整验证结果：**49 passed, 1 warning in 1.30s**。默认协议加载和实际
Uvicorn 子进程启动均通过；通过本地 TCP 验证了健康检查、文档页和场景加载/读取。
仍存在原来的 Starlette TestClient 弃用提示，与本次启动错误无关。
