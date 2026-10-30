# 分镜与 H3 视频制作 Demo

## 范围与页面

2026-10-30 新增 `/tools/storyboards` 和 `/tools/videos`。默认16:9、15秒、3镜头，中文对白与环境音，不添加背景音乐。正式项目、角色审批和资产归属继续由 LumenX 管理；本功能只在 Prism 保存实验分镜、生成输入和历史结果。

1. 从实体提取历史载入剧本，或粘贴剧本。
2. 从图片历史选定具体人物、场景、道具候选；图片制作台也有“用于分镜”入口。同名素材替换已有选择，不自动选定某任务第一张图片。
3. 生成结构化分镜，编辑并保存版本；点击“确认并进入视频制作”。
4. 预览 H3 提示词，单镜头生成或提交全部镜头。视频按 GPU 资源槽串行执行。
5. 每镜头选择一个成功候选，点击“合成所选候选”。合成保留各片段音轨，不增加背景音乐。可从合成历史重新查看。

分镜以文字为准，不强制生成铅笔分镜图。前后镜头动作、人物身份和口型仍需人工查看；结构校验通过不等于一致性验收通过。

## MiniMax skill 映射

参考本地 MiniMax-H3 的 `skills/3d-animation-short-generator/SKILL.md`、`references/shot-table-spec.md`、`references/storyboard-guidelines.md`、`references/model-selection.md`、`references/fallback-policy.md`、`references/qc-checklist.md`，以及 `skills/h3-prompt-writing/SKILL.md` 和 `references/ref-en.txt`。前者的 Hub 画布操作适配为 Prism 页面和数据库记录，不调用不存在的 Hub 工具。

保留六列语义：镜头编号/时长、衔接、空间与身份锚点、叙事钩子、逐秒导演指令、音频对白。每秒包含动作/表情、运镜、空间、音效和交接状态；每镜头最多3个重要人物，所有素材名称必须对应用户选定的图片。模型按所选画风规划，不强制3D动画风格。

