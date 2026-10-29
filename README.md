# LumenX Prism

独立 AI 效果服务。当前能力：剧本实体提取，以及基于 ComfyUI 的角色、场景、道具图片异步生成。后续逐项扩展画风分析、分镜、提示词策略、视频增强与一致性评测，不代表这些能力已经实现。

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
# 从 .env.example 建立自己的 .env，配置自己的 MongoDB 连接及服务 token。
python -m uvicorn lumenx_prism.api:app --app-dir src --env-file .env --host 127.0.0.1 --port 17178 --reload
```

另开一个终端启动独立 worker（API 进程不会自动启动 worker）：

```bash
cd /home/zj/aigc/lumenx-prism
conda activate lumenx_prism
PYTHONPATH=src python -m lumenx_prism.worker --env-file .env
```

worker 默认每秒查队列，每进程同时执行一个任务。可运行多个 worker，MongoDB 原子领取保证同一任务只被一个有效持有者执行；图片任务使用资源组互斥及 ComfyUI 队列预检，尚无通用多 GPU 容量调度。Ctrl+C / SIGTERM 停止领取新任务，等待当前推理与结果保存后退出。不要为 worker 开启自动 reload。

实体提取测试台 http://127.0.0.1:17178/tools/entities （根路径 `/` 也可打开）；API 文档 http://127.0.0.1:17178/docs；`GET /health` 返回 service=lumenx-prism，仅表示存活。
模型配置页为 http://127.0.0.1:17178/tools/models 。模型名称、API key、API 地址及参数全部保存到 MongoDB 的 `model_comfigs` 集合。`.env` 配置 MongoDB 连接、服务 token、媒体目录和公开服务地址；运行时不读取 `OPENAI_*`、`DASHSCOPE_*` 或 `LLM_PROVIDER`，也不会在数据库出错时回退到环境变量。数据库中的模型修改对新提交即时生效，不需要重启；修改数据库连接或服务 token 仍需重启进程。

## 模型配置

打开 `/tools/models`，输入 `AI_SERVICE_API_KEY` 并连接配置库。新增模型时填写名称、类型、provider、协议、模型 ID、完整 Base URL、API key、能力标签和调用参数。编辑时 API key 留空保留原值，填写新值则替换；API 响应只返回 `api_key_set`，不回显原 key。保存与切换不会调用模型。

当前实体提取支持 **启用的文本模型 + `openai_chat` 协议**。保存后点击“用于实体提取”完成绑定。同步接口每次从数据库读取绑定及最新配置；异步任务提交时绑定配置 ID/版本，执行时重新从 MongoDB 读取凭据并检查版本和启用状态，不自动回退到其他模型。`base_url` 直接使用保存值（例如包含 `/v1` 或 `/compatible-mode/v1`），不会自动补路径。

图片已接入下文的 ComfyUI 异步生成；语音和视频仍仅支持配置保存。

`model_comfigs` 保留该集合拼写，包含两类文档：

| 字段 | 模型文档含义 |
|---|---|
| `_id` / `record_type` | UUID / `model` |
| `schema_version` / `revision` | 文档格式版本 / 每次编辑递增的配置版本 |
| `name` / `kind` | 显示名 / `text`、`speech`、`image`、`video` |
| `provider` / `protocol` | 供应商标识 / `openai_chat`、`openai_audio`、`openai_images`、`custom_http` |
| `model` / `base_url` / `api_key` | 实际模型 ID / 完整 API 根地址 / 原始凭据（仅配置集合保存，HTTP 不回显） |
| `enabled` / `capabilities` | 是否启用 / 能力标签，例如 entity_extraction、tts、i2v |
| `parameters` / `notes` | 调用参数对象 / 备注 |
| `created_at` / `updated_at` | UTC 时间 |

用途绑定保存在同集合固定 `_id=bindings`、`record_type=bindings` 文档中，`entity_extraction_id` 指向模型 UUID。列表排除该绑定文档。切换只更新单个文档中的指针；修改模型时通过 `revision` 检查并发覆盖，旧版本提交返回409。后续可在绑定文档中增加其他用途指针。

文本调用参数支持 `temperature`、`top_p`、`max_tokens`、`max_completion_tokens`、`seed`、`response_format`、`extra_body`；未知字段或无效范围返回422。`model`、`messages`、`api_key`、`base_url`、`stream` 不能放在参数中覆盖连接设置，`extra_body` 同样不能覆盖模型、输入或连接字段。其他模型类型的参数保存为 JSON，等对应适配器接入后执行。

配置 API 均沿用服务 Bearer token：

```text
GET  /v1/model-configs?limit=50&offset=0
POST /v1/model-configs
GET  /v1/model-configs/{id}
PUT  /v1/model-configs/{id}                    # 完整配置，需携带 revision
PUT  /v1/model-configs/{id}/entity-extraction  # 绑定，无请求体
```

列表每页1～100条，offset 0～10000。未配置数据库、无法读取配置、未绑定或停用模型时明确失败，不读取旧环境变量。HTTP 验证错误不回显 API key。

旧环境配置可显式迁移一次（不会自动在启动时执行）：

```bash
python scripts/import_model_env.py --env-file .env --remove-model-env
```

迁移使用 Prism 自己的文件，保留已有有效绑定；数据库读回验证成功后才移除旧模型变量。重复执行不创建重复模型，连接和服务 token 保留。新安装请直接使用配置页。

## 异步 AI 任务

测试页面已改用后台任务：提交后立即得到任务编号，查询排队/处理中/成功/失败状态。推理耗时本身不变。接口均沿用服务 Bearer token：

```text
POST /v1/scripts/extract-entities/jobs   # 同原提取请求体，必须提供 Idempotency-Key 请求头
GET  /v1/jobs/{id}                      # 输入、状态、阶段、结果或安全错误码
GET  /v1/jobs?limit=20&offset=0&status=queued
```

提交返回 `202`、完整任务文档及 `Location: /v1/jobs/{id}`；任务的 `id` 不是业务资产 ID。客户端每次有意新建任务生成一个提交标识（1～128字符）；网络超时重试必须复用该标识和相同输入。相同标识返回原任务，即使任务已结束；不同输入返回409。该标识在整个 Prism 服务内唯一，不是按用户隔离。列表最多100条、offset最多10000，返回摘要，详情才返回完整正文、提示词及结果。所有响应禁用缓存。

新增集合：

- `ai_jobs`：唯一的异步任务状态来源。保存类型、输入、组装提示词及哈希、无密钥模型快照、提交指纹、状态、阶段、领取标识、worker ID、租约、时间、结果、usage 和安全错误码。
- `ai_job_attempts`：执行尝试审计镜像，由 worker 从任务文档补齐；不是第二套调度状态。实体提取任务最多执行一次推理；图片任务按候选串行执行，恢复时查询原上游任务，不自动重新提交。当前不提供取消接口。

任务流为 `queued → running → succeeded/failed`。worker 以条件原子更新领取，默认租约60秒、每20秒续期；完成写入还必须匹配领取标识与未过期租约，旧 worker 不能覆盖终态。API 重启不影响任务；worker 未启动时任务留在队列。实体提取 worker 强制中断后，其他或重启的 worker 会将过期运行任务标记为 `failed / execution_outcome_unknown`，**不会重新提交上游**。同步文本 provider 没有可恢复的任务 ID，此时可能已经发生调用/计费，需人工检查后决定是否以新标识创建任务。恢复检查依赖运行中的 worker；停掉所有 worker 时状态不会自动变化。多机部署需保持时钟同步。

提交时固化提示词和配置 ID/版本，不复制 API key。worker 在执行前读取该模型；配置被禁用、删除或版本变化时失败，版本变化错误码为 `model_config_changed`，不会调用新配置替代。切换用途绑定只影响后续新任务。上游错误和非法输出记为失败，不自动重试；结果写库暂时失败最多重试写入3次，绝不因此重做推理。持续写库失败最终按租约过期处理，无法承诺故障下结果必达或上游 exactly-once。

旧 `POST /v1/scripts/extract-entities` 仍保留同步兼容行为和原响应结构；旧的 `entity_extraction_runs` 历史不迁移。新异步任务仅写 `ai_jobs`，不会重复写旧历史。此次没有修改或切换 LumenX 业务调用。图片 handler 和按资源组串行执行已加入；视频、分镜及通用多节点调度尚未实现。

## HTTP 接口与接入

`POST /v1/scripts/extract-entities`：

```json
{"text":"叶墨走进书房，拿起一本书。","custom_extraction_prompt":""}
```

输出 `characters`、`scenes`、`props` 三个数组，字段契约见 `src/lumenx_prism/schemas.py`。文本为 1～80000 个字符，自定义提示词最多 30000 字符。自定义模板含 `{text}` 时替换，否则在末尾追加原文。

服务配置 `AI_SERVICE_API_KEY` 后，请求携带 `Authorization: Bearer <token>`。请求错误 422；服务鉴权错误 401；模型数据库/模型未配置 503；上游失败或非法输出 502。原始 provider 错误、脚本及密钥不会放入错误响应。

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
2. 先在“模型配置”页面保存并绑定实体提取模型，再粘贴剧本或“载入中文样本”，可展开自定义提示词。只有点击“提取实体”或按 Ctrl/⌘+Enter 才会调用真实模型，可能产生费用。加载页面、样本、配置与历史不会调用模型。
3. 页面自动查询任务直到结束；刷新页面后再次连接服务可继续查询当前任务，也可从历史打开排队/运行任务。当前标签页的 sessionStorage 仅保存任务编号、提交标识和输入哈希，不保存令牌或正文。查询断开不取消任务；提交响应丢失时，相同输入再次提交会复用提交标识。
4. 检查角色/场景/道具卡片与原始 JSON；历史可切换“异步任务 / 旧同步历史”，支持筛选、分页、详情和恢复输入。恢复输入不会提交。
5. 历史与提取沿用同一 Bearer token 鉴权。未设置 token 时这些接口均无鉴权，请保持回环监听。页面无外部 CDN 依赖。

## 数据库与旧同步实验历史

按当前部署方案，Prism 使用现有 MongoDB 实例，以独立数据库 `lumenx_prism` 和账号 `prism_app` 做数据库级隔离，不启动单独 MongoDB 容器。连接信息只放 Prism 自己的 `.env`：

```dotenv
PRISM_MONGODB_URI=mongodb://prism_app:<URL_ENCODED_PASSWORD>@<mongo-host>:27017/lumenx_prism?authSource=lumenx_prism
PRISM_MONGODB_DATABASE=lumenx_prism
```

应用仅允许选择 `lumenx_prism` 数据库。`authSource` 指定用户认证库；URI 密码需要 URL 编码。安装依赖后，首次有效提取会自动创建 `entity_extraction_runs` 集合及时间排序索引，不需要手动创建表或运行初始化脚本。

记录格式 `schema_version=1`：UUID 作为实验编号（不是业务实体 UUID），UTC 创建/完成时间、`running/succeeded/failed` 状态、正文与自定义提示词、实际组装提示词及 SHA256、provider、请求配置模型、耗时、校验后结果或安全错误码。`model_config` 保存配置 ID、版本及无密钥快照；`requested_model` 为配置值，`actual_model` 为供应商响应模型名，`usage` 为供应商返回的 token 用量（如有）。不记录鉴权头、模型密钥、连接串或原始 provider 异常。正文和提示词为完整明文，保留至运维按自身策略清理；当前无自动过期、历史删除或业务项目写入。

- 配置 URI 后：先持久化通过校验的输入，再调用模型。初始写入失败返回 `503 history_unavailable`，不会调用模型。非法输入和未授权请求不留历史。
- 完成后更新结果；若回写失败，保留原输入及 `running` 状态，并在原响应增加 `X-Prism-History-Warning: finalize_failed`，不丢弃已生成结果、不重试模型。请复制结果留存。服务中断也可能留下 `running` 记录，该状态不表示后台任务一定仍在执行。
- 未配置 URI：实体提取返回 `503 model_database_not_configured`，不调用模型；独立历史查询返回 `503 history_not_configured`。MongoDB 连接格式错误返回 `503 history_configuration_error`。测试可显式注入假模型和假仓库，但运行服务不使用这些测试覆盖。
- 原接口成功响应结构仍为三个实体数组；已创建记录时增加响应头 `X-Prism-History-Id`，便于关联成功或失败请求。

历史查询（均需相同服务 token）：

```text
GET /v1/scripts/extract-entities/history?limit=20&offset=0&status=succeeded
GET /v1/scripts/extract-entities/history/{id}
GET /v1/test-console/config
```

列表每页1～100条，offset 0～10000；`status` 可省略，或为 `running`、`succeeded`、`failed`。列表仅返回摘要，详情返回完整输入/提示词/结果，返回 `Cache-Control: no-store`。不存在的记录返回404。控制台配置接口只返回模型名、provider、配置 ID/版本及配置是否存在，不返回密钥或 provider 地址；配置存在不代表上游在线。

## VS Code 与测试

打开同级 `../lumenx-prism.code-workspace`，同时显示 `LumenX` 和 `LumenX Prism`；两个文件夹各自保有 Git 仓库和解释器。F5 可分别启动业务后端或 Prism，工作区任务可执行 Prism 测试。

本项目新增 `.vscode/launch.json`：选 `Prism: API + Worker` 可同时调试两个进程，或仅启动 `Prism: Worker` 配合已经运行的 API。请先停止占用17178端口的 API，避免重复启动；在 `worker.py` 的 `run_once` 中设置断点。暂停 worker 超过60秒会使租约失效，这是调试时也保留的保护机制。

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

测试覆盖 JSON/实体契约、鉴权、错误脱敏、数据库模型配置、无环境变量回退、模拟历史仓库及控制台资源，以及复制应用到临时目录后启动真实 HTTP 服务对接本机模拟模型；不调用付费 API。自动化测试屏蔽真实 MongoDB URI。代码不依赖 ../lumenx，不提交个人工作材料。

中文剧本、参考实体及评测标准见 [实体提取评测](docs/entity-extraction-evaluation.md)。测试不会自动加载 `.env`；依赖包括 `pymongo`，安装命令见上文。部分受限沙箱无法运行 TestClient/回环 HTTP 测试，应在允许本机回环通信的终端执行。模型按配置页绑定的单一模型执行，不自动重试其他模型。自动化 pytest 仅使用模拟 provider；真实模型效果需要单独运行剧本评测。

## 环境边界

231 为测试环境，生产为另行管理的 LumenX 服务。Prism 已独立部署到231的 `/data/aigc/lumenx-prism`，使用指定 Conda 环境。开发代码同步需显式运行同步脚本，不会切换 LumenX 业务调用。业务仓库的 `src/ai_service` 仍有导入依赖，不能直接删除。

### 进程级队列验收

常规 `python -m pytest` 使用模拟数据库及 provider。额外的进程级测试需要可访问 Prism `.env` 指定的 MongoDB：

```bash
PRISM_MONGO_INTEGRATION=1 python -m pytest tests/test_jobs_process_integration.py
```

此测试在 `lumenx_prism` 中创建随机 `prism_test_*` 临时集合并在结束后清理，启动独立 API/worker 与本机模拟模型服务。验证 API 重启后排队任务仍可执行、重复提交返回同一任务、强制杀死执行中的 worker 后任务明确失败而不重复推理，以及新 worker 能继续处理后续任务。它不调用真实模型、不改已有模型配置或任务。

## 图片生成与角色参考卡

新增 `POST /v1/images/generate/jobs`，共用 `GET /v1/jobs/{id}`；独立 worker 执行。图片类型包括角色、场景、道具和自由图片。完整角色卡遵循本地 MiniMax-H3 `3d-animation-short-generator` STEP 3：主 3/4、正侧背视图、表情、服装/材质/道具细节、名称和 Visual ID 标签。支持指定二维或三维画风。正式角色版本及审批仍归 LumenX。

启动 API/worker 后打开 `/tools/images`；先在模型配置中添加并绑定 `comfyui / custom_http / image / qwen-image-2.1`。本地 ComfyUI 可不填 API key。参数至少包含 `{"resource_group":"231-gpu0"}`，所有共享同一物理 GPU 的图像配置必须使用同一个 resource_group。建议配置 `peer_base_urls:["http://192.168.8.231:8188"]`，避免与旧实例队列冲突。

也可显式运行（仅修改 Prism 自身模型配置，不覆盖已有图像绑定；加 `--bind` 才替换）：

```bash
PYTHONPATH=src python scripts/configure_qwen_images.py --env-file .env
```

`POST /v1/images/preview` 只编译提示词。`POST /v1/media` 使用 multipart 文件上传，返回 id；请求 `reference_media_ids` 按上传后的 ID 有序引用。`GET /v1/media/{id}/download` 沿用服务 token。文件限制32MiB，PNG/JPEG/WebP 单帧，内容解码和哈希校验。媒体目录通过 `PRISM_MEDIA_ROOT` 配置，默认项目下 `data/media`；API 与 worker 必须使用相同绝对目录。231 推荐 `/data/aigc/lumenx-prism/data/media`。`PRISM_PUBLIC_BASE_URL` 可配置远端可达地址来返回绝对下载 URL；未配置返回相对 URL，由客户端相对服务地址解析。

模型参数仅从 `model_comfigs` 读取。新增图片绑定 `image_generation_id`，不改变实体提取绑定。工作流固定为随包发布的 `qwen-image21-v1`，输入冻结模型版本、工作流哈希、角色描述、参考文件哈希和提示词。候选1～4张串行生成，避免一批占满显存。三视图必须有参考图片；全身图、头像和完整卡片可文生图，也可引用已有角色。延续同一角色时推荐使用参考图。

图片运行记录增加内部 executions（不在公共响应暴露），保存提交意图、上游 prompt_id、精确工作流与已完成候选。worker 租约失效后，替代 worker 查询原上游任务；提交结果不确定时按关联标识查找，无法唯一确认则明确失败，保留资源锁供人工核查，不自动重复生成。持有不确定上游任务的 `ai_resources` 记录只能在核对上游终止后人工释放；不能按时间自动删除。资源锁只约束 Prism，外部直接调用 ComfyUI 不受该锁管理。当前不提供任务取消接口。

`ai_media` 保存媒体元数据，文件落盘后才完成任务。结果返回 artifacts、download_url、review_required；成功仅表示文件生成完整，不表示身份一致性自动验收。参考卡的标签、视角和身份特征需人工检查。

LumenX 可通过自己的 `PRISM_IMAGE_SERVICE_URL` 启用 HTTP 接入，上传参考、提交、查询、下载到原有 output/assets，再保存为正式候选。Prism 绝不直接修改业务数据库。不启用时业务调用不变。生成模型以 Prism 绑定为准，旧 LumenX 图片模型选择不控制远端模型。

本轮真实图像链路已在 231 验证：角色卡、引用卡片的头像、场景和道具均生成并回收，单张约22～24秒。角色卡小字、视角标签与表情数量仍有不足；按用户确认先作为 Demo 使用，后续再优化细节。

### 临时关闭服务接口鉴权

本机开发 `.env` 当前设置 `PRISM_SERVICE_AUTH_ENABLED=false`，并注释 `AI_SERVICE_API_KEY`。重启 API 后，所有 Prism 接口无需服务令牌，三个测试页隐藏令牌输入框；上游模型 API key 仍从 MongoDB 正常读取。恢复时设置 `PRISM_SERVICE_AUTH_ENABLED=true`、恢复服务令牌并重启 API，页面也会恢复令牌输入。此开关只控制 Prism HTTP 入口，不影响模型供应商鉴权。

### 图片测试样本库

图片制作台的“选择测试样本”从 Prism MongoDB `image_test_samples` 读取，按风格、人物、场景、道具分组，支持搜索及风格分类。风格与主体分别应用，可自由组合；风格照片用于浏览，生成应用所选正面/负面提示词，不自动把照片中的人物作为身份参考。

样本文档保存 `schema_version`、`kind`、名称/说明、排序、创建时间和 `source` 来源。风格保存正负提示词、分类、适合/避免题材和原图/缩略图媒体 ID；实体样本保存可用的 `ImageRequest` 和缺失信息说明。照片元数据在 `ai_media`，文件保存在 Prism 媒体目录。人物、场景、道具来源为提取任务的结果快照，缺失外观不自动编造。当前导入15种风格及3人物/2场景/4道具。

查询：`GET /v1/image-samples?kind=style&limit=100&offset=0`，类型可省略；详情 `GET /v1/image-samples/{id}`。沿用 Prism 鉴权开关，不开放样本修改接口。样本不是模型配置或业务资产。需要复制初始数据时显式执行：

```bash
PYTHONPATH=src python scripts/import_image_samples.py --env-file .env \
  --lumenx-root /path/to/lumenx \
  --source-job-id 3aab2bb8-6a38-4c3b-bf1f-53b36cb77712
