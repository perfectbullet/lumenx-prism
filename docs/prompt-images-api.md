# 提示词生图 API 调用指南（供 Agent 集成）

核对日期：2026-11-02。对应页面：`/tools/prompt-images`，页面地址不是提交接口。

## 1. 地址与调用流程

测试环境 Base URL：`http://192.168.8.231:17178`。

**提交一次 → 保存任务 ID → 轮询任务 → 下载图片**。不需要作品、分集、实体提取或分镜任务。

| 用途 | 方法与路径 |
|---|---|
| 提交图片生成 | `POST /v1/images/generate/jobs` |
| 查询任务详情、原输入和结果 | `GET /v1/jobs/{job_id}` |
| 独立生图历史 | `GET /v1/jobs?type=image_generation&prompt_mode=raw` |
| 下载原图 | `GET /v1/media/{media_id}/download` |
| 校验并预览实际提示词，不生成图片 | `POST /v1/images/preview` |

在线接口定义：`http://192.168.8.231:17178/docs`，机器可读定义：`/openapi.json`。

启用服务鉴权时，上述请求带 `Authorization: Bearer <服务 token>`；关闭鉴权的开发环境可不带。这里使用 Prism 服务 token，不是模型供应商 API key。模型及凭据由服务端 MongoDB 配置管理。

## 2. 提交任务

请求头：

```http
Content-Type: application/json
Idempotency-Key: <本次逻辑生成请求的唯一标识，推荐 UUID>
```

请求体示例（生成 4 张）：

```json
{
  "prompt_mode": "raw",
  "asset_type": "image",
  "generation_type": "image",
  "name": "雨夜城市测试",
  "prompt": "电影级写实风格，雨夜城市街道，霓虹灯倒映在湿润路面，远景，细腻光影，无人物。",
  "negative_prompt": "人物，人脸，文字，水印，模糊",
  "width": 1344,
  "height": 768,
  "seed": 20261102,
  "batch_size": 4
}
```

| 字段 | 约束与默认值 |
|---|---|
| `prompt_mode` | **必须显式传 `raw`**；不传会走默认资产模板 `asset` |
| `asset_type` | 本页面模式固定 `image`，默认也是 `image` |
| `generation_type` | 固定 `image`，默认也是 `image` |
| `name` | 可选，任务名称，最多 100 字符，默认空字符串 |
| `prompt` | 必填且非空，最多 12000 字符 |
| `negative_prompt` | 可选，最多 3000 字符，默认空字符串 |
| `width` / `height` | 各 256～2048，必须为 32 的倍数；两者乘积不超过 2097152；默认 1344×768 |
| `seed` | 整数，0～9007199254740991（`2**53-1`），默认 0；需要随机时由调用方生成合法整数 |
| `batch_size` | 整数 1～4；**API 默认 1，页面默认 4**，建议显式传入 |
| `model_config_id` | 可选 UUID，不传使用当前默认图片模型；普通调用建议不传 |
| `reference_media_ids` | 可选，最多 4 个不重复的已上传图片 UUID，默认空数组；纯文字生图不传 |
| `source_context` | 可选追踪字典，最多 12 项，键最多 100 字符、值为最多 1000 字符的字符串；独立生图通常不传 |

raw 模式不追加角色、道具、风格或默认负面提示词，也不调用润色模型；字段首尾空白会被清理。不能同时设置非空 `style`、`description` 或角色卡 `character_card`。不支持的字段会返回 422，不能直接传 `steps`、`cfg`、`model` 或供应商密钥。

成功受理返回 **HTTP 202**，`Location: /v1/jobs/{job_id}`。响应是任务对象，重点读取 `id`、`status`、`phase`；202 表示已受理，不表示生成成功。

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "type": "image_generation",
  "status": "queued",
  "phase": "queued",
  "result": null,
  "error_code": null
}
```

上面和后面的响应 JSON 均为字段节选；不要要求响应只能包含这些字段。

### 幂等与重试

- `Idempotency-Key` 必填，长度 1～128。新的一次生成使用新 UUID。
- 同一逻辑请求重试时，使用**原请求体及原 key**；服务端返回已有任务，不重复创建。同一 key 改参数会返回 409 `idempotency_conflict`。
- POST 超时、网络中断或响应丢失，任务可能已经创建；不能换 key 直接重提。调用方应先持久化 key 和请求体，使用原值恢复。
- 重放提交仍可能返回 202，返回任务也可能已经成功或失败；以响应中的状态为准。
- key 不应跨不同调用方业务请求复用，推荐 UUID。

## 3. 查询状态与下载

`GET /v1/jobs/{job_id}` 返回完整任务，包括 `request` 原输入及 `result`。

| status | 调用方行为 |
|---|---|
| `queued` | 等待；GPU 队列暂停或显存资源等待时可能持续排队 |
| `running` | 正在执行，可读取 `phase` 辅助展示 |
| `succeeded` | 读取 `result.artifacts` 并下载 |
| `failed` | 读取 `error_code`，保留任务 ID 用于排查 |

建议每 5 秒轮询一次。调用方等待超时不会取消后端任务；保留 ID，之后继续查询，不重新提交。

成功结果节选：

```json
{
  "id": "00000000-0000-4000-8000-000000000001",
  "status": "succeeded",
  "error_code": null,
  "result": {
    "artifacts": [
      {
        "id": "00000000-0000-4000-8000-000000000002",
        "kind": "image",
        "mime_type": "image/png",
        "width": 1344,
        "height": 768,
        "candidate_index": 0,
        "generation_type": "image",
        "download_url": "/v1/media/00000000-0000-4000-8000-000000000002/download"
      }
    ],
    "review_required": true,
    "prompt_version": "raw-prompt-v1"
  }
}
```

候选序号 `candidate_index` 从 0 开始；4 张图片对应 4 个 artifact。`review_required` 提醒人工检查效果，不代表请求失败。下载响应为图片二进制，格式以 `Content-Type` / `mime_type` 为准。

`download_url` 可能是相对路径，也可能因公开地址配置而为绝对 URL。内网 Agent 可直接使用已知 Base URL 加 `/v1/media/{artifact.id}/download`；带鉴权时下载也要携带服务 token，不要把 token 转发到未知地址。

## 4. 最小 curl 示例

以下命令会真正创建图片任务并占用 GPU，按需要执行。先将第 2 节 JSON 保存为 `request.json`。

```bash
export PRISM_BASE_URL='http://192.168.8.231:17178'
# 只生成一次并记下；网络重试继续使用此值
export PRISM_REQUEST_KEY="$(python3 -c 'import uuid; print(uuid.uuid4())')"

