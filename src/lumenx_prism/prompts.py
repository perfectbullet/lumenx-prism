"""Entity extraction prompt, independent of Studio business models."""

DEFAULT_ENTITY_EXTRACTION_PROMPT = """
你是一名专业的分镜师与编剧。
分析下面的小说文本，为漫画/视频制作提取结构化数据。

重要：
- 所有描述性内容（名称、描述）必须使用中文（简体中文）。
- 只提取角色（characters）、场景（scenes）和道具（props）。

严格按以下结构输出合法 JSON：
{
    "characters": [
        {
            "id": "char_001",
            "name": "角色名（如 '叶墨'、'叶墨 (古装)'）",
            "description": "外观描述（发型、眼睛、体型、显著特征）。不要包含具体的面部表情（如 悲伤、愤怒）或临时动作（如 奔跑、哭泣）。聚焦于长期固定的外形特征。",
            "age": "年龄估计（如 '25'）",
            "gender": "性别",
            "clothing": "默认服装描述。若某角色服装有显著变化（如 从便装换成婚纱），为每个服装变体单独创建一个角色条目，并取一个有区分度的名字（如 '名字 (服装)'）。",
            "visual_weight": 5  // 1-5 重要度
        }
    ],
    "scenes": [
        {
            "id": "scene_001",
            "name": "地点名（如 '咖啡店'、'古代遗迹'）",
            "description": "外观描述（光线、氛围、关键元素）",
            "visual_weight": 3
        }
    ],
    "props": [
        {
            "id": "prop_001",
            "name": "道具名",
            "description": "外观描述"
        }
    ]
}

文本：
{text}
"""


def build_entity_prompt(text: str, custom_prompt: str = "") -> str:
    if custom_prompt and custom_prompt.strip():
        if "{text}" in custom_prompt:
            return custom_prompt.replace("{text}", text)
        return f"{custom_prompt}\n\nText:\n{text}"
    return DEFAULT_ENTITY_EXTRACTION_PROMPT.replace("{text}", text)
