# 公网发布指南

仓库包含一个多阶段 `Dockerfile`：Node 阶段构建 React/Vite，Python 阶段运行 FastAPI，生产环境由同一个端口提供页面和 `/api`，不依赖开发服务器代理。

## 1. 发布到 GitHub

`docs/API`、`.env`、`.python-deps`、`node_modules`、运行产物和构建产物均已忽略。发布前仍应执行：

```bash
git status --short
git check-ignore docs/API
```

创建空白 GitHub 仓库后，在项目根目录执行：

```bash
git init
git branch -M main
git add .
git commit -m "Initial public demo"
git remote add origin https://github.com/<账号>/<仓库名>.git
git push -u origin main
```

## 2. 部署到 Render

1. 登录 Render，选择 **New → Blueprint**，连接上述 GitHub 仓库。
2. Render 自动读取根目录的 `render.yaml`，用 Dockerfile 构建一个 Web Service。
3. 如需真实 Qwen Agent，在 Render 的 Environment 页面填写 `MODEL_API_KEY`。不要把值写进 `render.yaml`、Dockerfile 或 GitHub。
4. 部署完成后访问 Render 分配的 `https://<service>.onrender.com` 地址。

健康检查地址为 `/healthz`，API 文档地址为 `/docs`。当前服务使用内存会话和本地 JSON 回放；容器重启后活动会话清空，免费实例的本地回放文件也不保证持久保留。

## 3. 公网模型调用边界

在完全公开且没有登录、限流和配额保护的站点中配置模型密钥，会允许访客消耗该密钥的模型额度。公开技术预览建议先不设置 `MODEL_API_KEY`，主要展示确定性实时模式；需要开放真实 Agent 时，应先增加身份验证、速率限制和每日调用额度。