curl --fail-with-body -sS \
  -H 'Content-Type: application/json' \
  -H "Idempotency-Key: $PRISM_REQUEST_KEY" \
  --data-binary @request.json \
  "$PRISM_BASE_URL/v1/images/generate/jobs"
```

从响应取出 `id`，之后查询：

```bash
curl --fail-with-body -sS "$PRISM_BASE_URL/v1/jobs/<job_id>"
curl --fail-with-body -sS \
  "$PRISM_BASE_URL/v1/media/<artifact_id>/download" -o result.png
```

如启用鉴权，以上每条 curl 额外增加 `-H "Authorization: Bearer $PRISM_SERVICE_TOKEN"`。不要将真实 token 写入请求文件、文档或版本库。

仅验证提示词而不推理：

```bash
curl --fail-with-body -sS -H 'Content-Type: application/json' \
  --data-binary @request.json "$PRISM_BASE_URL/v1/images/preview"
```

该接口返回 `prompt`、`negative_prompt`、`prompt_version`、`style_snapshot`，不创建生成任务。

## 5. 历史和保存

```http
GET /v1/jobs?type=image_generation&prompt_mode=raw&limit=20&offset=0
GET /v1/jobs?type=image_generation&prompt_mode=raw&status=succeeded&limit=20&offset=0
```

`limit` 为 1～100（默认 20），`offset` 为 0～10000；`status` 可为上述四种状态。返回 `items`、`total`、`has_more`、`limit`、`offset` 等字段。列表是摘要，原输入和候选结果请再查任务详情。必须带 `prompt_mode=raw` 才能隔离资产制作流程的图片任务。

任务保存到 `lumenx_prism.ai_jobs`，包括输入、状态、模型及工作流快照和结果引用；媒体元数据在 `ai_media`，图片文件在 Prism 媒体目录。关闭页面不影响任务和历史保存。这些独立任务不会自动更新作品素材库。调用方也应保存任务 ID 和所需结果文件。

## 6. 错误处理及 Agent 约定

- HTTP 401：服务 token 缺失或不正确。
- HTTP 422：请求字段、长度、尺寸、种子、必填 Header 等校验失败；检查响应错误详情后修改。
- HTTP 409 / `detail.code=idempotency_conflict`：同一个 key 使用了不同输入，先核对已有请求，不盲目换 key。
- HTTP 404：任务或媒体不存在，或媒体文件已不可用。
- HTTP 503：可能是数据库、任务存储或配置不可用，读取 `detail.code`；不是生成成功。
- 任务 HTTP 查询成功但 `status=failed`：属于异步生成失败。可能见到 `model_config_changed`、`workflow_changed`、`comfy_execution_failed`、`comfy_execution_timeout`、`execution_outcome_unknown`、`image_execution_error` 等 `error_code`。
- 对 `execution_outcome_unknown`、推理超时等结果不确定情况，保留任务 ID 交由维护者检查 ComfyUI，避免自动新建任务造成重复推理。
- 只查询或下载失败时，重试对应 GET；不要因此重新 POST 生成。不要为了解决排队自动修改模型、数据库、GPU 暂停状态或重启服务。
- 后端采用当前配置的 ComfyUI 图片工作流；同一批候选逐张执行，4 张并不代表同时执行。相同 seed 也不保证跨模型/工作流版本完全复现。

## 7. 可选：参考图片

纯提示词生图无需上传。需要引用图片时，可先以 multipart 字段 `file` 调用 `POST /v1/media`，取得响应 `id`，再放入 `reference_media_ids`。

```bash
curl --fail-with-body -sS -F 'file=@reference.png' "$PRISM_BASE_URL/v1/media"
```

支持单帧 PNG/JPEG/WEBP，单文件最大 32 MiB；上传同样遵循服务鉴权。参考图会参与推理，但不改变 raw 模式“不额外拼接提示词”的规则。

实现依据：`src/lumenx_prism/api.py`、`image_schemas.py`、`image_jobs.py`、`image_prompts.py`、`media.py`。
