# Prism × MiniMax H3：跨镜头角色与画风一致性集成实施方案（Codex 执行稿）

> 文档版本：v1.0 ｜编制日期：2026-10-28 ｜目标仓库：`https://github.com/perfectbullet/lumenx-prism`
>
> 主体读者：**Codex（开发执行代理）**。用户只审阅目录和关键决策；不要把此稿改写成对用户的泛泛科普。
>
> 任务边界：**先 P0 代码审计与 P1 小规模验证；取得证据后再做 P2 产品级集成。** 本文不是“以上全部直接开发”的授权。未经确认不得迁移生产数据库、重做界面框架、升级生产 ComfyUI、部署到线上或发起大量 GPU 生成任务。

---

## 目录

1. [执行任务书与强制规则](#1-执行任务书与强制规则)
2. [已知事实、证据等级与必须先做的 Prism 仓库审计](#2-已知事实证据等级与必须先做的-prism-仓库审计)
3. [五组官方/社区材料：作用、限制与集成映射](#3-五组官方社区材料作用限制与集成映射)
4. [Prism 目标架构与职责边界](#4-prism-目标架构与职责边界)
5. [角色卡 Character Card 页面设计](#5-角色卡-character-card-页面设计)
6. [独立 MongoDB 数据模型、版本锁定与迁移](#6-独立-mongodb-数据模型版本锁定与迁移)
7. [3D Animation Short Generator 在 Prism 的应用级集成](#7-3d-animation-short-generator-在-prism-的应用级集成)
8. [Ref2VA Prompt 编译、逐镜头约束与 H3 Context-IR](#8-ref2va-prompt-编译逐镜头约束与-h3-context-ir)
9. [Ref2VA 与 FL2VA 两条不可混淆的生成路线](#9-ref2va-与-fl2va-两条不可混淆的生成路线)
10. [ComfyUI-H3-Multishot 社区方案：试验性接入](#10-comfyui-h3-multishot-社区方案试验性接入)
11. [小规模 POC：实验矩阵、执行、评分与报告](#11-小规模-poc实验矩阵执行评分与报告)
12. [已知风险、量产障碍与安全边界](#12-已知风险量产障碍与安全边界)
13. [分阶段 Codex 实施任务、验收标准与回滚](#13-分阶段-codex-实施任务验收标准与回滚)
14. [接口契约和参考伪代码](#14-接口契约和参考伪代码)
15. [测试用例与验收清单](#15-测试用例与验收清单)
16. [交付物、回报格式与 Codex 启动指令](#16-交付物回报格式与-codex-启动指令)
17. [原始来源与核验说明](#17-原始来源与核验说明)

---

## 1. 执行任务书与强制规则

### 1.1 业务目标

公司 AIGC 短漫剧 Demo 需要从“全局美术风格”走向“**角色卡及参考图可见、角色身份可锁定、逐镜头可绑定、结果可比较、返工可追踪**”的工作流，主要解决跨镜头生成时：

- 角色脸型、五官、发型不一致；
- 同一场景/连续场景服装与配饰漂移；
- 二维/三维绘制语言、色板、光影和线条漂移；
- 跨片段续接的纹理增生、亮度/饱和度偏移、异常闪烁；
- 生成参数与引用资产不可追溯，无法解释失败，也无法定向重生成。

成功定义不是“Prompt 更长”，而是 **Prism 可以对镜头 S03 回答：引用的是哪一版角色卡、哪几张参考图、哪种 H3 模式、什么 Prompt、何种工作流及参数，为什么通过或未通过审查。**

### 1.2 Codex 执行原则（强制）

1. **实际仓库优先**：以 `perfectbullet/lumenx-prism` 当前分支实际代码、`AGENTS.md` / 项目规则、数据库连接配置与部署文档为唯一实施基准。不要未经确认把公开 `alibaba/lumenx` 上游的 TypeScript/FastAPI 文件路径、数据结构或者路由照搬为 Prism 当前实现。
2. **已存在独立 MongoDB**：Prism 的 MongoDB 是既有基础设施。必须复用它的连接工厂、库名配置、鉴权、项目隔离、迁移和索引约定；**禁止再起一个并行 MongoDB，禁止使用上游业务库作为 Prism 的默认库，禁止用清库重建代替迁移。**
3. **现有 UI 风格优先**：以 `src/lumenx_prism/static/entities.html` 的 DOM、CSS、布局、颜色、交互和路由方式为首要 UI 基准。不得因为上游 LumenX 使用 React/Next.js 就强行给 Prism 引入第二套前端框架。
4. **先审计，再实施**：P0 必须给出实际路径/类名/接口/数据库 collection 的核查矩阵，并提交差异清单；若关键实现与本文假设不同，记录替代方案，再开始写代码。
5. **不破坏旧数据**：所有新增字段向后兼容，既有任务/媒体可读取；幂等、版本并发、回滚必须被验证。
6. **API 密钥不下发浏览器**：云端 H3-Context-IR 等 API 必须由服务端代理且默认为可选；本地视频生成优先现有 ComfyUI。
7. **高成本任务显式确认**：POC 设定最大批量、预算与 GPU 并发；默认只做 smoke test，未通过或无法连通时不得自动大批量提交。
8. **分阶段原子提交**：P0 文档、P1 后端数据与实验脚本、P1 UI、P1 报告等分开 commit；每阶段跑相应测试并汇报，不要把全部工作堆为一个改动。

### 1.3 非目标

- 第一阶段不做 H3 DiT 微调、角色 LoRA 训练、2K 重生成或完整 Agent 平台重构；
- 不替换现有剧本、分镜、任务、媒体存储、成片导出模块；
- 不把 `fully_preserved` 文本标记宣传成模型级“100% 身份硬锁”；
- 不将社区插件的作者实验数字等同于公司 5090 上的已测性能；
- 不直接把 MiniMax Hub 独有的 `hub_generate_*` 或画布 Choice Card API 当作 Prism 已有 API。

---

## 2. 已知事实、证据等级与必须先做的 Prism 仓库审计

### 2.1 事实级别

| 对象 | 已知事实/证据 | Codex 下一步 |
|---|---|---|
| Prism 仓库 | 用户指定 `perfectbullet/lumenx-prism`；本次远程连接未取得它的完整源码 | 在本地已检出的仓库核实默认分支、HEAD、目录结构 |
| Prism UI | 用户提供 `src/lumenx_prism/static/entities.html` 为视觉基准 | 打开原文件，确认可复用布局、CSS、JS、页面路由 |
| MongoDB | 用户明确确认 Prism 使用**独立 MongoDB** | 核实连接层、DB 名、collections、索引、现有迁移机制 |
| GPU | 项目此前有 231 服务器上的 ComfyUI + MiniMax H3 部署记录 | 核查当前环境，不自动假定现有权重是 ref2va |
| 官方 Skill | `h3-prompt-writing` 为可移植文档 Skill，`3d-animation-short-generator` 依赖 MiniMax Hub | 两者采用不同集成方法（见 §7–§8） |
| 公开上游 | `alibaba/lumenx` 有角色参考图、镜头引用、异步 VideoTask、候选生成等机制 | **只作为对照**，不得盲目移植或猜测 Prism 实际架构 |

### 2.2 P0 审计命令（由 Codex 在实际仓库执行）

```bash
pwd
git remote -v
git status --short
git rev-parse HEAD
find . -maxdepth 3 -iname 'AGENTS.md' -o -iname 'README*' -o -iname '*compose*'
rg -n 'MongoClient|motor|pymongo|MONGO_URI|MONGODB|DATABASE_NAME|database_name' src config tests .env.example 2>/dev/null || true
rg -n 'entities\.html|render_template|StaticFiles|Jinja2|APIRouter|Blueprint|character|reference|storyboard|shot|task|comfy|H3|ref2va|fl2va' src tests 2>/dev/null || true
```

不得把这里的命令结果作为已知结论。P0 报告要列出：实际模块、入口、是否复用、建议修改文件、是否有测试、风险级别。

必看对象：

- `src/lumenx_prism/static/entities.html`：角色与实体展示页面；其依赖的 CSS/JS/静态资源和导航机制；
- 后端路由：项目、剧本/镜头、人物/实体、资产、视频任务、视频候选、媒体文件、审批；
- MongoDB：client 的创建/释放、配置读取、已有数据库/collection 名称、主键及 `project_id`；
- ComfyUI：API 代理、workflow JSON 版本、模型权重文件名、`prompt_id` 状态/取消/取回；
- 是否已经有角色卡、reference sheet、候选版本、锁定/发布状态、镜头关联、任务幂等键；
- 测试脚手架及 CI，现有 Demo 用户路径。

**P0 的阶段门**：不能读取上述页面或无法确认 MongoDB 的真实 collection 约定时，禁止进行 schema 迁移，只允许整理代码差异报告。

---

## 3. 五组官方/社区材料：作用、限制与集成映射

| 材料 | 来源级别 | 直接可用部分 | Prism 集成落点 | 不能直接照搬的部分 |
|---|---|---|---|---|
| 官方 H3 架构/参考机制 | 官方 | `ref2va` 保留多模态参考；`fl2va` 首尾帧路线 | 推理 Adapter、路由选择器、参考媒体解析 | “模型支持参考”≠“跨任务严格一致” |
| 官方 **3D Animation Short Generator** | 官方 | 项目简报、故事大纲、角色卡、场景卡、六列表、审核关卡、分镜、生成/回退、Assembly | Prism 现有阶段上的 `ProductionBlueprint` 与状态门 | MiniMax Hub canvas、choice cards、`hub_generate_image/video` **不可直接移植** |
| 官方 **H3 Ref2VA Prompt Guide** 与 `h3-prompt-writing` | 官方 | 六段 Prompt schema、`<Subject N>` / `<Picture N>` 引用、保留分析、音视频规则 | 服务器端 `H3PromptCompiler` + Codex 开发 Skill | 不可把任意 `[characterN:name]` 当 `<Subject N>`；本地与云端调用方式不同 |
| 社区 **H3 Workflow Recipes** | 第三方经验 | 同一张角色原图反复引用、参考分辨率、保存细节、失败回退、链式风险 | POC 参数矩阵与质量评分策略 | 性能主张需要在 231 环境复验 |
| 社区 **ComfyUI-H3-Multishot** | 第三方实验性节点包 | identity bank、链式片段续接、master normalization、音色固定思路 | 可选的 `multishot_experimental` Provider/Workflow | 不作为必选依赖；不可在生产 ComfyUI 上直接覆盖已有节点 |

### 3.1 官方 Skill 的正确技术定位

**两层分开：**

- **开发阶段的 Codex Skill**：读取本地 Markdown Skill 内容辅助产出 Prompt/映射规范；`h3-prompt-writing` 适合安装/引用。
- **Prism 运行时生产能力**：必须写成后端可调用、可测试、可审计的规则引擎与工作流编排，不依赖 Codex 持续在线；`3d-animation-short-generator` 要迁移“规则与产物模型”，不是调用官方 Hub 的不可用工具。

建议新建维护文档 `docs/h3/integration-source-map.md`，记录每个来源仓库、固定 commit SHA、license、实际复制/改写的内容及更新日期。技术实现不可依赖远程 `main` 在运行时自动变化。

---

## 4. Prism 目标架构与职责边界

### 4.1 逻辑结构（按职责，不预判实际语言或模块名）

```text
Prism 既有 Web/UI（沿用 entities.html 样式）
  ├── Script / Art Direction（保留原始创作流程）
  ├── Entities / Character Cards（新增或扩展角色卡浏览）
  ├── Storyboard Shot Detail（逐镜头参考约束及预览）
  └── POC Evaluation（小规模实验结果对比）
              │ REST / 原有项目后端机制
              ▼
Prism 既有后端（复用已有权限、项目、任务、媒体服务）
  ├── CharacterCardService（定妆图与版本锁定）
  ├── ConsistencyResolver（确定性引用解析 + 快照）
  ├── ProductionBlueprintService（官方 3D Skill 规则映射）
  ├── H3PromptCompiler（Ref2VA 六段 / FL2VA base 三段）
  ├── VideoGenerationRouter（能力表驱动 ref2va/fl2va/其他模型）
  ├── ComfyUIAdapter（已有则直接复用）
  └── QualityReview & ExperimentService（评审/实验，先轻量）
              │                        │
              ▼                        ▼
Prism 既有 **独立 MongoDB**        231 ComfyUI / MiniMax H3
角色/版本/镜头/任务/审评记录          prompt→history→media
              │                        │
              └────────► 原有媒体存储 ◄─┘
                           │
                           ▼
                       Assembly 导出
```

### 4.2 架构规则

1. `ConsistencyResolver` **不调用 LLM**：负责稳定版本解析、身份/服饰/风格保留字段、素材顺序、哈希、软/硬约束、冻结 Task Snapshot。
2. `PromptCompiler` 才负责从统一的 `ShotConstraint` 生成**具体模型**的 Prompt。Ref2VA 与 FL2VA 不可共用同一模板。
3. `VideoGenerationRouter` 根据“能力表 + 可用权重 + 工作流部署状态 + 用户策略”选择路由；不得因为某个节点名相似就把 FL2VA 任务送进 Ref2VA 的权重。
4. 每个 H3 job 都要保存 `character_card_version_id`、`reference_asset_version_id`、`style_version_id`、`workflow_version`、`model_checkpoint`、`prompt_snapshot`、`seed`、`steps`、`resolution`、`GPU_seconds`、`review_status`。
5. **静态资产中的真实身份必须源于用户批准的定妆资产**。不能将上一镜头生成画面自动升级为“最新主参考图”；它只能作为可选择的续接辅助素材。
6. 合成只选择已被用户批准的候选版本；质量检测可以标记失败，不可无提示大规模自动重生成。

### 4.3 功能与原有流程对应

| 现有/待核实 Prism 流程 | 加入的能力 | 是否必须先做 |
|---|---|---|
| Script | 角色/场景/镜头唯一 ID，生成 `ProductionBlueprint` 草稿 | P1 可用简版 |
| Art Direction | 全局 style snapshot 和**风格参考图**版本 | P1 手工固定即可 |
| Entities/资产 | Character Card 浏览、上传、版本选择、锁定 | P1 必须有简版页面 |
| Storyboard | 单镜头绑定角色/服装/风格/可选关键帧 | P1 只做实验必要字段 |
| Motion | H3 Ref2VA / FL2VA 双路线、任务快照 | P1 必须 |
| Assembly | 审核选择、失败标签、定向重生成 | P2 产品化 |

---

## 5. 角色卡 Character Card 页面设计

### 5.1 页面目标

**用户必须有一个真正可以打开查看的页面，而不是藏在 MongoDB 里的角色 JSON。** 第一阶段至少支持：查看全部角色卡，点击进入详情，查看大图和多视角参考图，辨认当前正式参考版本，查看服装/风格，执行版本选择与锁定，并知道哪些镜头引用它。

### 5.2 视觉约束

- **以 `src/lumenx_prism/static/entities.html` 为强制样式模板。** Codex 首先读取原文件与依赖，复用现有导航、主题 token、CSS class、卡片、弹层、按钮、响应式断点，不凭空重绘成另一套 React 后台。
- 新页面路由必须和现有 Prism Web 路由一致；优先在实体页内增加“角色卡”视图/详情；若原页面复杂可复用组件有限，再新增邻接子路由。
- 先提供静态/Mock 数据展示测试，通过后接 MongoDB 实际数据；不要以导入未知 CSS 框架为前提。

### 5.3 页面信息架构（线框，不是像素设计图）

```text
┌───────────────── Prism 现有实体管理页导航 / 面包屑 ────────────────┐
│ 角色卡 Character Cards     [按剧本/系列] [状态] [搜索] [+新增]       │
├───────────────────────────────────────────────────────────────────┤
│ [角色缩略图: 林夏] [角色缩略图: 周野] [角色缩略图: ...]              │
│  卡片下: 版本 v3 | 已锁定 | 关联镜头 6 | 需审核                       │
├───────────────── 选中角色林夏 · 角色卡详情 ────────────────────────┤
│ 主定妆图 大图展示        │ 身份清单：脸型/发色/眼色/身形/配饰      │
│ [主图 v3]               │ 服装版本：COSTUME_01                    │
│ [正面][侧面][背面][3/4] │ 风格绑定：STYLE_02                     │
│ [表情][服饰细节][道具]  │ 禁止变化项；允许变化项                  │
│ 图像放大/切换版本        │ [设为正式版本] [锁定/解锁] [查看引用镜头]│
├───────────────────────────────────────────────────────────────────┤
│ 版本历史：v1 / v2 / v3，创建时间、来源、哈希、批准人/时间             │
│ 受影响镜头：S01、S02、S03 ...   [查看镜头]                           │
└───────────────────────────────────────────────────────────────────┘
```

### 5.4 交互验收

- 从 `entities.html` 相同的视觉入口能到角色卡，返回不丢筛选状态；
- 可查看主图、各视角图、放大、版本切换、状态提示；未有图片明确显示占位而非 404；
- 锁定动作有确认，锁定的是**版本号与关联资产快照**；换主图必须创建/批准新版本；
- 镜头引用列表按 `project_id` 过滤，不跨项目暴露其他用户资产；
- 参考图浏览优先使用后端安全媒体接口，不把对象存储凭证放在 HTML；
- 首期允许只读/手动锁定，不要求在页面内生成所有视角图，生成按钮可复用既有资产生成服务。

### 5.5 与官方角色卡映射

最小角色卡内容：名称、角色作用、年龄段/体型、正面/侧面/背面/3/4 视图、表情、服装颜色及细节、标志性道具、必须保留的身份特征、允许变化项、审核状态。对于 2D 短漫剧，不强制套用官方 Skill 默认的“Pixar 风格”；风格由 Prism 的 Art Direction 决定。

---

## 6. 独立 MongoDB 数据模型、版本锁定与迁移

> **命名仅作建议。** Codex 完成 P0 后，以现有 collection 设计为准：可以扩展已有实体，而不必新建相同含义的 collection。绝不能为“一致性”建立第二套角色主数据。

### 6.1 最小实体关系

```text
project (现有)
 ├── character (现有) ─── character_card_versions (新增或内嵌版本)
 │                         └── reference asset version + SHA256
 ├── art_direction (现有) ─── style_versions (新增或扩展)
 ├── storyboard_shot (现有) ─── shot_consistency_config
 │                         └── frozen_reference_snapshot
 └── video_task (现有) ─── generation_snapshot / quality_review
                             └── experiment_run / experiment_case（POC）
```

**核心主键约定**：`project_id`（沿用真实类型）、`character_id`、`card_version_id`、`shot_id`、`task_id`。禁止混用 MongoDB `ObjectId` 与字符串导致找不到数据；应用层统一序列化，索引按实际存储类型建立。

### 6.2 角色卡版本示例（建议模型）

```json
{
  "_id": "cardver_001_v3",
  "project_id": "prism_project_01",
  "character_id": "CHAR_001",
  "version": 3,
  "status": "approved",
  "is_active": true,
  "assets": [
    {"role": "identity_master", "asset_id": "A001", "variant_id": "V003", "sha256": "<sha256>", "media_ref": "managed-media://..."},
    {"role": "front_view", "asset_id": "A002", "sha256": "<sha256>"},
    {"role": "side_view", "asset_id": "A003", "sha256": "<sha256>"}
  ],
  "identity_traits": {"face": "oval", "eyes": "amber", "hair": "black shoulder-length with left bangs"},
  "wardrobe": {"id": "COSTUME_01", "top": "white shirt", "outer": "navy jacket"},
  "immutable_traits": ["face_geometry", "eye_color", "hair_style", "wardrobe_design"],
  "allowed_changes": ["expression", "pose", "camera_angle", "lighting"],
  "approved_at": "<timestamp>",
  "approved_by": "<user_id>"
}
```

版本记录写入后不原地改写 `assets/identity_traits`；需要修改则新建版本。**`is_active` 只能表示当前默认，不应让旧 Task 的快照随之改变。** 在真实 MongoDB 中依业务约束采用条件更新/乐观锁；若不具备多文档事务能力，保证核心引用快照由单文档原子更新产生。

### 6.3 镜头一致性约束及冻结任务快照

```json
{
  "project_id": "prism_project_01",
  "shot_id": "SHOT_003",
  "config_version": 2,
  "reference_bindings": [
    {"slot": 1, "kind": "character", "character_id": "CHAR_001", "card_version_id": "cardver_001_v3", "image_role": "identity_master"},
    {"slot": 2, "kind": "style", "style_version_id": "STYLE_002", "image_role": "style_reference"}
  ],
  "continuity_from_shot_id": "SHOT_002",
  "wardrobe_id": "COSTUME_01",
  "route_preference": "ref2va",
  "preserve_identity": true,
  "preserve_wardrobe": true,
  "preserve_style": true
}
```

冻结的 `generation_snapshot` 至少包含：`resolved_binding_ids`、`media SHA256`、`ordered_references`、`compiled_prompt`、`prompt_schema_version`、`checkpoint/hash`、`workflow_json_hash`、`seed/steps/sampler/scheduler`、`resolution/frames`、`route/model`、`job_id`、`created_at`。提交后不可被后续角色资产切换篡改。

### 6.4 索引与数据安全

建议检查是否已有如下等价索引，再有条件创建：

- `character_card_versions`: `(project_id, character_id, version)` 唯一；
- `shot_consistency`: `(project_id, shot_id)` 唯一；
- `generation_reviews`: `(project_id, task_id)`；
- `poc_experiment_cases`: `(experiment_id, shot_id, treatment_code, replicate_no)` 唯一；
- 对 task idempotency keys 建唯一约束，避免重试造成重复 GPU 计费。

不得在未核实数据库版本/索引支持能力的情况下依赖唯一部分索引或多文档事务。迁移脚本需具备 dry-run、备份说明、重复执行幂等性；旧文档缺失新增字段时按默认逻辑读取，不得自动批量覆盖。

### 6.5 版本变化与失效提醒

新角色卡 v4 发布后：

- 已完成/正在跑的镜头仍关联 v3；
- 未生成镜头可按用户选择继承 v4；
- 使用 v3 的镜头可以标记“参考版本较旧”，但**不自动重生成**；
- 若修改角色卡会影响多个镜头，UI 在确认弹层列出受影响镜头数。

---

## 7. 3D Animation Short Generator 在 Prism 的应用级集成

### 7.1 不能简单 `pip install` 作为运行插件

官方 `skills/3d-animation-short-generator/SKILL.md` 的 `compatibility` 明确写着 **Requires the MiniMax Hub agent**，依赖 canvas、choice cards、`hub_generate_image`、`hub_generate_video` 等。它的内容**可学习和转写为 Prism 规则**，但不具备“拷贝文件即可在 Prism 后端运行”的条件。官方 `h3-prompt-writing` 则是纯文档，可供 Codex 使用。务必区分。

### 7.2 定义 `ProductionBlueprint`（结构化生产计划）

为现有 Prism 项目新增**可选**生产计划文档/接口，其产物对应官方技能要求：

| 官方步骤 | 应保留的产物 | Prism 中对应实体 / 行为 |
|---|---|---|
| STEP 0 | Project Brief（比例、总时长、音频模式、风险） | 现有项目配置扩展 |
| STEP 1–2 | Story Outline、故事确认 | Script 解析结果及审核标记 |
| STEP 3 | Character Cards | §5 角色卡页面与版本锁 |
| STEP 4 | Scene Cards | 场景参考资产与版本锁 |
| STEP 5 | 六列标准镜头信息表 | 结构化镜头字段及连续性锚点 |
| STEP 5.5 | 镜头表自检 | 后端校验与错误列表 |
| STEP 6 | 文本分镜（默认）、可选铅笔分镜 | Storyboard 内容 |
| STEP 7 | 模型选择、分镜级生成及回退 | Ref2VA/FL2VA Router 与 VideoTask |
| STEP 8 | 审批、BGM、合成、最终审核 | Assembly 原有导出能力 |

### 7.3 六列镜头表（强烈建议落地）

必须保留以下列及其**机器可校验字段**：

1. `Shot ID & Duration`：镜头编号与秒数；
2. `Continuity Handoff`：上一镜结束状态、下一镜承接、位置/光照/声音衔接；
3. `Reference Anchors (Spatial + Identity)`：固定地标、角色卡版本与服饰、空间站位、视觉风格；
4. `Hook Type`：叙事钩子/情绪节点；
5. `Shot Description (Per-Second Directives)`：逐秒动作和镜头运动；
6. `Audio & Dialogue Track`：环境声、对白、语言、音效时间点。

MVP 可把这六列存为 JSON 子对象，不要求立即构建复杂表格编辑器。先使它可生成、可预览、可验证。检查每镜头都含 `reference_anchor`、`character_card_version_id`、时长与镜头衔接；任何镜头出现未批准角色卡，不允许进入“正式生成”状态。

### 7.4 审批关卡与失败回退

最小审批关卡：角色卡批准 → 场景卡批准 → 镜头计划批准 → 分镜确认 → 生成候选批准 → 成片输出。系统应保留草稿/已批准/退回状态，避免昂贵生成被未经批准的自动代理触发。

官方 fallback（作为**可配置策略，不自动收费执行**）：

- 第一次失败：强化引用锚点，不换角色主参考；
- 第二次失败：把困难镜头缩短至 ≤6s，必要时拆为相邻镜头并重新检查连续性；
- 第三次失败：提议使用项目支持的替代模型、降低复杂度或人工提供参考视频；
- 多次失败后停止并显式要求用户选择，不要死循环。

**特别注意**：官方默认的 3D/Pixar 风格只是它自身的一种风格模板。Prism 漫剧既可能是 2D 赛璐璐也可能是 3D，不能将默认 Pixar 规则写死为系统全局风格。

### 7.5 推荐的内部 API 契约（路由名称待 P0 与现有 API 对齐）

- `POST /projects/{id}/production_blueprint/draft`：从脚本生成草稿，保存来源/版本；
- `GET /projects/{id}/production_blueprint`：加载六列镜头表与审批状态；
- `POST /projects/{id}/production_blueprint/validate`：校验，无 GPU 调用；
- `POST /projects/{id}/production_blueprint/approve`：审批写入；
- `POST /projects/{id}/shots/{shot_id}/resolve_consistency`：得到结构化预览，不生成视频。

这些是**建议契约**，不是声称 Prism 当前已有路由。具体拆分和命名以已有后端风格为准。

---

## 8. Ref2VA Prompt 编译、逐镜头约束与 H3 Context-IR

### 8.1 正式采用的六段结构

官方 `ref-en.txt` 要求：

1. `subject_definitions`
2. `summary`
3. `retention_analysis`
4. `detailed_description`
5. `overall_soundscape`
6. `non_diegetic_music`

引用标签 `<Subject N>`、`<Picture N>`、`<Video N>`、`<Audio N>` 在各段必须指向同一事物；`fully_preserved` / `partially_preserved` / `attribute_transfer` / `weak_reference` 是**语义标记，不是模型硬性约束开关**。

### 8.2 先统一中间表示，再编译为 H3

新增 `ShotConstraintIR`（名称可调整）——**这不是官方 H3-Context-IR 的内部实现**，而是 Prism 自有的、可序列化的**业务中间表示**：

```json
{
  "shot_id": "S03",
  "duration_s": 5,
  "characters": [{
    "id": "CHAR_001",
    "card_version_id": "cardver_001_v3",
    "identity_ref": "managed-media://characters/linxia-v3.png",
    "preserve": ["face", "hair", "clothes", "silhouette"]
  }],
  "scene": {"id": "SCENE_002", "version": "v2"},
  "style": {"id": "STYLE_002", "version": "v2"},
  "action": "turn toward the window and smile",
  "camera": "slow dolly-in, medium close-up",
  "dialogue": [],
  "route": "ref2va"
}
```

**关键编译原则**：

- `ReferenceResolver` 分配的图/视频/音频索引必须与 **ComfyUI 输入的真实顺序**一致；不可仅把标签写在 Prompt 里，却不上传相应 reference；
- 同一角色跨镜头引用原始已锁定角色卡图，不用 S02 生成结果替代 S03 的主身份图；
- 将服饰、发型、脸型和配饰作为 `retention_analysis` 的精细规则；场景变化与角色身份不冲突；
- 对话严格保留原语言与说话人标识，不能改写台词；
- 编译后做六段标题及顺序、标签可解析、时长、引用计数、缺失图片、脱敏路径检查；失败则拒绝生成；
- 后端保存“编译前 IR + 编译后完整 Prompt + 参考素材列表”，以便重放和审核。

### 8.3 Ref2VA 样例（说明性，非模型实测）

```text
subject_definitions:
<Subject 1> is Lin Xia from <Picture 1>, preserving her oval face,
amber eyes, black shoulder-length hair with left bangs and stable facial proportions.
<Subject 2> is the locked navy jacket and white shirt shown in <Picture 1>.
<Subject 3> is the cel-shaded 2D visual language from <Picture 2>.

summary:
[reference generation] A five-second close-up of <Subject 1> in a classroom,
keeping her clothing and anime visual identity consistent.

retention_analysis:
<Subject 1> ([Shot 1]): fully_preserved - face geometry, eye color, hair and proportions.
<Subject 2> ([Shot 1]): fully_preserved - jacket design, shirt and color palette.
<Subject 3> ([Shot 1]): fully_preserved - linework, shading and drawing language.

detailed_description:
[Shot 1] Medium close-up. <Subject 1> stands by a classroom window,
wearing <Subject 2> in the <Subject 3> style. She turns slowly and smiles.
The camera gently pushes in; no change to her facial structure or wardrobe.

overall_soundscape:
Quiet classroom ambience and soft rain against the window.

non_diegetic_music:
Soft restrained instrumental underscore.
```

请 Codex 用官方 `ref-en.txt` 的完整格式校准示例细节，不要把此简化样例当成唯一合格长度标准。

### 8.4 H3-Context-IR 的可选集成

- **本地闭环默认**：Prism 业务 IR → 本地规则 + 可选 Qwen 多模态增强 → 官方格式编译 → ComfyUI ref2va；
- **云端对照组**：在用户配置 MiniMax API Key 且明确启用时，可用 H3-Context-IR API 生成 Prompt 作为对照；响应需验证并被锁定资产规则复核；
- 官方 H3-Context-IR **不是开源权重**；云端输出不能替代本地版角色绑定与版本快照；
- 禁止将内部未授权图片/音频隐式上传到第三方 API。服务端日志须过滤凭据和敏感媒体 URL。

### 8.5 与 Codex Skills 的关系

Codex 可以通过固定版本的官方 `h3-prompt-writing` Skill 学习格式；**生产请求由 Prism PromptCompiler 的显式校验逻辑执行**。建议在 `docs/h3/prompt-contract.md` 保留 IR/schema 和样例 golden tests，不依赖在线 prompt 套壳。

---

## 9. Ref2VA 与 FL2VA 两条不可混淆的生成路线

### 9.1 核心区别

| 条件/能力 | Ref2VA | FL2VA（含首帧 I2VA） |
|---|---|---|
| 对应权重 | `ref2va` checkpoint | `fl2va` checkpoint |
| 主要条件 | 多张人物/画风/场景/视频/音频参考 | 文本 + 首帧 / 首尾关键帧 |
| 主要目标 | 角色身份、服装、视觉风格与素材参考 | 构图、镜头视觉起止状态及帧间演化 |
| Prompt | 官方 Ref2VA 六段格式 | 官方 base 三段格式 |
| 跨独立镜头角色一致性 | 可提供身份软约束，仍会漂移 | 依赖各镜头事先制作一致静帧，不负责自身建立角色身份 |
| 成本 | 参考输入可能增大开销 | 首尾帧准备需要额外图像生成与审核 |
| 多帧/续接 | 需要验证 workflow 与特定节点兼容性 | 可用于首尾/时间锚点，具体取决于 ComfyUI 版本 |

**默认规则**：

- “新场景/新角度，但同一角色”优先 **Ref2VA + 固定原始定妆参考图**；
- “必须精确保持首尾构图、动作接续”优先 **FL2VA + 已审核镜头静帧**；
- FL2VA 可能通过首帧强制降低初始偏差，但不能自动保持整段乃至多段身份；
- **不默认将 ref2va references 和 fl2va keyframes 混成同一工作流**。社区报告有条件节点冲突，必须先证实本机版本支持后再尝试。

### 9.2 能力表与路由契约

```json
{
  "ref2va": {
    "checkpoint_family": "minimax_h3_ref2va",
    "prompt_schema": "ref_v1",
    "supports_reference_images": true,
    "supports_first_last_keyframes": false,
    "requires_reference_binding": true
  },
  "fl2va": {
    "checkpoint_family": "minimax_h3_fl2va",
    "prompt_schema": "base_v1",
    "supports_reference_images": false,
    "supports_first_last_keyframes": true,
    "requires_reference_binding": false
  }
}
```

此 JSON 是 Prism **MVP 保守路由能力描述**，不是模型所有潜在技术能力的完备声明。`supports_first_last_keyframes=false` 表示**本阶段不允许混合未经验证的工作流**，不代表技术上永久不可能。

### 9.3 ComfyUI 适配

需要确认并保存：

- 当前 ComfyUI 版本、`/object_info` 返回的真实节点输入、导出 workflow JSON；
- `MiniMaxH3ReferenceToVideo` 和 `MiniMaxH3AddGuide` 是否存在/可用；
- 两条工作流分别对应哪个 checkpoint（文件名、hash）、text encoder、video/audio VAE、GGUF mmproj 等；
- `/prompt` 提交 → `/history/{prompt_id}` 查询 → 获取输出媒体；超时/取消/重试；
- 输出是否为本机磁盘、容器路径、或反向代理 URL；Prism 媒体后处理不能直接信任 ComfyUI 返回的任意路径。

兼容性判断**以 `/object_info` 为准，不写死过去版本的节点字段**。禁止绕过现有 GPU 服务，重复启动第二个 ComfyUI 抢显存。

---

## 10. ComfyUI-H3-Multishot 社区方案：试验性接入

### 10.1 价值与边界

社区 `ComfyUI-H3-Multishot` 提出 identity bank、视频续接、跨镜头 latent/帧中继、亮度/对比度归一化、音色锚定等机制，**很值得研究**；但属于第三方实现，无法证明它在 Prism 当前权重和工作流中已经稳定。

- identity bank 与显式参考绑定主要依靠 Ref2VA 的参考 token；`fl2va` 没有等价的参考行，不能将该 bank 当通用特性；
- 多镜头链式生成会积累纹理、亮度、声音频谱漂移；旧片段作为唯一记忆并不安全；
- 社区在长链实验中报告了 `memory_frames=0`、`master_normalize=luma+contrast`、`pin_renorm` 等对漂移的改善，**仅作为待复验的实验参数**。

### 10.2 集成方式

1. 创建**隔离的** ComfyUI 扩展/容器或工作流测试环境，不覆盖 231 上已跑通 Demo 的稳定配置；
2. Pin 社区仓库 SHA，记录 License、节点依赖和工作流格式；安装后检查节点注册及 workflow 是否可 load；
3. 设计两镜头 smoke test，先做固定角色图 + 简单续接，对比原生 Ref2VA；
4. 将 `identity_bank_ref`、`continuity_mode` 作为**可选实验标记**附加在 `generation_snapshot`；
5. 若输出质量未改善、依赖冲突、卡显存或有崩溃，马上回退到原生 H3 工作流；
6. P1 主试验不依赖插件安装成功；若有效，P2 再讨论正式接入。

**禁止**将社区插件实验结果直接表述成 H3 官方承诺的“零漂移”；也禁止把它用于需要精确、无剪辑痕迹的长镜头合成而不审核转场。

---

## 11. 小规模 POC：实验矩阵、执行、评分与报告

### 11.1 试验目标与先决条件

问清三个实际问题：

1. 固定角色卡原图后，跨镜头人脸、发型和服装是否显著改善？
2. 统一画风参考与逐镜头身份约束，是否减少镜头间风格漂移？
3. 已审核的分镜首尾帧 + FL2VA 路线，相比 Ref2VA 是否更适合公司短漫剧生产？

**前置 Gate**：目标模型/节点能在 231 GPU 上成功跑一次 4–6s，Prism 能保存输入输出快照；确认实际已具备 `ref2va`、`fl2va` 对应的权重，否则标记该组 `blocked`，绝不能用另一种权重冒名替代。

### 11.2 固定测试资产

测试短剧《雨后的约定》（仅做 POC，非商业成片）：

- 角色 `CHAR_001` 林夏：黑色齐肩短发，左侧刘海，琥珀色眼睛，白衬衫 + 深蓝外套；
- 风格：二维赛璐璐漫画，稳定勾线粗细/色板/阴影层次；
- 场景：教室、雨后校园，2 个场景卡；
- 角色卡：1 套主参考图 + 侧面/全身选配 + 风格参考图；**一旦批准，试验中不得更新图像内容**；
- 6 个镜头，每镜 4–6s：正面近景 / 3-4 侧脸转头 / 全身站立 / 室外雨中中景 / 侧身行走 / 回头特写；
- 禁止换装与复杂多人互动，确保实验变量可解释。

### 11.3 分组与成本门槛

| 组别 | 处理方法 | 检验性质 | 数量 |
|---|---|---|---|
| A | Prism 当前路线：全局画风 + 文本人物描述，不新增逐镜头约束 | 业务基线 | 6 |
| B | 固定角色卡原图 + 服装/风格参考 + **Ref2VA 六段 Prompt** | 身份参考路线 | 6 |
| C | 基于相同锁定资产先做、审核分镜静帧，再采用 **FL2VA**（首帧/首尾帧按镜头需要） | 关键帧路线 | 6 |
| D（可选） | 两段连续镜头采用 **ComfyUI-H3-Multishot identity bank** | 社区探索，不与 A/B/C 混合计主结论 | 2 |

第一轮 **A/B/C = 18 个主实验视频**；D 仅在主组可运行、插件隔离完成后追加 2 个。**生成前先抽每组 1 个镜头做 smoke test**（最多 3 条主组 smoke，可计入正式组）。主试验预算不足时允许先跑 2 镜头 × 3 组，得到方向性证据后再继续；禁止无限自动重试。

**重要统计说明**：A/B/C 跨 checkpoint、任务条件有差异，属于“**生产策略对比**”，不是严格单因素因果试验。若 B 组接口允许同权重不附参考，可另外增加同一 Ref2VA checkpoint 下的 1–2 镜头 no-ref 对照，用于隔离“有无参考图”的影响；不支持则如实记录。

### 11.4 运行参数与记录要求

每项记录：`experiment_id` / `shot_id` / `group` / `project_id` / `character_card_version_id` / `style_version` / `reference media SHA256` / checkpoint SHA / ComfyUI version / workflow hash / sampler / scheduler / steps / seed / 目标帧数 / 输出帧数 / 时长 / 分辨率 / GPU 显存峰值 / GPU 秒数 / 执行时间 / task status / 视频位置 / 失败类型 / 重试原因。

原则：同路线内尽可能固定参数和 seed；跨路线记录配置差异，不用“不同模型相同 seed”冒充严格一致；镜头文字语义必须相同；每个镜头 A/B/C 对比顺序随机化供评审使用，避免提示偏差。

### 11.5 评审量表与合格线（探索性阈值）

| 维度 | 权重 | 主要核查 |
|---|---:|---|
| 人物身份 | 30% | 脸型、眼睛、发型、身形比例 |
| 服饰一致性 | 20% | 颜色、款式、配饰，无凭空换装 |
| 画风一致性 | 25% | 线条、面部绘制语言、阴影、色板 |
| 时间稳定性 | 15% | 闪烁、重复纹理、局部变形、黑帧 |
| 镜头连续性 | 10% | 转场、空间站位、情绪、声画衔接 |

每维 1–5 分，计算加权百分制。建议阈值（**未实测，需校准**）：综合 ≥80/100、身份项 ≥4/5、严重身份错误 0、生成视频/音频技术性损坏 0。报告同时展示**首轮合格率、一次合格率、GPU 秒/镜头、人工分钟/镜头、重试次数、最终合格率**，不要只呈现最漂亮的成功案例。

对每段视频抽取首帧、1/3、2/3、末帧并结合完整播放；动漫风格不宜单独以真人 face embedding 作为最终裁判。优先人工盲评 2 人 + 图片/视频多模态模型辅助，不将自动评分视为地面真值。

### 11.6 POC 页面（复用 entities.html 视觉体系）

同一 Prism 页面组内新建轻量实验页或标签：顶部实验名称/状态/组别/截止预算；左侧镜头列表；主区以同一镜头 A/B/C 三列同步预览；每列显示参考图、生成路线、checkpoint、Prompt 快照、生成耗时、质量评分；底部展示失败标签、备注、批准/返工按钮。新增“导出实验报告 Markdown/CSV”及对照视频/关键帧拼图。

拒绝把试验页变成无法回溯的临时 Python 脚本输出；至少必须有**可查看的实验结果页面**。但 P1 阶段可先实现最简 HTML 只读对比页，复杂评分交互放到 P2。

### 11.7 报告模板

POC 输出 `docs/h3/reports/YYYYMMDD-prism-h3-consistency-poc.md`，结构：

1. 环境与代码/工作流版本清单；
2. 实验目的、假设、固定与变化因素；
3. 角色卡/参考图展示及 hash；
4. A/B/C 镜头对照索引（指向实际媒体）；
5. 人物/服饰/画风/时间稳定性对比表；
6. 失败案例（必须保留）及复现方法；
7. 每路线 GPU 秒数、CPU RAM/VRAM 与人工工时；
8. 是否达成 P2 实施条件；
9. 风险、未解决问题和下一步建议。

---

## 12. 已知风险、量产障碍与安全边界

| 风险 | 证据级别 | 现象 | 对策与验收方式 |
|---|---|---|---|
| 跨任务身份/服饰漂移 | 官方材料支持“参考机制”；社区指出难题 | 同名角色脸/衣服不一致 | 固定 master asset 版本，逐镜头引用与盲评；不要过度承诺 |
| 链式镜头“错误传递” | 社区报告 | 连续 5–10 镜头纹理/色彩逐次偏移 | 每段保留原始角色卡锚点，尽量不把上一段作为唯一身份源；分组比较归一化开关 |
| Ref2VA 与 FL2VA 条件混用 | 社区实现报告 | 特定工作流条件序列不匹配、崩溃 | 两条路由分开，工作流静态预检，未验证不混用 |
| 角色远景识别失真 | 社区经验 | 远景人脸模糊，错误身份无法判断 | 中/近景设关键识别镜头，远景只测构图/服装 |
| 显存限制/量化损失 | 环境相关 | 5090 32GB 仍可能 CPU offload、OOM、耗时很长 | 实测当前权重/量化/显存峰值，限制 GPU 并发，不推测“实时” |
| 视频黑屏/音频异常 | 第三方 WanGP 等个别报告 | 部分 allocator/量化/编译组合长视频失败 | 对 Prism 自身环境做完整解码、黑帧率、静音检测；不直接迁移其修复参数 |
| ComfyUI 插件冲突/节点升级 | 第三方实验性插件 | workflow 不可导入、参数变化、节点覆盖 | 独立环境 + pin SHA + `/object_info` schema 检测 + 回滚 |
| Prompt 标记不被模型遵循 | 官方 Prompt 结构是软参考 | `fully_preserved` 仍能生出变脸 | 一致性评审 + 分镜静帧审核 + 局部重试 |
| H3-Context-IR 未开源 | 官方 | 私有云端服务、成本与合规 | 本地 Compiler 必须独立运行；云端仅 opt-in |
| 模型许可证 / 地域及商业条款 | 官方 License/FAQ | 演示产品需要遵循模型使用条件、标识和内容安全 | 在正式上线/对外展示前复核最新 H3 Community License，包括商用阈值、署名和地域条款 |
| 人脸相似评测不适合动漫 | 工程判断 | 真人面部识别误判漫画脸 | 自适应人工评审 + 属性级检测，留误判案例 |
| Mongo 版本竞争与旧数据 | 项目特定 | 生成时切角色参考，旧镜头引用悄悄变更 | 使用冻结 task snapshot、乐观锁、幂等迁移、旧字段兼容 |
| 数据泄露/版权/IP | 合规 | 角色图/音频传第三方或参考人物无授权 | 默认本地处理，服务端密钥、素材授权与审计、隔离 project_id |

需把风险与缓解措施同步到 POC 报告，不能只留在技术方案。

---

## 13. 分阶段 Codex 实施任务、验收标准与回滚

### P0 — 仓库与环境审计（**第一条必须执行的任务**）

**任务**：确认 Prism 的实际代码架构、`entities.html` 样式、独立 MongoDB、ComfyUI 连接、权重与节点、已有角色/镜头/候选逻辑。创建 `docs/h3/prism-current-architecture-audit.md`。

**通过条件**：矩阵包含真实文件、函数、端点、collection、需要修改的最小代码范围；列明不可访问/未部署的部分；验证 MongoDB 非破坏性读取；记录 workflow/export/hash。

**失败退出**：无 MongoDB 权限 / 无当前源码 / 无 ComfyUI 元信息时，不进行 P1 写操作，报告阻塞项。

### P1-A — 数据与定妆版本能力（最小闭环）

**任务**：补齐角色卡 version、style version、shot constraint 和 generation snapshot；追加幂等迁移与测试；不得重建用户现有 collection。

**通过条件**：同角色 v3/v4 能共存；S01 绑定 v3 的任务在切换为 v4 后仍可查原快照；并发选版不出现脏读；迁移可重复执行；旧项目可打开。

### P1-B — Character Card 页面

**任务**：基于 `src/lumenx_prism/static/entities.html` 的真实 CSS/布局添加角色卡列表/详情/图集/版本/锁定/关联镜头 UI。首先复用当前页面资产与路由，无需引入新前端技术栈。

**通过条件**：真实 MongoDB 测试资产可展示，主图和多视角可点开；版本和锁定操作持久化；无未授权跨项目读写；页面视觉风格与 entities.html 一致。

### P1-C — H3 Prompt/Route/ComfyUI 最小集成

**任务**：实现 `ConsistencyResolver`、Ref2VA 六段编译与校验、FL2VA base 编译与校验、能力表路由及 ComfyUI Adapter（已有则扩展）。

**通过条件**：Ref2VA/FL2VA 两套输入独立；对同一角色卡生成两个任务快照；导出并校验 workflow；接口返回原有任务格式；配置错误时 fail-fast；不会拿 FL2VA 权重跑 Ref2VA 条件。

### P1-D — 小规模实验与对照查看页

**任务**：固定六镜头和角色卡，执行 ≤18 个主组视频的计划（前置 smoke）；记录消耗与评分；提供 entities.html 同风格实验对比页/最小只读表；生成 POC 报告。

**通过条件**：结果支持镜头 A/B/C 对比；每段视频可定位到任务快照及原始参考；可计算质量和 GPU 成本；失败视频保留复现数据；报告说明实际改善及代价。

### P2 — 基于 POC 结果的产品化（**待用户审批**）

**任务**：正式加入 3D Animation Short Generator 六列表、审批关卡、批处理编排、自动一致性评估、局部返工、跨片段 Multishot 可选插件。不要未经用户允许在 P1 完成时顺手开启 P2。

**通过条件**：用户能从剧本经定妆、镜头约束、视频候选审批直到 Assembly；关键字段有审计；不破坏当前演示版本；合规审查完成。

### 提交与回滚策略

- 每个阶段独立 Git commit / 测试报告；
- 新逻辑使用项目配置开关 `H3_CONSISTENCY_EXPERIMENT_ENABLED`（可换已有 feature flag 系统）；关闭时原流程不变；
- 数据迁移保留旧字段，不可删除已有角色或任务记录；
- 独立 ComfyUI 实验工作流失败时删除/禁用实验扩展即可回退；
- 未经用户允许不 push 到主分支、不部署、不触发商业 API 批量调用。

---

## 14. 接口契约和参考伪代码

> 所有 URL 均为建议示例。Codex 必须适配仓库现有 API 命名、认证、响应格式及 Mongo persistence。

### 14.1 角色卡

```http
GET    /api/projects/{project_id}/character-cards
GET    /api/projects/{project_id}/character-cards/{character_id}
POST   /api/projects/{project_id}/character-cards/{character_id}/versions
POST   /api/projects/{project_id}/character-cards/{character_id}/versions/{version_id}/approve
POST   /api/projects/{project_id}/character-cards/{character_id}/versions/{version_id}/activate
GET    /api/projects/{project_id}/character-cards/{character_id}/shot-usage
```

### 14.2 镜头与 Prompt

```http
GET    /api/projects/{project_id}/shots/{shot_id}/consistency
PUT    /api/projects/{project_id}/shots/{shot_id}/consistency
POST   /api/projects/{project_id}/shots/{shot_id}/resolve-consistency
POST   /api/projects/{project_id}/shots/{shot_id}/compile-h3-prompt
POST   /api/projects/{project_id}/shots/{shot_id}/generate-h3
```

`resolve-consistency` 必须无 GPU 调用；`compile-h3-prompt` 先仅本地处理，无云端 API；`generate-h3` 通过已有 Task 层异步执行且附 idempotency key。

### 14.3 实验

```http
POST   /api/projects/{project_id}/h3-experiments
GET    /api/projects/{project_id}/h3-experiments/{experiment_id}
POST   /api/projects/{project_id}/h3-experiments/{experiment_id}/preview-plan
POST   /api/projects/{project_id}/h3-experiments/{experiment_id}/start
POST   /api/projects/{project_id}/h3-experiments/{experiment_id}/cases/{case_id}/review
GET    /api/projects/{project_id}/h3-experiments/{experiment_id}/report
```

### 14.4 后端伪代码（语言与文件布局由真实仓库决定）

```python
# 仅表示流程边界，不能直接当成现有 Prism 源码。
def prepare_h3_generation(project_id, shot_id, route, idempotency_key):
    shot = repository.load_shot(project_id, shot_id)
    constraints = repository.load_shot_constraints(project_id, shot_id)
    frozen = consistency_resolver.resolve_approved_versions(project_id, shot, constraints)
    assert frozen.character_card_is_approved
    assert frozen.media_hashes_verified
    assert route in capability_registry.available_routes()
    prompt = h3_prompt_compiler.compile(route, shot, frozen)
    prompt_validator.validate(route, prompt, frozen.ordered_media)
    task = tasks.create_once(
        project_id=project_id, shot_id=shot_id,
        idempotency_key=idempotency_key,
        generation_snapshot=freeze(frozen, prompt, route)
    )
    return task

async def execute_h3_task(task):
    workflow = workflow_registry.load_pinned(task.snapshot.route)
    payload = workflow.bind(task.snapshot.prompt, task.snapshot.ordered_media)
    workflow.validate_against_comfy_object_info(payload)
    comfy_prompt_id = await comfy_adapter.submit(payload)
    tasks.save_provider_job_id(task.id, comfy_prompt_id)
    result = await comfy_adapter.wait_and_fetch(comfy_prompt_id)
    checked = media_validator.validate_audio_video(result)
    tasks.finish(task.id, checked.media_ref, checked.technical_flags)
```

**不得**把真实媒体下载地址、JWT、AccessKey 存在给前端可读的任务快照中；展示层只返回受控预览 URL。

---

## 15. 测试用例与验收清单

### 15.1 单元测试

- [ ] 同一角色两个版本的锁定与快照不可变；
- [ ] 角色切换主图不会改变历史任务 snapshot；
- [ ] 角色名相同但 ID 不同不会错绑；
- [ ] 多图参考按固定顺序去重，不使用无序 `set` 导致标签错位；
- [ ] Ref2VA 六段字段顺序正确；每个 `<Picture N>` 与实际附件完全一致；
- [ ] FL2VA 绝不意外引用 Ref2VA bank；
- [ ] 缺失参考图、失效 URL、错误媒体类型和不批准的卡片拒绝提交；
- [ ] API 重试同一幂等键不会重复创建 GPU 工作；
- [ ] 未配置 H3/ComfyUI 时旧 Prism 功能不受影响。

### 15.2 MongoDB 集成测试

- [ ] 使用专用测试数据库/命名空间，不碰真实生产数据；
- [ ] 索引创建不破坏旧 collection；
- [ ] 迁移重复执行两遍仍然幂等；
- [ ] 同一角色并发批准/切换仅有一个生效版本；
- [ ] 所有 API 都按项目隔离查询；
- [ ] 删除/归档角色时引用镜头和旧媒体不会发生悬空不可追溯状态。

### 15.3 UI 与操作测试

- [ ] `entities.html` 同风格角色卡入口正常；
- [ ] 大图预览、缩略图、版本选择、锁定、镜头引用列表正常；
- [ ] 分镜可显示引用的具体版本及“历史任务仍用旧版”提示；
- [ ] A/B/C 实验页面能加载失败和成功案例；
- [ ] 小屏查看不出现重要操作被遮挡；
- [ ] 对高成本生成、资产切版与删除有显式确认。

### 15.4 GPU 与结果验收

- [ ] 两条 workflow 从实际 ComfyUI 导出并经 `/object_info` 校验；
- [ ] 生成结果可播放，有正确音轨/帧数/分辨率；
- [ ] 黑帧、音频异常和视频解码错误可自动标记；
- [ ] 每段生成可追溯权重/工作流/输入参考；
- [ ] 连续生成受控于并发和预算阈值，无无限重试；
- [ ] 失败不影响已批准镜头，能选择只重生成单镜头。

---

## 16. 交付物、回报格式与 Codex 启动指令

### 16.1 必须交付

- `docs/h3/prism-current-architecture-audit.md`：P0，真实代码路径 + 独立 MongoDB + ComfyUI 环境；
- `docs/h3/integration-source-map.md`：官方/社区材料固定版本、作用与许可；
- `docs/h3/prompt-contract.md`：Ref2VA/FL2VA 输入输出契约、golden samples；
- 角色卡页面、后端服务、Mongo schema 修改及相应测试（P1 通过审计后）；
- `docs/h3/reports/YYYYMMDD-prism-h3-consistency-poc.md`：实验结果，必须含失败案例、视频/帧链接、性能/评分/风险；
- 简短 `README` 或导航链接，便于在 Prism 代码库找到以上文档。

### 16.2 每个阶段完成后 Codex 必须汇报

```text
阶段：P0 / P1-A / P1-B / P1-C / P1-D
实际分支与 HEAD：...
更改文件：...
审计/实现结论：...
测试命令与结果：...
MongoDB 数据影响：...
ComfyUI/GPU 调用量：...
已知阻塞及回退方式：...
下一阶段是否需要用户批准：是/否
```

### 16.3 可以直接交给 Codex 的启动指令

> 阅读项目根目录的 `AGENTS.md` 及本文件；**只执行 P0 代码审计，不进入 P1**。特别检查用户指定的 `src/lumenx_prism/static/entities.html` 页面视觉体系、Prism 现有独立 MongoDB、角色/镜头/任务数据模型以及 231 ComfyUI 的 Ref2VA/FL2VA 权重与 Workflow 能力。产出真实文件路径、端点、collection、当前缺口与最小改造计划，写入 `docs/h3/prism-current-architecture-audit.md`。不要迁移/清理 MongoDB，不要升级生产 ComfyUI，不要触发大批量生成，不要重构前端框架。P0 报告通过后，再依据本文依次执行 P1-A 至 P1-D；P2 必须等用户明确批准。

---

## 17. 原始来源与核验说明

> 下列来源是 Codex 的**原始核查清单**，优先阅读指定文件，再读取其关联 `references/*.md`。不要依赖转述、旧文章或未经确认的社区推理结果。建议在项目 `integration-source-map.md` 为每条来源记录 Git commit SHA 与读取日期。

### 官方（优先级最高）

1. MiniMax H3 官方仓库/权重/部署说明：<https://github.com/MiniMax-AI/MiniMax-H3>
2. MiniMax H3 官方中文 README（说明哪些 Skill 可移植）：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/README.zh-CN.md>
3. **3D Animation Short Generator 主 Skill**：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/SKILL.md>
4. **3D Animation Short Generator 六列表规范**：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/references/shot-table-spec.md>
5. **3D Animation Short Generator 失败回退**：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/references/fallback-policy.md>
6. **3D Animation Short Generator 模型选择**：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/3d-animation-short-generator/references/model-selection.md>
7. **H3 Prompt Writing Skill**：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/SKILL.md>
8. **H3 Ref2VA Prompt Guide**：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/ref-en.txt>
9. **H3 FL2VA/base Prompt Guide**：<https://github.com/MiniMax-AI/MiniMax-H3/blob/main/skills/h3-prompt-writing/references/base-en.txt>
10. ComfyUI H3 节点源码：<https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_minimax_h3.py>
11. 官方 H3 License/FAQ 与商业条款：<https://design.minimax.io/h3>；正式上线以仓库 LICENSE/具体协议现行原文为准。

### 社区（必须单独验证）

12. **H3 Workflow Recipes**：<https://github.com/alperktt/awesome-minimax-h3-skills/blob/main/skills/h3-workflow-recipes/SKILL.md>
13. **ComfyUI-H3-Multishot**：<https://github.com/jlucasmcrell/ComfyUI-H3-Multishot>
14. LumenX 开源上游，仅作架构对照：<https://github.com/alibaba/lumenx>
15. **Prism 当前项目**（实际代码为准）：<https://github.com/perfectbullet/lumenx-prism>

### 本稿中的不确定性声明

- 本稿无法直接审阅 `perfectbullet/lumenx-prism` 当前实际代码及 `entities.html` 具体 CSS/DOM，**所有与实际落地路径有关的细节必须由 Codex 的 P0 审计确认**；
- 文中路由名称/collection 名、JSON 示例、评分阈值和实验成本上限均为建议方案，不应冒充当前已实现接口；
- 官网声称的参考机制和社区成功案例不等于 Prism 已成功通过测试；质量改善以 231 本机实测为准；
- 任何商业发布应根据发布时有效的 H3 Community License 与适用法规再次复核。

---

**结束条件**：当 P0 完成且 P1 实验报告显示可复现的角色身份改善、可接受的显存/GPU 成本，并且 MongoDB 旧数据无回归时，才建议把“角色卡审批 → 镜头参考锚点 → 模型路由 → 一致性审核”推广为 Prism 的正式量产工作流。
