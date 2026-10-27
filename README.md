# LumenX Prism

独立 AI 效果服务。当前能力：剧本理解中的角色、场景与道具提取。后续逐项扩展画风分析、分镜、提示词策略、视频增强与一致性评测，不代表这些能力已经实现。

## 项目边界

- 目录：`/home/zj/aigc/lumenx-prism`，独立 Git 仓库、虚拟环境、环境文件与发布生命周期。
- 当前 LumenX 后端通过 `POST /v1/scripts/extract-entities` 调用，Prism 不导入业务代码或直接修改项目/资产。
- AI 输出临时实体编号；正式 UUID、系列/服装变体关联、持久化和业务鉴权由 LumenX 负责。
- 原 LumenX 内的 `src/ai_service` 暂留为兼容副本，尚未删除；后续 AI 能力开发在 Prism 中进行。删除兼容副本需要后端改造与单独验收，不能直接移动目录让原业务导入失效。

## 本地开发

```bash
cd /home/zj/aigc/lumenx-prism
python3 -m venv .venv
.venv/bin/python -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt
# 从 .env.example 建立自己的 .env，配置自己的模型凭据及服务 token。
.venv/bin/python -m uvicorn lumenx_prism.api:app --app-dir src --env-file .env --host 127.0.0.1 --port 17178 --reload
```

调试页 http://127.0.0.1:17178/docs；`GET /health` 返回 service=lumenx-prism，仅表示存活。
模型凭据不从同级业务项目继承。`.env` 为本地受限文件，首次准备时未复制任何真实模型凭据；配置凭据后才能成功提取。

## HTTP 接口与接入

`POST /v1/scripts/extract-entities`：

```json
{"text":"叶墨走进书房，拿起一本书。","custom_extraction_prompt":""}
```

输出 `characters`、`scenes`、`props` 三个数组，字段契约见 `src/lumenx_prism/schemas.py`。文本为 1～80000 个字符，自定义提示词最多 30000 字符。自定义模板含 `{text}` 时替换，否则在末尾追加原文。

服务配置 `AI_SERVICE_API_KEY` 后，请求携带 `Authorization: Bearer <token>`。请求错误 422；服务鉴权错误 401；模型未配置 503；上游失败或非法输出 502。原始 provider 错误、脚本及密钥不会放入错误响应。

LumenX 后端如需接入，单独在它自己的私密配置中设置：

```dotenv
ENTITY_EXTRACTION_SERVICE_URL=http://127.0.0.1:17178
AI_SERVICE_API_KEY=与Prism匹配的服务token
```

当前没有自动改写 LumenX 的配置或线上服务。未配置远端时，LumenX 继续运行兼容实现。跨容器/机器需替换为业务可达地址，不能照抄回环地址。

## 数据库隔离

**当前实体提取接口无状态，不连接任何数据库，也不保存剧本或提取结果。** 独立项目未复制业务库、媒体文件和账号凭据。

为后续任务/实验记录预留了专用 MongoDB 编排 `compose.database.yml`：

| 项目 | Prism 专用值 |
|---|---|
| Compose 项目 | lumenx-prism |
| MongoDB 实例 | 独立 mongo:7.0 容器，不复用 CosyVoice/LumenX 实例 |
| 主机端口 | 127.0.0.1:27019，容器端口 27017 |
| 数据库 / authSource | lumenx_prism |
| 应用用户 | prism_app，仅有此库 readWrite |
| 数据卷 | lumenx-prism-mongodb-data |
| 配置来源 | Prism 自己的 .env 与 PRISM_* 变量 |

该数据库尚未启动，应用也未接入持久化。本机 WSL 尚无可用 Docker（未启用 Docker Desktop WSL 集成），当前只做了编排静态校验；需要可用 Docker/Compose 后才能启动。需要时先检查目标机器端口，设置独立的 root/app 密码，再手动运行：

```bash
docker compose --env-file .env -f compose.database.yml up -d
```

初始化脚本只在空数据卷执行。修改 `.env` 不会修改已有数据库用户密码，不删除数据卷来重置密码。root 仅用于初始化/维护，未来应用使用 prism_app；URI 密码需 URL 编码。不得填入 lumenxdb、tts_mongodb 或业务库连接串。

## VS Code 与测试

打开同级 `../lumenx-prism.code-workspace`，同时显示 `LumenX` 和 `LumenX Prism`；两个文件夹各自保有 Git 仓库和解释器。F5 可分别启动业务后端或 Prism，工作区任务可执行 Prism 测试。

```bash
.venv/bin/python -m pytest
```

测试覆盖 JSON/实体契约、鉴权、错误脱敏、provider fallback，以及复制应用到临时目录后启动真实 HTTP 服务对接本机模拟模型；不调用付费 API。代码不依赖 ../lumenx，不提交个人工作材料。

## 231 状态（2026-10-27）

仅原 `src/ai_service` 源码随业务项目同步到了231；检查时17178没有服务，业务后端也未配置远端提取。Prism 独立项目及专用数据库尚未部署到231，本次不自动部署或修改现有运行服务。
