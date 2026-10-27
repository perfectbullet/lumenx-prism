# LumenX Prism

- 独立 AI 推理服务；禁止导入同级 LumenX 项目源码，两个仓库仅通过 HTTP 契约集成。
- pip 安装和 Docker Python 依赖默认使用 https://pypi.tuna.tsinghua.edu.cn/simple。
- 配置、模型凭据、运行数据和数据库均独立，不读取 ../lumenx/.env，不连接 lumenxdb。
- 当前实体提取无状态；MongoDB 编排仅预留，接入持久化前明确数据模型及迁移方案。
- 不提交 .env、凭据、数据、个人工作材料；docs/agents/ 仅本地使用。
- 修改后使用 .venv/bin/python -m pytest；测试仅使用本机模拟 provider，不默认调用付费模型。
- 独立、原子的 Git 提交；不添加 Co-Authored-By。未指定远端前不推送。
