import pytest
from lumenx_prism.image_prompts import compile_image_prompt
from lumenx_prism.image_schemas import ImageRequest
from lumenx_prism.style_rules import adapt_visual_style

STYLE = ('stylized feature-quality 3D animation, expressive rounded character design, appealing shape language, '
         'warm cinematic lighting, rich color design, soft subsurface skin shading, detailed but friendly environment, '
         'polished animation render, clear emotional storytelling, family adventure tone')


def test_prop_style_excludes_character_and_environment_but_preserves_rendering():
    snapshot = adapt_visual_style(STYLE, 'prop')
    assert snapshot['original'] == STYLE
    assert '3D animation' in snapshot['effective'] and 'warm cinematic lighting' in snapshot['effective']
    for term in ('character design', 'skin shading', 'environment', 'storytelling', 'adventure'):
        assert term not in snapshot['effective']
    assert snapshot['removed_terms']


def test_scene_style_keeps_environment_without_person_design():
    snapshot = adapt_visual_style(STYLE, 'scene')
    assert 'friendly environment' in snapshot['effective']
    assert 'character design' not in snapshot['effective'] and 'skin shading' not in snapshot['effective']


@pytest.mark.parametrize('kind', ['character', 'image'])
def test_character_and_free_image_styles_preserve_original_text(kind):
    assert adapt_visual_style(STYLE, kind)['effective'] == STYLE


@pytest.mark.parametrize('kind', ['scene', 'prop'])
def test_empty_or_character_only_styles_have_asset_specific_fallback(kind):
    snapshot = adapt_visual_style('人物设计，皮肤，服装', kind)
    assert snapshot['effective'] and snapshot['removed_terms'] == ['人物设计', '皮肤', '服装']
    assert adapt_visual_style('', kind)['effective']


def test_phone_prompt_has_still_life_and_screen_content_constraints():
    request = ImageRequest(asset_type='prop', name='手机', description='具有可显示消息的屏幕。', style=STYLE)
    prompt, negative = compile_image_prompt(request)
    assert 'Product still-life of exactly ONE standalone object' in prompt
    assert 'three monochrome rounded-rectangle message bubbles' in prompt
    assert 'uniform solid-color, unprinted materials' in prompt
    assert 'portrait' not in prompt and 'avatar' not in prompt
    assert 'expressive rounded character design' not in prompt
    assert 'family adventure tone' not in prompt
    assert 'human skin' in negative and 'malformed anatomy' not in negative
    assert request.style == STYLE  # Never rewrite caller input or extraction facts.


def test_non_screen_prop_does_not_invent_display_interface():
    prompt, _ = compile_image_prompt(ImageRequest(asset_type='prop', name='咖啡杯', description='白色陶瓷杯', style=STYLE))
    assert 'message bubbles' not in prompt
    assert '白色陶瓷杯' in prompt and 'Product still-life' in prompt
