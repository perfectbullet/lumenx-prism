"""Deterministic asset-specific style adaptation; original request text is preserved."""
import re

CHARACTER_TERMS = re.compile(
    r'\b(character|characters|face|faces|facial|skin|anatomy|proportions?|fashion|costume|streetwear|pose|heroic silhouette|storytelling|adventure)\b'
    r'|人物|角色|五官|皮肤|脸部|面部|表情|服装|人体|冒险|叙事', re.I)
ENVIRONMENT_TERMS = re.compile(
    r'\b(background|environment|environments|city|urban|rooftop|ruins|forest|landscape|skyline|street|mist)\b'
    r'|环境|场景|城市|都市|废墟|森林|山水|背景', re.I)


def adapt_visual_style(style, asset_type):
    original = style or 'Stylized 3D animated feature design, readable shapes, tactile materials, soft lighting.'
    terms = [term.strip() for term in re.split(r'[,，;；\n]+', original) if term.strip()]
    removed, kept = [], []
    for term in terms:
        incompatible = asset_type in ('scene', 'prop') and CHARACTER_TERMS.search(term)
        if asset_type == 'prop':
            incompatible = incompatible or ENVIRONMENT_TERMS.search(term)
        (removed if incompatible else kept).append(term)
    effective = ', '.join(kept)
    if not effective:
        effective = {'prop': 'Stylized product rendering, clean material shading, studio lighting.',
                     'scene': 'Stylized environment rendering, cohesive colors and lighting.'}.get(asset_type, original)
    # Keep character/custom-image formatting verbatim; adaptation targets non-character assets.
    if asset_type not in ('scene', 'prop'):
        effective = original
    return {'asset_type': asset_type, 'original': original, 'effective': effective, 'removed_terms': removed}