H3 Ref2VA 提示词固定顺序：`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。原中文对白不翻译；素材顺序与 `<Picture N>` 一致，角色卡作为身份参考，不作为首帧。`N/A` 明确无背景音乐；这仍是模型指令，不是生成质量的硬保证。

解析拒绝缺失字段、非法引用和时间空洞。模型若使用整片累计时间，只在每镜头绝对区间完整且无歧义时转换为镜头内0起点，不补造台词或动作。原始模型输出保留在任务内部；解析失败可以重新校验保存的输出，不重新调用模型。

## HTTP 契约

新增提交均需 `Idempotency-Key`，返回202及 `Location`，复用 `/v1/jobs/{id}` 查询。

| 接口 | 用途 |
|---|---|
| POST `/v1/storyboards/generate/jobs` | 剧本、素材、画幅、总时长、镜头数 → 分镜建议 |
| GET/POST `/v1/storyboards` | 查询/保存实验分镜 |
| GET/PUT `/v1/storyboards/{id}` | 读取/按 revision 修改草稿 |
| GET `/v1/storyboards/{id}/shots/{shot_id}/prompt` | 预览已保存镜头的 H3 提示词 |
| POST `/v1/videos/generate/jobs` | draft_id、revision、shot_id、seed、可选 model_config_id |
| POST `/v1/videos/assemble/jobs` | draft_id、revision、有序 video_job_ids |
| POST `/v1/jobs/{id}/recover-video` | 对已提交但失败的任务查询原推理并恢复回收，不盲目重生成 |
| POST `/v1/jobs/{id}/recover-storyboard` | 重新解析已保存模型输出，不重新推理 |

任务列表支持 `type=storyboard_generation|video_generation|video_assembly`，以及可选 `draft_id`、`revision` 筛选。正式字段以 `/docs` schema 为准。

`storyboard_drafts` 保存可编辑草稿和 revision；视频任务冻结完整分镜输入、镜头、媒体哈希、模型版本、工作流版本和实际提示词。更新草稿不会改写历史视频；合成拒绝混合草稿版本、错误镜头或未完成候选。请求时长与实际片长分别记录，不能假设请求5秒就精确得到5秒。

## 模型与运行环境

配置只来自 MongoDB `model_comfigs`。模型页新增“用于分镜规划”和“用于视频生成”；分镜绑定可复用实体提取的文本模型，独立绑定不修改实体提取用途。视频配置示例（无凭据）：

```json
{
  "name": "231 H3 Ref2VA",
  "kind": "video", "provider": "comfyui", "protocol": "custom_http",
  "model": "minimax-h3-ref2va", "base_url": "http://192.168.8.231:8188",
  "parameters": {
    "resource_group": "231-gpu0", "steps": 20, "output_megapixels": 0.4,
    "min_free_vram_mb": 24000, "peer_base_urls": ["http://192.168.8.231:8190"]
  }
}
```

当前仅接入 `h3-ref2va-v1` 图片参考工作流；最多9张参考图片。首尾帧、纯文本、音视频参考、声音克隆和其他视频模型尚未接入，不能仅因为节点存在就认为可用。231本地 Ref2VA 权重名与原云端模板目录不同，模板已固定本地名称并做提交前校验。

所有推理只在231；本机不运行或存储模型。H3由 ComfyUI Manager 的 `h3` 实例管理，8188；Qwen图片实例为 `qwen`，8190。H3权重在 `/data/aigc/comfyui-h3/models`，源码/插件/输入输出仍有NFS挂载，本次未迁移。

## Worker 与显存

`prismctl.sh` 默认管理 API、GPU worker（组件名 `worker`）和文本 worker（`text-worker`）：

```bash
./scripts/prismctl.sh status
./scripts/prismctl.sh restart
./scripts/prismctl.sh logs worker -f
./scripts/prismctl.sh logs text-worker -f
# 开发时分别运行；API和worker必须使用相同数据库和媒体根目录
python -m lumenx_prism.worker --env-file .env --capabilities text
python -m lumenx_prism.worker --env-file .env --capabilities gpu
```

`--capabilities all` 保留单进程开发兼容方式；231使用分开的文本和GPU进程。视频不会阻塞实体提取与分镜文本生成。图片和视频共用 `231-gpu0`，不能以端口分配两份GPU容量。

持有资源锁并确认双方队列空闲后，请求空闲对端 `/free` 卸载模型。视频还卸载本实例缓存并等待实际可用显存达到 `min_free_vram_mb`，45秒内未释放则明确失败，不冒险提交。模型仍可能在推理时OOM，该检查不保证峰值显存足够。若需要停止容器，先停止GPU worker接收新任务、等待在途任务完成，再通过 Manager 队列保护操作，避免全局中断。

已提交视频在worker重启后查询持久化 prompt_id。提交响应不确定时按关联标识查找，无法确认则保留资源锁；恢复接口只继续查询，不重发推理。下载/落盘最多重试3次，之后可显式恢复原视频任务。文本调用中断不自动重复调用模型。

## 媒体与验证

视频保存为 Prism 本地 MP4，元数据在 `ai_media`；上限256MiB，使用 ffprobe 检查真实尺寸、时长、fps、音轨，ffmpeg 完整解码后原子落盘。231需安装 ffmpeg/ffprobe。复用媒体下载接口，支持浏览器视频播放；文件路径和上游地址不作为结果公开。合成归一到24fps、同尺寸、AAC双声道，并保留实际片长。

回归覆盖时间轴、引用解析、版本冲突、幂等、文本中断、视频重启恢复、未确认提交的资源锁、HTTP鉴权以及带音轨视频合成。模拟测试不加载模型。真实样片只能证明链路与具体样本可用，不等于中文对白、音色或身份一致性已完整验收。
