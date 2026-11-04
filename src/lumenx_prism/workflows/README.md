# ComfyUI API 工作流

此目录是 Prism 工作流模板的唯一维护入口。JSON 均为 ComfyUI API 节点图（节点编号、class_type、inputs），用于 HTTP `/prompt` 提交；不是带画布坐标和连线布局的编辑器导出格式。文件不含模型权重。

| 文件 | 用途 | 加载与组装代码 |
|---|---|---|
| [qwen-image21-v1.json](qwen-image21-v1.json) | Qwen-Image 2.1 图片生成模板；执行时可增加参考图节点 | [comfy_images.py](../comfy_images.py) |
| [h3-ref2va-v1.json](h3-ref2va-v1.json) | H3 Ref2VA 参考图生成带音频视频 | [comfy_videos.py](../comfy_videos.py) |

## 参数如何进入工作流

- 图片：注入正负提示词、尺寸、种子、步数、CFG、参考图及输出文件名前缀；候选逐张执行。
- 视频：注入当前镜头提示词、时长、画幅、输出像素、种子、采样步数、参考图片时间线和输出文件名前缀。
- 服务地址、凭据和模型调用参数仍从 MongoDB `model_comfigs` 读取；模板中的模型文件名在提交前检查。
- worker 将组装后的节点图提交 ComfyUI，保存上游任务编号、查询结果并下载媒体。视频合并由 FFmpeg 执行，不使用 ComfyUI workflow。

## 版本与维护

提交任务会保存模板版本及 SHA256；模板变化可能使已排队任务返回 `workflow_changed`，因此修改模板需要考虑在途任务，不能静默覆盖。执行参数注入只修改内存中的节点图，不改写模板文件。视频任务另保存实际提交的工作流，便于追溯。

此次目录整理未改动两份运行时 JSON 的文件名、内容或哈希。原根目录 `workflows/qwen-image21-t2i.api.json` 与 `qwen-image21-v1.json` 字节完全相同，已删除重复副本。历史服务器的工作流文件不是新的源码维护入口，此次不修改远端文件或运行服务。

打包配置 `pyproject.toml` 已包含 `workflows/*.json`，安装后的服务仍能按包内路径加载模板。未来新模板也统一放在本目录，并显式接入适配器、配置校验和测试；仅新增 JSON 不会自动获得新的生成能力。