```

导入仅一次性读取参考 JSON/照片，不导入业务 Python 或连接业务数据库；运行时只读取 Prism 数据。稳定样本 ID 和媒体 ID 支持重复执行，已有样本及手动修改不会被覆盖。选择记录写入新任务 `source_context`，实际输入仍完整保存在任务快照。

点击历史任务仅查看结果；“恢复原输入”显式恢复原请求，包括参考媒体 ID、模型选择、提示词、尺寸、种子与候选数量，清除待上传文件及提交标识。恢复不创建任务；再次提交会使用新的提交标识。

### 角色图片生成顺序（提示词 v3）

推荐“主体全身图定妆 → 头像细化 → 三视图展开 → 完整角色参考卡”。重视五官时也可从头像开始。页面默认从全身图进入；选取人物样本会进入全身图，不再默认请求完整卡。

- `full_body`：单人、单正面视角、从头到脚完整可见；参考图可选，推荐768×1344。
- `headshot`：单人头肩近景；参考图可选。无图时从文字设计新角色，有图时延续身份；推荐768×1024。
- `three_view`：同一角色正、侧、背三个视角；必须传参考图，推荐1344×768。
- `reference_sheet`：汇总3/4、正侧背、表情、服装、道具与身份说明；建议用已确认的参考素材，推荐1344×768。当前仍由模型生成整张卡片，尚未实现程序化拼版。

推荐尺寸只作用于页面，新任务接口仍接受原有合法尺寸。手动改尺寸后切换类型会保留自定义值，可用“采用推荐尺寸”重置；恢复历史保留原始尺寸。单图提示词不带多视图身份重复或“缺少必要视图”负面约束，风格只改变渲染表现，不应取代明确的角色服装。

角色结果下的“准备下一步”沿用该任务的输入、带入参考图并设置下一阶段与推荐尺寸，不自动提交。头像转三视图时保留已有全身参考作为首图，再附头像；其他步骤同样保留有序参考，最多4张。所有新任务保存 `character-production-v3` 提示词版本；已有任务保留提交时的提示词与输入快照，不会被新模板改写。

## 231 独立部署与管理

部署目录 `/data/aigc/lumenx-prism`，解释器 `/home/zenking/miniconda3/envs/lumenx_prism/bin/python`。API 监听 `0.0.0.0:17178`；图片页面为 `http://192.168.8.231:17178/tools/images`，实体页 `/tools/entities`，模型页 `/tools/models`，接口文档 `/docs`。API 和 worker 为独立后台进程，不使用 reload，SSH 断开后继续运行；当前未配置开机自启。

