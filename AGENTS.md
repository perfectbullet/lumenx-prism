# LumenX Prism

- 独立 AI 推理服务；禁止导入同级 LumenX 项目源码，两个仓库仅通过 HTTP 契约集成。
- pip 安装和 Docker Python 依赖默认使用 https://pypi.tuna.tsinghua.edu.cn/simple。
- 配置、模型凭据、运行数据和数据库均独立，不读取 ../lumenx/.env，不连接 lumenxdb。
- 使用现有 MongoDB 实例的 lumenx_prism 数据库和 prism_app 账号做数据库级隔离，不创建独立 MongoDB 容器。实体提取可记录实验输入与结果，不写业务项目/资产。
- 模型运行配置仅从 MongoDB 的 model_comfigs 读取；.env 仅用于数据库连接、服务 token、媒体目录与公开服务地址，不得添加模型环境变量回退。图像已有 ComfyUI Qwen-Image21 异步适配；分镜规划、H3 Ref2VA 图片参考视频和预览合成已有异步接口；语音仍仅支持配置保存。其他视频路线未验证不得宣称支持。
- 不提交 .env、凭据、数据、个人工作材料；docs/agents/ 仅本地使用。
- 使用独立 Conda 环境 lumenx_prism；修改后在该环境中运行 python -m pytest；自动化回归测试使用模拟 provider；研发联调与效果评测可按需调用已配置的真实付费模型，无需再次单独确认。
- 完成代码修改和验证后，保留工作区改动并说明修改内容，供用户先审阅。不要自动执行 git add、git commit 或 git push；只有用户明确要求时才执行相应操作。用户要求提交时，两个项目分别按独立功能点做原子提交；不添加 Co-Authored-By。未指定远端前不推送。
