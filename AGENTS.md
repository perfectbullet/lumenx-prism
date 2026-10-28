# LumenX Prism

- 独立 AI 推理服务；禁止导入同级 LumenX 项目源码，两个仓库仅通过 HTTP 契约集成。
- pip 安装和 Docker Python 依赖默认使用 https://pypi.tuna.tsinghua.edu.cn/simple。
- 配置、模型凭据、运行数据和数据库均独立，不读取 ../lumenx/.env，不连接 lumenxdb。
- 使用现有 MongoDB 实例的 lumenx_prism 数据库和 prism_app 账号做数据库级隔离，不创建独立 MongoDB 容器。实体提取可记录实验输入与结果，不写业务项目/资产。
- 模型运行配置仅从 MongoDB 的 model_comfigs 读取；.env 仅用于数据库连接和服务 token，不得添加模型环境变量回退。语音/图像/视频目前仅支持配置保存，不代表已接入生成。
- 不提交 .env、凭据、数据、个人工作材料；docs/agents/ 仅本地使用。
- 使用独立 Conda 环境 lumenx_prism；修改后在该环境中运行 python -m pytest；测试仅使用本机模拟 provider，不默认调用付费模型。
- 独立、原子的 Git 提交；不添加 Co-Authored-By。未指定远端前不推送。