本机执行同步（包含 Prism 自己的 `.env` 和媒体）：

```bash
./scripts/sync-to-231.sh --dry-run  # 预览，不写远端
./scripts/sync-to-231.sh            # 同步代码、.env 和 data/media
./scripts/sync-to-231.sh --code-only # 后续只同步代码和 .env
```

同步不删除远端文件，不安装依赖、不重启。排除 Git、虚拟环境、缓存、个人 docs/agents 材料和独立 ComfyUI 编排。远端旧 `.env` 备份在 `run/env-backups/`，新 `.env` 权限600；同步后的 `.env` 内容与本机一致，不打印配置内容。

`deploy/231.env` 是无凭据的主机覆盖文件：将媒体根目录设为 `/data/aigc/lumenx-prism/data/media`、公开 URL 设为 `http://192.168.8.231:17178`。服务管理器先读 `.env`，再应用该文件，因此无需把本机路径写成远端路径；数据库、鉴权开关继续沿用同步的 `.env`。

首次部署或依赖变化时，在231安装依赖：

```bash
cd /data/aigc/lumenx-prism
/home/zenking/miniconda3/envs/lumenx_prism/bin/python -m pip install \
  --index-url https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
```

在231管理服务（省略组件名表示 API + worker）：

```bash
./scripts/prismctl.sh start
./scripts/prismctl.sh restart
./scripts/prismctl.sh stop
./scripts/prismctl.sh status
./scripts/prismctl.sh logs --lines 100
./scripts/prismctl.sh logs api -f
./scripts/prismctl.sh logs worker -f
# 单独管理组件，例如：
./scripts/prismctl.sh restart api
```

也可在本机运行 `ssh zenking@192.168.8.231 /data/aigc/lumenx-prism/scripts/prismctl.sh status`。日志为 `logs/api.log`、`logs/worker.log`。PID 及进程起始标识保存在 `run/`，重复启动不创建第二个进程；只停止记录且校验通过的进程，避免误杀 PID 复用后的其他服务。worker 收到终止信号后完成当前任务再退出，默认最多等待1200秒；超时明确失败，不强杀，也不启动第二个 worker。

首次切换已暂停本机 Prism API/worker，并校验迁移时所有历史媒体。共用同一 Prism 队列时，API 与 worker 应使用相同媒体文件目录；不要直接在本机重新启动 worker 与231混用各自的本地媒体目录。远端运行生成的媒体不会反向同步到本机；后续同步不会删除这些远端文件。
