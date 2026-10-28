# LumenX Prism

独立 AI 效果服务。当前能力：剧本理解中的角色、场景与道具提取。后续逐项扩展画风分析、分镜、提示词策略、视频增强与一致性评测，不代表这些能力已经实现。

## 项目边界

- 目录：`/home/zj/aigc/lumenx-prism`，独立 Git 仓库、Conda 环境、环境文件与发布生命周期。
- LumenX 后端支持通过 `POST /v1/scripts/extract-entities` 调用 Prism；仅在配置远端地址后启用，默认仍使用业务仓库内兼容实现。Prism 不导入业务代码或直接修改项目/资产。
- AI 输出临时实体编号；正式 UUID、系列/服装变体关联、持久化和业务鉴权由 LumenX 负责。
- 原 LumenX 内的 `src/ai_service` 暂留为兼容副本，尚未删除；后续 AI 能力开发在 Prism 中进行。删除兼容副本需要后端改造与单独验收，不能直接移动目录让原业务导入失效。

## 本地开发

```bash
cd /home/zj/aigc/lumenx-prism
conda activate lumenx_prism
python -m pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements-dev.txt
# 从 .env.example 建立自己的 .env，配置自己的模型凭据及服务 token。
python -m uvicorn lumenx_prism.api:app --app-dir src --env-file .env --host 127.0.0.1 --port 17178 --reload
```

实体提取测试台 http://127.0.0.1:17178/tools/entities （根路径 `/` 也可打开）；API 文档 http://127.0.0.1:17178/docs；`GET /health` 返回 service=lumenx-prism，仅表示存活。
模型凭据不从同级业务项目继承。`.env` 为本地受限文件。使用 OpenAI 兼容服务时配置 `LLM_PROVIDER=openai`、`OPENAI_API_KEY`、`OPENAI_BASE_URL`、`OPENAI_MODEL`；修改环境文件后需要重启进程，`--reload` 不保证重新读取 `.env`。

## HTTP 接口与接入

`POST /v1/scripts/extract-entities`：

```json
{"text":"叶墨走进书房，拿起一本书。","custom_extraction_prompt":""}
```

输出 `characters`、`scenes`、`props` 三个数组，字段契约见 `src/lumenx_prism/schemas.py`。文本为 1～80000 个字符，自定义提示词最多 30000 字符。自定义模板含 `{text}` 时替换，否则在末尾追加原文。

服务配置 `AI_SERVICE_API_KEY` 后，请求携带 `Authorization: Bearer <token>`。请求错误 422；服务鉴权错误 401；模型未配置 503；上游失败或非法输出 502。原始 provider 错误、脚本及密钥不会放入错误响应。

调用流程：请求校验与服务 token 鉴权 → 构建默认/自定义提示词 → OpenAI 兼容模型接口 → JSON 解析 → Pydantic 校验 → 返回推理 DTO。支持纯 JSON 和完整 Markdown 代码围栏；重复 JSON 字段、NaN/Infinity、缺失数组、非法实体及重复引用编号返回 `502 invalid_model_output`。不自动修补截断输出或重新发起推理。结构校验不能识别语义幻觉，仍需样本评测。

默认提示词仅提取原文明示信息，未知属性留空；服装变体使用 `原名 (服装)` 命名，供业务端关联。自定义提示词会替换默认规则，使用时需要自行保留这些约束。

LumenX 后端如需接入，单独在它自己的私密配置中设置：

```dotenv
ENTITY_EXTRACTION_SERVICE_URL=http://127.0.0.1:17178
AI_SERVICE_API_KEY=与Prism匹配的服务token
```

当前没有自动改写 LumenX 的配置或线上服务。未配置远端时，LumenX 继续运行兼容实现。跨容器/机器需替换为业务可达地址，不能照抄回环地址。

## 测试页面

1. 启动服务后打开 `/tools/entities`，输入 Prism 自己的 `AI_SERVICE_API_KEY`，点击“连接服务”。令牌仅留在页面内存，不写入浏览器存储；服务端模型密钥不返回浏览器。
2. 粘贴剧本或“载入中文样本”，可展开自定义提示词。只有点击“提取实体”或按 Ctrl/⌘+Enter 才会调用真实模型，可能产生费用。加载页面、样本、配置与历史不会调用模型。
3. 检查角色/场景/道具卡片与原始 JSON；历史支持状态筛选、分页、详情和恢复输入。恢复输入不会提交。
4. 历史与提取沿用同一 Bearer token 鉴权。未设置 token 时这些接口均无鉴权，请保持回环监听。页面无外部 CDN 依赖。

## 数据库与实验历史

按当前部署方案，Prism 使用现有 MongoDB 实例，以独立数据库 `lumenx_prism` 和账号 `prism_app` 做数据库级隔离，不启动单独 MongoDB 容器。连接信息只放 Prism 自己的 `.env`：

```dotenv
PRISM_MONGODB_URI=mongodb://prism_app:<URL_ENCODED_PASSWORD>@<mongo-host>:27017/lumenx_prism?authSource=lumenx_prism
PRISM_MONGODB_DATABASE=lumenx_prism
```

