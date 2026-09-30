# 集群任务重构智能体 Demo

这是一个可在普通电脑上运行的二维集群任务重构演示系统。它提供固定场景 SC01–SC08 和随机动态任务两种入口，展示事件注入、TRDG 依赖传播、影响评估、最小扰动重构、全局约束校核、事务提交与历史回放。**不用购买云服务器，也不用配置模型 API 密钥，就能运行完整的确定性演示。**

仓库包含 React/Vite 前端、FastAPI 后端、场景数据、自动测试和技术报告。当前版本为 Phase 6.2；二维运动和任务进度是任务级仿真，不表示真实飞控、物理动力学或高保真无人机仿真。

## 从 GitHub 下载并运行

### 环境要求

| 项目 | 要求 |
|---|---|
| 操作系统 | Ubuntu/Linux、macOS；Windows 建议使用 WSL2 Ubuntu |
| Python | 3.10 或更新版本，带 `venv` 和 `pip` |
| Node.js | 20.19+ 或 22.12+；附带 npm |
| 网络 | 首次安装依赖需要访问 Python 和 npm 软件包源；离线确定性演示运行时不需要模型网络 |

下面的命令在 Linux/macOS 终端或 WSL2 Ubuntu 中执行。Windows 原生 PowerShell 用户可先安装 WSL2，再在 WSL 终端操作；不要把 `bash scripts/run_demo.sh` 当作 PowerShell 命令执行。

```bash
git clone https://github.com/D1fficultChip/swarm-refrastructure-agent.git
cd swarm-refrastructure-agent

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock

cd frontend
npm ci
cd ..

bash scripts/run_demo.sh
```

终端显示 `Demo: http://127.0.0.1:5173` 后，在**同一台运行程序的电脑**上打开 <http://127.0.0.1:5173>。API 文档在 <http://127.0.0.1:8000/docs>。运行期间保持终端打开；按 `Ctrl+C` 同时停止前后端。下次开机后，进入仓库并执行：

```bash
source .venv/bin/activate
bash scripts/run_demo.sh
```

若 `python3 -m venv .venv` 报 `ensurepip is not available`，在 Ubuntu 安装对应版本的 `python3-venv` 包后重试。不能使用 venv 时，也可把依赖安装在仓库内的持久目录：

```bash
python3 -m pip install --target .python-deps -r requirements-dev.lock
cd frontend && npm ci && cd ..
bash scripts/run_demo.sh
```

`.python-deps`、`.venv`、`frontend/node_modules`、运行结果及 API 密钥均被 Git 忽略，不需要上传。

### 可选：使用自己的 Qwen API 密钥

确定性实时模式不需要密钥。若希望体验模型驱动的 Agent Policy，需自行准备兼容配置的模型 API 密钥，并在**本机**创建 `docs/API` 文件，文件内容只放一行密钥；Linux/macOS 下执行 `chmod 600 docs/API`。启动脚本会在后端进程中读取该文件。也可在启动终端设置 `MODEL_API_KEY` 环境变量；`.env.example` 是变量示例，程序不会自动加载 `.env`。

模型名称、接口地址和超时配置在 [config/agent.json](config/agent.json)。使用 Agent 模式会向配置的模型服务发出请求，可能产生费用；不配置密钥而选择 Agent 模式时，页面会提示模型未配置，切换回“实时确定性模式”即可。不要提交 `docs/API`、`.env` 或把密钥写入前端源码。

## 页面怎么用

### A. 固定场景：第一次体验推荐 SC08

打开首页后保持默认的 **SC08 · 综合态势突变** 和 **实时确定性模式**。点击 **一键综合演示**，系统自动加载场景、注入预设事件、运行重构并提交。想观察各阶段时，按页面顺序操作：

1. 点击 **加载场景**，查看任务、编队、节点和原航迹。
2. 点击 **1 注入事件**；在 TRDG 与任务评估中查看哪些任务被影响、传播路径是什么。
3. 点击 **2 查看影响原因**，再点击 **3 运行重构**。页面展示候选筛选、工具执行和方案变化。
4. 若开启“分步讲解”，候选方案完成后还没有改动现场；点击 **4 校核并提交** 才会事务化发布。**一键综合演示**则自动完成校核和提交。
5. 在地图切换“态势变化后 / 重构前”“重构后方案”“叠加对比”，并在底部“真实运行回放”选择历史记录。

这里的 `8/8 规划约束可行` 表示当前 8 项任务都通过了系统已实现的硬约束检查，不等于真实飞行任务已经完成。`PASS_WITH_LIMITATIONS` 表示已建模约束通过，同时保留对未建模物理因素的说明。

### B. 动态场景：连续事件与多轮重构

点击右上角 **动态任务演示**。首次体验建议把“事件模式”设为**手动**，“重构模式”设为**实时确定性模式**，保留默认的 60 节点、8 任务、L3 和 2× 仿真速度：

