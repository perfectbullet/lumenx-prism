# 剧本制作规划与按场分镜

入口 `/tools/scripts`：优先从作品库选择已确认的作品、分集版本和集；兼容载入旧实体提取任务原文或直接粘贴剧本；目标时长和画风可留空。点击分析后由独立文本 worker 推理，使用 MongoDB `model_comfigs` 已绑定的“用于分镜规划”文本模型，没有模型环境变量回退。

分析结果区分原文标注的目标时长、用户指定目标、内容估时和可编辑制作预算。识别已有集与场次，不把下集预告自动扩写成下一集。内容估时不是实际成片时长，需通过对白试读和剪辑校准。单场超180秒时要求拆成连续子段；单场2～36镜头，每镜头仍4～15秒。

参考 MiniMax-H3 `skills/3d-animation-short-generator/SKILL.md` STEP 0/1/2 与 `references/shot-table-spec.md` STEP 5/5.5。已有完整剧本采用识别、归纳和制作规划，不强制改写为新故事；画风遵循用户输入，不套用默认3D风格。Hub画布操作映射为页面与Mongo记录。

原文按保留换行的行号建立不可变范围，每场覆盖连续原文，所有场次必须完整覆盖原文且不能重叠；开头元信息归首场，片尾/预告归末场。模型输出必须引用有效范围，但范围校验不代表剧情、对白或估时已经人工验收。保存后的 `script_plans` 有 revision；可编辑集标题、摘要、画风、共享人物服装约定、单场预算与镜头数、所需素材名称。原文范围及集场映射不能通过编辑规划修改；需重新分析创建新来源。

确认保存后点击某场“进入本场分镜”，选择缺少的角色、场景和道具图片再提交。默认按素材名称匹配；可在本场明确设置 asset_bindings，将三屏工位绑定到包含该物件的办公室图，或将纸质笔记绑定到已选纸质笔记本图。对应关系随任务冻结，不自动猜测。可回到规划修正模型误判的素材清单。单场输入由服务器从已确认版本构建，冻结原文片段及哈希、行号、集摘要、共享角色/服装/风格、前后场摘要和选定图片；不从浏览器接收伪造的来源上下文。分镜按场独立进入现有异步任务，可单独重做；新版本不改变旧任务。任务页仍可查看旧版本快照，但新提交必须使用当前规划版本。

## HTTP

- `POST /v1/scripts/plan/jobs`：script、source_entity_job_id?、style?、target_duration?；Idempotency-Key，返回202及Location。
- `GET /v1/jobs/{id}`：规划任务类型script_planning，结果含content、declared_duration、review_required。
- `GET/POST /v1/script-plans`、`GET/PUT /v1/script-plans/{id}`：读取、保存确认规划；PUT要求revision。
- `GET /v1/script-plans/{id}/scene-jobs?revision=N`：该版本逐场分镜任务历史。
- `POST /v1/script-plans/{id}/scenes/{scene_id}/storyboard/jobs`：revision、assets、asset_bindings?、aspect_ratio；Idempotency-Key，返回现有storyboard_generation任务。

规划任务冻结输入、提示词、无密钥配置快照和原始输出。文本推理中断不自动重发；已保存输出可在worker重启后继续解析。规划与分镜共用文本worker，不占用GPU；不会自动调用图像或视频生成。

原有短片接口保持最多45秒、6镜头；按场接口支持180秒、36镜头。既有单场分镜可保存到原分镜草稿供查看或单镜头视频使用；本轮不扩展整集合成、自动批量生成、分镜错误修订或旧页面错误提示。

测试样本文件继续保留在 static/samples，但规划页不再显示测试样本入口。可通过作品库作为命名的测试作品导入；原实体提取历史不变。