应用仅允许选择 `lumenx_prism` 数据库。`authSource` 指定用户认证库；URI 密码需要 URL 编码。安装依赖后，首次有效提取会自动创建 `entity_extraction_runs` 集合及时间排序索引，不需要手动创建表或运行初始化脚本。

记录格式 `schema_version=1`：UUID 作为实验编号（不是业务实体 UUID），UTC 创建/完成时间、`running/succeeded/failed` 状态、正文与自定义提示词、实际组装提示词及 SHA256、provider、请求配置模型、耗时、校验后结果或安全错误码。`requested_model` 是请求配置，并不保证代表回退链最终使用的模型。不记录鉴权头、模型密钥、连接串或原始 provider 异常。正文和提示词为完整明文，保留至运维按自身策略清理；当前无自动过期、历史删除或业务项目写入。

- 配置 URI 后：先持久化通过校验的输入，再调用模型。初始写入失败返回 `503 history_unavailable`，不会调用模型。非法输入和未授权请求不留历史。
- 完成后更新结果；若回写失败，保留原输入及 `running` 状态，并在原响应增加 `X-Prism-History-Warning: finalize_failed`，不丢弃已生成结果、不重试模型。请复制结果留存。服务中断也可能留下 `running` 记录，该状态不表示后台任务一定仍在执行。
- 未配置 URI：原提取接口继续工作，但不保存历史，页面明确提示。历史接口返回 `503 history_not_configured`。配置错误返回 `503 history_configuration_error`。
- 原接口成功响应结构仍为三个实体数组；已创建记录时增加响应头 `X-Prism-History-Id`，便于关联成功或失败请求。

历史查询（均需相同服务 token）：

```text
GET /v1/scripts/extract-entities/history?limit=20&offset=0&status=succeeded
GET /v1/scripts/extract-entities/history/{id}
GET /v1/test-console/config
```

列表每页1～100条，offset 0～10000；`status` 可省略，或为 `running`、`succeeded`、`failed`。列表仅返回摘要，详情返回完整输入/提示词/结果，返回 `Cache-Control: no-store`。不存在的记录返回404。配置接口只返回模型名、provider、配置是否存在，不返回密钥或 provider 地址；配置存在不代表上游在线。

## VS Code 与测试

打开同级 `../lumenx-prism.code-workspace`，同时显示 `LumenX` 和 `LumenX Prism`；两个文件夹各自保有 Git 仓库和解释器。F5 可分别启动业务后端或 Prism，工作区任务可执行 Prism 测试。

安装 VS Code Python、Python Debugger 扩展。在 Prism 文件中执行 `Python: Select Interpreter`，选择 Conda 环境 `lumenx_prism` 的 Python。本项目 `.vscode/settings.json` 已配置 pytest 和 `src` 分析路径。

- 启动：终端执行上面的 uvicorn 命令，或运行任务 `Prism: dev server`，仅监听 `127.0.0.1:17178`。
- 调试：在运行和调试下拉框选择 `Prism: FastAPI (17178)` 再按 F5；先停止占用该端口的开发服务。在 `api.py` 的 `extract_entities`、`extraction.py` 的 `extract` / `parse_entity_output` 设置断点。此配置读取 Prism 自己的 `.env`；不要选择业务后端配置。
- 测试：运行任务 `Prism: tests`，或使用 Testing 面板运行/调试测试；后者可逐步跟踪模拟模型，无需配置模型密钥。工作区文件为本机配置，不属于此仓库。
- `/docs` 中配置了服务 token 时须先点 Authorize；提交提取请求会调用已配置的真实 provider，不能用作免费冒烟检查。`/health` 不检查模型可用性。

```bash
python -m pytest
# 只运行中文样本回放与解析回归（无需真实模型）
python -m pytest tests/test_chinese_sample.py tests/test_output_parsing.py
```

测试覆盖 JSON/实体契约、鉴权、错误脱敏、provider fallback、模拟历史仓库及控制台资源，以及复制应用到临时目录后启动真实 HTTP 服务对接本机模拟模型；不调用付费 API。自动化测试屏蔽真实 MongoDB URI。代码不依赖 ../lumenx，不提交个人工作材料。

中文剧本、参考实体及评测标准见 [实体提取评测](docs/entity-extraction-evaluation.md)。测试不会自动加载 `.env`；依赖包括 `pymongo`，安装命令见上文。部分受限沙箱无法运行 TestClient/回环 HTTP 测试，应在允许本机回环通信的终端执行。运行时默认模型顺序由 `llm_adapter.py` 定义；测试仅验证回退行为，不确认这些模型在线可用。

## 环境边界

231 为测试环境，生产为另行管理的 LumenX 服务。Prism 的页面与历史功能在当前仓库内运行，不会自动部署或切换 LumenX 业务调用。业务仓库的 `src/ai_service` 仍有导入依赖，不能直接删除。
