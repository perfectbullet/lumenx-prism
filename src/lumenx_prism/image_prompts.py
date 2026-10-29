"""Production rules adapted from MiniMax-H3 3d-animation-short-generator STEP 3/4.
Source revision: 199b7d0e1fd95e7b3eafa8c15eca692534142427. No Hub dependency.
"""
import json
import re
from .image_schemas import ImageRequest

PROMPT_VERSION = 'character-production-v3'
SKILL_REVISION = '199b7d0e1fd95e7b3eafa8c15eca692534142427'


def compile_image_prompt(request: ImageRequest):
    r = request
    negative = r.negative_prompt
    parts = [f'Asset: {r.name}. {r.description}', r.prompt]
    single_instruction = None
    if r.character_card:
        card = r.character_card
        identity = '; '.join(card.immutable_traits)
        specification = card.model_dump()
        if r.generation_type != 'reference_sheet':
            specification.pop('expressions', None)
        parts += ['Identity lock: ' + identity,
                  'Character appearance: ' + json.dumps(specification, ensure_ascii=False)]
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
        parts += ['Isolated prop production reference: readable silhouette, consistent materials and colors, '
                  'neutral background, no person or hands.']
    if r.reference_media_ids:
        if r.asset_type == 'character':
            reference_rule = 'Preserve the character identity and costume from image 1; use additional images as supplied visual references.'
        else:
            reference_rule = 'Preserve the described environment or object from the supplied visual references.'
        parts += ['The attached reference images are ordered: ' + ', '.join(
            f'image {i+1}' for i in range(len(r.reference_media_ids))) + '. ' + reference_rule]
    style = r.style or 'Stylized 3D animated feature design, readable shapes, tactile materials, soft lighting.'
    parts += ['Visual style: ' + style]
    if single_instruction:
        parts = [single_instruction] + parts + [single_instruction]
    if r.generation_type == 'reference_sheet':
        negative = ', '.join(part.strip() for part in re.split('[,，]', negative)
                             if part.strip().lower() not in ('text', 'no text', '文字', '无文字', 'labels', 'no labels'))
    if r.asset_type == 'scene':
        negative += ', people, crowds, silhouettes, faces, hands, character cameos'
    negative += ', watermark, malformed anatomy'
    return '\n\n'.join(p for p in parts if p), negative.strip(', ')