1. 点击 **生成新任务场景**，然后点击 **开始运行**。地图上的节点沿航迹运动，右侧显示任务进度。
2. 在“运行时态势注入”选择 L1 并点击 **生成动态事件**，观察受影响但仍可继续的任务。
3. 选择 L2 或 L3，再点击 **生成动态事件**；TRDG 显示“节点→编队→能力→任务”和可能的“区域→航迹→任务”传播链。
4. 若出现未解决的约束，点击 **重构并校核提交**。成功后页面展示该轮的方案变更、算法执行和 Validator 结果，任务继续推进。
5. 可以再次生成事件、再次重构；用 **暂停 / 继续 / 单步 +1s** 控制讲解节奏，并在“历史真实动态任务回放”查看检查点。

初始任务的时间窗为 600 仿真秒，有效执行量为 300 仿真秒，余量用于容纳事件阻塞和重构。右侧“超时未完成（仿真）”表示超过该任务时间窗时进度仍不足 100%；它与“当前规划约束可行”是不同指标。动态页默认模型模式是 Agent；没有配置模型密钥时，请在生成场景前切换成**实时确定性模式**。

保存的回放文件位于 `artifacts/`。当前活动会话保存在单个后端进程内，重启服务后需要重新加载或生成场景；已写入磁盘的回放文件仍可读取。前端和后端应在同一台机器运行，`127.0.0.1` 仅供本机访问，甲方在自己电脑上按以上步骤安装后即可使用。

## 部署到甲方自己的服务器（可选）

项目也能部署成由甲方维护的固定链接。当前 [backend/app/main.py](backend/app/main.py) 在存在 `frontend/dist` 时，会由 FastAPI 同时提供页面和 `/api`，因此生产环境只需一个后端端口。最小部署流程如下：

1. 在服务器克隆**不含密钥**的仓库，创建 Python venv，并安装 `requirements-dev.lock` 或项目运行依赖。
2. 在 `frontend/` 执行 `npm ci && npm run build`；也可在开发机先构建，再把 `frontend/dist` 上传服务器。
3. 从仓库根目录运行 `python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8000 --workers 1`。先在服务器本机检查 `/healthz` 和首页。
4. 使用 systemd 托管后端进程以便开机自启；由 Nginx 将域名的 HTTPS 请求转发到本机 `8000` 端口。公网只开放 80/443，不直接开放 8000。
5. 将模型密钥放在服务器环境变量中；若甲方允许多人访问 Agent 功能，应增加访问控制和调用限额。
6. 将 `artifacts/` 放到可持久保存、可备份的位置。系统只支持**一个 Uvicorn worker**：会话是进程内状态，多个 worker 会导致随机的“会话不存在”。服务重启会清空活动会话，但不会自动删除磁盘中的回放 JSON。

仓库还保留 [Dockerfile](Dockerfile) 与 [Render 配置](render.yaml) 作为可选托管方案；**甲方按本 README 在自己的电脑运行时无需 Docker，也无需云服务器**。更详细的公网发布边界见 [公网发布指南](docs/public-deployment.md)。

## 验证与排错

在仓库根目录、已激活 venv 的终端执行：

```bash
python -m pytest -q
cd frontend && npm test && npm run build
```

常见情况：

| 现象 | 处理 |
|---|---|
| 打不开 `127.0.0.1:5173` | 确认启动终端仍在运行；查看 Vite 是否报告端口占用或安装失败 |
| 页面出现“场景列表读取失败” | 检查后端是否在 `127.0.0.1:8000` 启动，并打开 `/healthz` 验证 |
| 运行 Agent 提示 `MODEL_NOT_CONFIGURED` | 配置自己的模型密钥，或选择“实时确定性模式” |
| `No module named uvicorn` 等缺包 | 激活 `.venv` 后重新安装依赖；不要依赖重启后可能被清理的 `/tmp` |
| `websockets.server` 导入错误 | 使用锁定依赖重新安装；其中包含兼容的 `websockets==15.0.1` |
| 页面显示任务“超时未完成” | 这是仿真时间窗结果，可重新生成场景或降低速度、在事件后及时重构 |

## 系统结构与资料

核心流程：`动态/固定场景 → EventInjector → TRDG 影响传播 → 任务评估 → Agent Policy 或确定性 baseline → Proposal → Global Validator → 事务提交 → 持续执行/回放`。Agent 以细粒度确定性工具构造方案；模型不能直接修改权威规划状态。Runtime State 持续更新位置和进度，Planning State 仅在任务相关变更时更新版本。

- [动态任务演示操作指南](docs/dynamic-demo-guide.md)
- [Demo 分步讲解与场景说明](docs/demo-guide.md)
- [动态环境设计与已知边界](docs/dynamic-environment.md)
- [Phase 6.2 完成报告](docs/phase6.2-report.md)
- [Agent 架构与工具契约](docs/agent-architecture.md)
- [完整系统架构与接口](docs/architecture.md)
- [独立能力接口与 baseline / Agent Policy 边界](docs/primitive-interfaces.md)
- [SC08 示例结构化结果](docs/evidence/phase6/final-sc08.json)

本系统已实现任务、编队、能力、资源、时间窗和航迹等规则下的全局校核。飞行转场时间、动态能耗、队形几何稳定性、物理碰撞和真实通信链路不在当前验证范围内；论文或演示中应明确区分已实现约束与未建模条件。
