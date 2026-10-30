# Qwen-Image-2.1 on 231

> 历史快照：下文记录 2026-10-28 初次迁移时的路径、命令和能力状态，不代表当前运行状态。8190 当前运行目录已迁移到 `/data/aigc/comfyui-qwen-image21`，由独立 ComfyUI Manager 维护；Prism 已接入异步图片任务。

> 当前维护入口已迁到独立项目 `/home/zj/aigc/comfyui-manager`，包括8190的TTS插件配置、启停与后台迁移管理。本目录保留初次图像迁移参考；后续部署请使用管理项目中的 `deploy/qwen/compose.yaml`，避免旧配置覆盖TTS插件设置。

独立 ComfyUI 地址：`http://192.168.8.231:8190`。容器为 `prism-comfyui-qwen-image21`，数据目录为 `/nfs-data/comfyui-qwen-image21`。当前工作流与运行环境已准备，权重后台迁移完成后仍需真实出图验收。

云端原始工作流为 `/root/zealman-app/workflows/API-B18-Qwenimage2.1.json`，源码为 ComfyUI 0.39.0、revision `b0b743566f65daafc423b4fea8a2fbda94b3384a`。使用其原生 `TextEncodeQwenImage21`、`QwenImage21Cache`、`ModelAttentionBackend`、`ResolutionSelector` 等节点，无需安装额外自定义插件。原图像保存节点 `SaveImageAdvanced` 改为原生 `SaveImage`，去掉云面板 `_api_config` 元数据，初次验收使用20步、0.4MP、16:9。

受控 API 工作流位于仓库 `workflows/qwen-image21-t2i.api.json`，231 副本为 `/nfs-data/comfyui-qwen-image21/workflows/qwen-image21-t2i.api.json`。

依赖复用现有 `yanwk/comfyui-boot:cu130-slim-v2` 镜像及只读基础包，在独立 packages 目录覆盖 `runtime-requirements.txt` 指定版本；不构建镜像，不升级8188实例。容器以 UID/GID 1000 运行，适配 NAS root-squash。推理使用231的5090；云端无卡实例只提供文件。默认关闭自定义节点与云API节点，模型目录只读。

在231查看迁移进度：

```bash
python3 /nfs-data/comfyui-qwen-image21/progress.py
watch -n 10 python3 /nfs-data/comfyui-qwen-image21/progress.py
```

传输由本机 WSL 的独立后台 SSH relay 执行；退出 Codex 不影响该进程，但必须保持 WSL 运行、两端 SSH 可达。支持已存在 `.partial` 文件断点续传，最多重试5次。完成后校验文件大小、safetensors 张量偏移并记录目标 SHA256，再原子改为正式文件名；已有源 SHA256 时同时比对。`READY` 表示文件就绪，尚不代表真实推理已通过。传输失败会显示 `failed`。

本机查看后台日志和 PID：

```bash
tail -n 30 /tmp/prism-qwen-migration/transfer.log
cat /tmp/prism-qwen-migration/transfer.pid
```

在231管理独立实例：

```bash
docker compose -f /nfs-data/comfyui-qwen-image21/compose.yaml ps
docker logs --tail 50 prism-comfyui-qwen-image21
docker compose -f /nfs-data/comfyui-qwen-image21/compose.yaml stop
docker compose -f /nfs-data/comfyui-qwen-image21/compose.yaml up -d
```

当前只准备推理运行环境与工作流，未增加 Prism 图像任务 handler，未修改 LumenX 业务路由。全部模型就绪后，需要提交工作流、检查 history 成功与 PNG 解码，再决定接入异步 worker。
