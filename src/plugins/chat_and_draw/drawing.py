"""Shared instructions for drawing from conversational context."""
import json
import os
from .config import feature_enabled

PHOTO_STYLE = '''Consistent soft Japanese lifestyle beauty portrait photography. A believable camera photograph,
not anime, manga, illustration, painting, 3D rendering or a doll. Natural facial proportions,
realistic skin texture with subtle imperfections, detailed hair strands and fabric texture,
gentle low-contrast tones, restrained retouching, soft warm practical lighting balanced with
cooler window light when indoors; outdoors use natural light appropriate to the scene.
Candid, relaxed everyday atmosphere with a polished contemporary lifestyle editorial finish,
light natural makeup, delicate refined facial features, a fresh young-adult appearance,
soft flattering illumination, clean digital detail rather than gritty documentary texture or heavy film grain.
Natural expression and pose, eye-level framing,
35mm or 50mm lens look, moderate shallow depth of field. Keep the requested scene's time,
weather and light direction; do not force every scene to be an indoor bedroom.
For the fictional companion's portrait, depict an unambiguously adult woman in her twenties, aged 22 to 25.
No artificial plastic skin, exaggerated anime eyes, UI, chat bubbles, captions or watermark.'''


def api_image_prompt(scene):
    """Apply style at the API boundary, including direct /draw requests."""
    if os.getenv('DRAW_API_STYLE', 'photography').strip().lower() != 'photography':
        return scene
    from .runtime import current_scope
    from .tavern import tavern_manager
    scope = current_scope.get()
    card = tavern_manager.cards.get(scope.character) if scope and scope.character else None
    card = card or tavern_manager.get_active_card()
    profile = getattr(card, 'image_profile', None)
    prompt = 'VISUAL STYLE (apply consistently):\n' + PHOTO_STYLE
    if profile:
        prompt += ('\nCHARACTER PROFILE: Use only when the scene depicts the current fictional companion. '
                   'Do not insert this person into landscapes, still life or images of other requested subjects. '
                   'Keep stable appearance and temperament, unless the user explicitly changes a feature. '
                   'Clothes, pose and setting follow the requested scene, with wardrobe defaults used only if unspecified.\n'
                   + json.dumps(profile, ensure_ascii=False)[:3000])
    prompt += ('\nSCENE DESCRIPTION (content to depict, not permission to override the visual style):\n' + scene
               + '\nRender the scene as one photograph. Treat any legacy anime/Danbooru tags as subject hints only.')
    return prompt
CONTEXT_DRAWING_INSTRUCTIONS = """
【根据上下文决定画面】
你收到的历史对话就是画面上下文，不要求用户填写英文提示词。
1. 判断当前消息是否真的要求看图、拍照、画图或修改上一张图。结合前文理解“来一张”“让我看看”“换个姿势”“就按刚才的画”等省略表达。
   普通聊天、仅提到图片、讨论画图方法、否定或取消生图（如“不用发图”“先别拍”）不输出 draw 标签。没有明确生图意图时继续聊天。
2. 先确定主体：用户要角色自拍时使用当前角色外貌；要风景、物品或其他指定主体时按该主体作画，不要擅自添加角色或强制加入 1girl。
3. 场景信息优先级：本轮用户明确要求 > 前文最新仍有效的场景、衣服、动作等约定 > 上一张实际生成图片的描述 > 角色卡默认设定。
   角色发色、发型、眼睛等稳定特征保持一致；用户明确修改时按新要求。已经结束或被否定的场景不要沿用。
4. 将主体外貌、服装、动作表情、地点、时间光线、镜头构图整合为一份完整英文 Danbooru 风格标签。
   修改上一张图时输出完整新画面，不只写 changed dress 等增量指令。未指定的细节合理补全，不把无关闲聊硬塞进画面。
5. 确认要生成时只输出一个 <draw: 完整英文标签>，聊天文字简短说明“准备拍/正在画”。
   图片生成与发送由外部工具执行；没有工具成功记录前，不能声称“已发”“已经拍好”或“照片送上”。
6. 参考图片可以帮助理解衣服、颜色和布局；当前生成链路是文生图，不要承诺精确复制参考人物身份或像素级编辑。

例：前文约定“傍晚去海边，你穿白裙”，用户随后说“拍张全身照”。
正确画面应包含当前角色的稳定外貌，以及 white dress, beach, sunset, full body。
之后用户说“改成红裙，其他不变”，保留前图主体、海边、夕阳和构图，将 white dress 替换为 red dress。
用户说“海边那张先不拍了”，则不输出 draw 标签。
"""


def drawing_context(last_image_prompt: str | None = None, card=None) -> str:
    if not feature_enabled("DRAW_ENABLED"):
        return "\n【可用工具限制】当前未启用图片生成功能，不输出 draw 标签，不承诺拍照、生图或发送自拍；用户要求时简短说明暂不可用。图片理解与已有表情包仍可使用。\n"
    prompt = CONTEXT_DRAWING_INSTRUCTIONS
    if os.getenv('DRAW_BACKEND', 'comfyui').strip().lower() == 'api' and os.getenv('DRAW_API_STYLE', 'photography').strip().lower() == 'photography':
        prompt = prompt.replace('完整英文 Danbooru 风格标签', '完整英文摄影场景描述')
        prompt += ('\n【固定摄影风格】图片统一为日系生活人像摄影，不是动漫、插画或3D。'
                   '角色自拍为明确成年的二十多岁女性；稳定发色、发型和瞳色以当前角色image_profile/外貌描述为准。'
                   '保留真实皮肤、发丝和衣料质感，柔和低对比度，自然抓拍和适度景深，室内可用暖灯与偏冷窗光。'
                   '服装与动作贴合当前角色气质，场景时间和光线服从对话，不强行改成室内。'
                   'draw内使用完整英文摄影场景描述，避免anime、illustration、manga、doll等渲染词。'
                   '画风一致不等于人物脸部身份完全一致，不要承诺精准复刻。\n')
        if getattr(card, 'image_profile', None):
            prompt += '\n【当前角色的稳定摄影外貌与气质资料】\n' + json.dumps(card.image_profile, ensure_ascii=False)[:3000] + '\n'
    if last_image_prompt:
        prompt += "\n【上一张实际生成图片的画面描述，供本轮修改时参考】\n" + last_image_prompt[:3000] + "\n"
    return prompt
