"""Production rules adapted from MiniMax-H3 3d-animation-short-generator STEP 3/4.
Source revision: 199b7d0e1fd95e7b3eafa8c15eca692534142427. No Hub dependency.
"""
import json
import re
from .image_schemas import ImageRequest
from .style_rules import adapt_visual_style

PROMPT_VERSION = 'asset-production-v6'
SKILL_REVISION = '199b7d0e1fd95e7b3eafa8c15eca692534142427'


def compile_image_prompt(request: ImageRequest):
    r = request
    if r.prompt_mode == 'raw':
        return r.prompt, r.negative_prompt
    negative = r.negative_prompt
    parts = [f'Asset: {r.name}. {r.description}', r.prompt]
    single_instruction = None
    appearance_directive = None
    if r.character_card:
        card = r.character_card
        parts = [f'Character: {r.name}.', r.prompt]
        current = [('年龄 / Age',card.age_range),('体型 / Body type',card.body_type),
                   ('发型 / Hairstyle',card.hairstyle),('服装及颜色 / Required outfit',card.costume)]
        appearance_directive = ('本次权威角色细节 / CURRENT REQUIRED APPEARANCE: '+
            '；'.join(label+': '+value for label,value in current if value)+'. '+
            '不可改变的身份特征优先；其余当前非空细节覆盖辅助描述和参考图中冲突的旧年龄、发型与服装。'
            'Current explicit details override conflicting older age, hair and clothing in background descriptions or references. '
            'Keep the immutable identity traits. Render the specified current outfit and age.')
        identity = '; '.join(card.immutable_traits)
        specification = card.model_dump()
        if r.generation_type != 'reference_sheet':
            specification.pop('expressions', None)
        parts += [appearance_directive, 'Identity lock: ' + identity,
                  'Character appearance: ' + json.dumps(specification, ensure_ascii=False)]
        if r.description:
            parts += ['辅助外貌描述（低优先级；仅补充当前角色细节未说明的内容；忽略冲突的旧值） / Secondary background description: '+r.description]
        if card.props:
            parts += ['道具只呈现明确的实体物品；习惯、喜好不直接推断为胡须或手持物。 Only explicit physical props are drawable; preferences do not imply facial hair or held objects.']
        if r.generation_type == 'reference_sheet':
            parts += [
                'Create a labeled production CHARACTER REFERENCE CARD, landscape layout. '
                'Large main three-quarter (3/4) full-body view on the left; '
                'FRONT, SIDE and BACK full-body turnaround views on the right, same scale. '
                'Include an expression strip: ' + ', '.join(card.expressions) + '. '
                'Include separate close-up panels for MATERIALS, COSTUME colors and details, '
                'and each listed PROP (if no props are specified, label that panel NONE). '
                'Clearly label character name and role, each view, expression, costume and prop. '
                'Add a short readable VISUAL ID note with age range, body type, hairstyle, outfit colors, '
                'signature props and DO NOT CHANGE traits. Keep labels outside the figures. '
                'All views portray exactly the SAME person wearing the SAME outfit. '
                'Clean neutral background, full bodies including shoes, production-design layout.'
            ]
        elif r.generation_type == 'three_view':
            parts += [
                'Create exactly THREE full-body turnaround views: FRONT / SIDE / BACK, '
                'each depicting the same character, same face, hair, costume and proportions. '
                'One row, three evenly spaced figures at the same scale on a plain neutral background. '
                'No fourth angle, no portrait inset, no expression strip or detail panels.'
            ]
            negative += ', extra views, extra panels, expression strip, detail panels'
        elif r.generation_type == 'full_body':
            single_instruction = (
                '只生成一个人物的一张正面全身立绘。从头顶到鞋底完整入镜，站立，双脚可见，'
                '人物居中，纯色背景。画面中只能出现一个人物、一个正面角度。'
                '不要侧面、背面、多角度并排、参考卡、细节小图或文字。'
                'Output exactly ONE person in ONE front-facing full-body image, head to toe, '
                'neutral standing pose, feet visible, centered on a plain background. '
                'No turnaround, no contact sheet, no panels, no other views, no text.'
            )
            negative += ', multiple figures, duplicate person, turnaround, three views, reference sheet, contact sheet, panels, inset images, text, labels, cropped feet'
        elif r.generation_type == 'headshot':
            origin = ('将参考图中的同一个人物重新绘制为一张单人头像特写。'
                      '参考图用于身份、脸型、发型和服装，不保留原图的排版。'
                      if r.reference_media_ids else
                      '根据下方人物描述设计一张单人头像特写。')
            single_instruction = (
                origin + '只画一个头部和双肩，脸占画面大部分，表情自然，纯色背景。'
                '不要全身人物、多角度、其他人物、面板、文字或道具细节。'
                'Output exactly ONE head-and-shoulders portrait, large face, plain background. '
                'No reference sheet, no panels, no full-body figure, no text, no duplicate person.'
            )
            negative += ', reference sheet, contact sheet, text, labels, panels, full body, multiple people, multiple heads'
        if r.generation_type in ('three_view', 'reference_sheet'):
            parts += ['Repeat identity lock across ALL views: ' + identity]
            negative += ', missing required views, inconsistent face, changing costume'
        else:
            parts += ['Preserve these identity traits in this single image: ' + identity]
            negative = ', '.join(term.strip() for term in re.split('[,，]', negative)
                                 if term.strip().lower() != 'missing required views')
            negative += ', inconsistent face, changing costume'
        parts += [
            'The specified identity, costume and composition take priority. '
            'Apply the visual style to rendering, palette and lighting; preserve the listed clothing. '
            'Do not add armor, capes, accessories or environmental panels unless explicitly requested '
            'in the character specification.'
        ]
    elif r.asset_type == 'scene':
        parts += ['Environment-only scene reference card: overview, key lighting, emotional sub-spaces, '
                  'fixed continuity landmarks and important environmental props. '
                  'No characters, people, crowds, silhouettes, faces, hands or character cameos.']
    elif r.asset_type == 'prop':
        single_instruction = (
            '道具静物资产图。画面唯一主体是下方指定的一件物品，独立置于空白展示台上。'
            '完整外轮廓居中，纯色摄影棚背景，画面内容仅由物品本体、展示台与接触阴影构成。'
            '所有表面采用连续均匀的纯色材质，严格保留指定的颜色、材质、结构和功能。'
            '表面仅有材质本身的纹理和高光，装饰保持素净，优先展示工业造型与结构细节。'
            'Product still-life of exactly ONE standalone object. '
            'The composition consists solely of object geometry, plain studio surface and contact shadow. '
            'Every visible surface uses uniform solid-color, unprinted materials, with natural material '
            'texture and highlights. Clean undecorated product design, complete outline, centered composition. '
            'Preserve the specified materials, colors and functional details.'
        )
        if re.search(r'手机|电脑|显示器|屏幕|平板|电视|phone|laptop|monitor|screen|tablet|television',
                     r.name + ' ' + r.description, re.I):
            parts += ['If a display is visible, its entire content consists of a solid light-gray background, '
                      'three monochrome rounded-rectangle message bubbles and short horizontal placeholder lines. '
                      'Use only these simple geometric shapes in a minimal abstract interface. '
                      '屏幕界面全部由浅灰纯色底、三条单色圆角矩形消息条和短横线占位符组成。']
        negative += ', people, person, character, face, portrait, avatar, hands, arms, human skin, person holding object'

    if r.reference_media_ids:
        if r.asset_type == 'character':
            reference_rule = 'Preserve the character identity from image 1; current explicit character details override conflicting reference hair or outfit. Use additional images as supplied visual references.'
        else:
            reference_rule = 'Preserve the described environment or object from the supplied visual references.'
        parts += ['The attached reference images are ordered: ' + ', '.join(
            f'image {i+1}' for i in range(len(r.reference_media_ids))) + '. ' + reference_rule]
    style = adapt_visual_style(r.style, r.asset_type)['effective']
    parts += ['Visual style: ' + style]
    if appearance_directive:
        parts += [appearance_directive]
    if single_instruction:
        parts = [single_instruction] + parts + [single_instruction]
    if r.generation_type == 'reference_sheet':
        negative = ', '.join(part.strip() for part in re.split('[,，]', negative)
                             if part.strip().lower() not in ('text', 'no text', '文字', '无文字', 'labels', 'no labels'))
    if r.asset_type == 'scene':
        negative += ', people, crowds, silhouettes, faces, hands, character cameos'
    negative += ', watermark'
    if r.asset_type in ('character', 'image'):
        negative += ', malformed anatomy'
    return '\n\n'.join(p for p in parts if p), negative.strip(', ')
