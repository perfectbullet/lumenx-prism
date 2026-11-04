# h3-ref2va-v1 独立视频工具

入口：`/tools/h3-ref2va-v1`。无需作品、集、制作规划或镜头方案。

1. 填写提示词，上传 1～9 张 PNG/JPEG/WebP，按 `<Picture 1>`、`<Picture 2>` 顺序引用。
2. 设置时长、画幅、种子；可调整步数、输出像素规模和参考图精度。
3. 提交后由独立 GPU worker 执行；历史可查询进度、播放、下载、恢复输入与参考图顺序。

页面直接传递提示词，不调用语言模型、不自动润色。提示词格式参考 MiniMax-H3 `skills/h3-prompt-writing` 的全能参考六段结构。该入口只接收图片参考，不支持参考视频、音频或宫格编辑。

## HTTP

`POST /v1/videos/h3-ref2va-v1/jobs`，请求头 `Idempotency-Key`，返回 `202` 和持久化任务。重复标识与相同输入返回原任务；标识相同但输入不同返回冲突。

```json
{
  "prompt_mode": "raw",
  "name": "角色运动测试",
  "prompt": "参考 <Picture 1> 的人物和画风，人物缓慢向前行走，镜头轻微推进。自然环境声，无音乐。",
  "reference_media_ids": ["先通过 POST /v1/media 上传图片得到的 UUID"],
  "duration_seconds": 4,
  "aspect_ratio": "16:9",
  "seed": 20261016,
  "steps": 20,
  "output_megapixels": 1.8,
  "ref_image_size": "匹配输出（推荐）"
}
```

参数范围：时长 4～15 秒；画幅 auto / 21:9 / 16:9 / 4:3 / 1:1 / 3:4 / 9:16；种子 0～9007199254740991；步数 1～50；当前服务输出规模 0.2～2 MP（默认 1.8 MP）；精度可选“匹配输出（推荐）”或“最高保真（更慢）”。其余节点沿用 `workflows/h3-ref2va-v1.json`：全能参考、24 fps、res_multistep、simple、denoise 1、对齐 32、联合音视频。

模型和凭据仅从 MongoDB `model_comfigs` 的默认视频配置读取，也可在 HTTP 中传 `model_config_id` 指定支持的 H3 配置。

## 存储与调度

沿用 `ai_jobs`，任务类型 `video_generation`，请求标记 `prompt_mode=raw`。输入、参数、有效提示词、模型版本、参考图哈希和工作流哈希冻结保存；不创建虚构作品或分镜。媒体保存在 Prism 管理目录并通过媒体接口下载。

历史：`GET /v1/jobs?type=video_generation&prompt_mode=raw`，支持状态和分页；详情：`GET /v1/jobs/{id}`。主流程概览使用 `prompt_mode=asset`，按镜头方案的历史查询保持原契约。

共享 GPU 资源锁、全局暂停、单任务暂缓、ComfyUI 提交恢复、超时错误与结果回收。修改输入后提交新任务，保留失败历史。较高分辨率和参考图精度会增加资源需求，默认 1.8 MP。

## 原任务重试

选择失败任务后，点击“重试本任务”。`POST /v1/jobs/{id}/retry-video`，带 `Idempotency-Key`：保留 `_id`、`created_at`、原输入及列表顺序，递增 `retry_count` 并在 `retry_events` 保存前次错误与执行记录。排队/执行中的任务不会重复重置，已成功的任务不覆盖。

已提交到 ComfyUI 的超时任务优先恢复原 `prompt_id` 并回收结果；明确失败才创建新一轮推理，新一轮使用独立提交标识，Prism 任务 ID 不变。无法确认原提交去向时保留资源保护，不盲目重复生成。全局暂停和单任务暂缓均保持原状态。

## 更新原任务与输入版本

“新建视频任务”创建新 ID。“恢复原输入”进入编辑模式；此时“更新并重新运行”使用表单中的修改更新原任务，保持 ID、创建时间及排序。每次更新在 `ai_job_input_versions` 保存不可变的输入快照；旧版视频仍可在“输入版本与历史结果”查看和下载。

- 排队中：原子切换到新输入版本，保留单独暂缓和全局暂停状态。
- 执行中：保存为待执行版本，当前运行的输入和输出保持独立；完成并释放原资源后再切换。连续更新仅执行最新待执行版本，其余版本保留为“已被后续更新替代”。
- 已完成或明确失败：保存原结果与执行信息，再按原顺序排队新版本。
- 超时但原执行未确认：先恢复原执行查询和结果回收，确认结束并释放资源后再执行新版本。

`POST /v1/videos/h3-ref2va-v1/jobs/{id}/update`：完整 H3 输入，带 `Idempotency-Key` 和 `X-Input-Revision`（编辑时所见当前输入版本）。版本冲突返回 409，不能静默覆盖已切换的输入。`GET /v1/videos/h3-ref2va-v1/jobs/{id}/versions` 查询版本及历史结果。

## 实际执行耗时

`execution_timing` 来自 ComfyUI `execution_start` 到 `execution_success` / `execution_error` / `execution_interrupted` 的毫秒时间戳，不含 Prism 或 ComfyUI 排队，包含模型加载、采样、解码和保存，不代表纯模型推理时间。API 在后台读取已有 `/history/{prompt_id}` 补齐旧任务，worker 也保存新执行的时间。缺失历史不以 Prism 的 `duration_ms` 冒充实际耗时。多图片任务按各次工作流耗时相加，不包含候选间等待；不完整时标为部分记录。重新运行清除当前时间缓存，但保留旧版时间。

## 暂缓与显存等待

任务底层状态仍是 queued，单独暂缓标记为 `scheduling_paused=true`。页面提供“单独暂缓”筛选：`GET /v1/jobs?type=video_generation&prompt_mode=raw&status=queued&scheduling_paused=true`；普通排队可传 `scheduling_paused=false`。

H3 提交前要求空闲显存 **至少 30 GiB（30,720 MiB）**；若模型配置的门槛更高则采用较高值，不修改已提交任务的模型配置快照。显存不足保持 queued，`phase=waiting_for_memory`、`waiting_reason=insufficient_vram`。`memory_wait` 记录所需/可用字节、上次检查与下次检查时间、检查间隔（30 秒）。页面显示所需显存、当前可用及差额。

同一物理 GPU 资源组按原创建时间排队。队首未暂缓任务在下一检查时间到达前阻止后续新任务越过它；不同资源组互不阻塞。已提交执行的恢复/回收优先解决原资源锁，再开展新推理。暂缓任务不参与队首选择，不会因显存空闲自动恢复；全局暂停时不领取新的 GPU 任务，并在提交 ComfyUI 前再次检查暂停开关。

等待显存的检查发生在提交 `/prompt` 前，不计入实际执行耗时或 ComfyUI 结果等待超时。不足时不提交推理，不停止其他进程；恢复足够显存后使用原任务 ID 继续。模型节点缺失、推理过程中发生的执行错误等仍按真实失败记录。
