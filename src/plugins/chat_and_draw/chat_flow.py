"""Bot chat flow callbacks."""
import asyncio
import random
import re
import time
import os
from functools import partial
from nonebot import logger
from nonebot.adapters.onebot.v11 import Bot, Event, MessageSegment
from nonebot.typing import T_State
from .llm import ServiceNotice
from .code_analysis import needs_code_analysis
from .qq_text import qq_plain_text, qq_chunks
from .config import MAX_IMAGES, feature_enabled
from .sessions import session_key, trim_history
from .image_history import save_image
from .delivery import DeliveryResult, deliver_image, log_send_error
from .stickers import normalize_category
from .runtime import operation, record
from .incoming_images import read_image
from .group_chat import GroupChat

class ChatFlow:

    async def send_reply(self, reply_text, draw_tags, sticker, voice, *, allow_sticker=True, defer_draw=None, character=None, sticker_context=''):
        draw_tags = draw_tags if feature_enabled('DRAW_ENABLED') else None
        voice = voice and feature_enabled('TTS_ENABLED')
        reply_text = qq_plain_text(reply_text)
        result = DeliveryResult(image_requested=bool(draw_tags))
        if reply_text:
            if voice:
                audio = await self.get_tts_audio(reply_text, character=character)
                if audio:
                    result.text_sent = await self.try_send(self.chat_handler, MessageSegment.record(audio), 'voice')
                if not result.text_sent:
                    result.text_sent = await self.try_send(self.chat_handler, MessageSegment.text(reply_text), 'voice-text-fallback')
            else:
                parts = qq_chunks(reply_text)
                for (index, part) in enumerate(parts):
                    if index:
                        await asyncio.sleep(random.uniform(0.5, 1.2))
                    sent = await self.try_send(self.chat_handler, MessageSegment.text(part), f'text-{index + 1}')
                    result.text_sent = result.text_sent or sent
        if sticker and allow_sticker:
            category = normalize_category(sticker)
            if category:
                sticker_args = {'character': character, 'context': sticker_context} if character or sticker_context else {}
                animation = await self.get_sticker(category, **sticker_args)
                if animation:
                    result.sticker_sent = await self.try_send(self.chat_handler, MessageSegment.image(animation), 'anime-sticker')
        if draw_tags:
            if defer_draw:
                result.image_queued = await defer_draw(draw_tags)
            else:
                image = await self.generate_image(draw_tags)
                await deliver_image(self.chat_handler, image, result)
        return result

    async def handle_message(self, bot: Bot, event: Event, state: T_State=None):
        group_context = (state or {}).get('group_context')
        if group_context and group_context['mode'] == 'bot-interaction':
            await self.handle_bot_interaction(bot, group_context, event.get_message().extract_plain_text(), getattr(event, 'message_id', None))
            return
        ambient = bool(group_context and group_context['mode'] in {'ambient', 'followup'})
        (text_parts, image_urls, memory_text_parts) = ([], [], [])
        for seg in event.get_message():
            if seg.type == 'text':
                text_parts.append(seg.data.get('text', ''))
                memory_text_parts.append(seg.data.get('text', ''))
            elif seg.type == 'face':
                text_parts.append(f"[表情{seg.data.get('id', '')}]")
            elif seg.type in {'mface', 'image'}:
                image_urls.append(dict(seg.data))
                if seg.data.get('summary'):
                    text_parts.append('[QQ提供的表情名称：' + str(seg.data['summary'])[:100] + ']')
        text = ''.join(text_parts).strip()
        if ambient:
            image_urls = []
        memory_text = ''.join(memory_text_parts).strip()
        if not text and (not image_urls):
            if group_context and group_context['mode'] == 'mention':
                text = '[群成员刚刚单独 @ 你，请简短回应。]'
            else:
                return
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            self.identify(session, bot, event)
            async with operation(self.runtime, key, session.character, text or '[图片]', getattr(event, 'message_id', None)) as scope:
                if scope is None:
                    return
                session.touch()
                await self.store.save(session)
                combined_text = text
                attachments = []
                visual_failed = False
                if image_urls:
                    await record('image-input', 'started', count=min(len(image_urls), MAX_IMAGES))
                    images = await asyncio.gather(*(read_image(bot, data, self.get_image_base64) for data in image_urls[:MAX_IMAGES]))
                    images = [image for image in images if image]
                    if self.direct_vision():
                        for image in images:
                            try:
                                attachments.append(await asyncio.to_thread(save_image, image))
                            except (OSError, ValueError) as exc:
                                logger.warning('Image attachment failed: {}', type(exc).__name__)
                        visual_failed = not attachments
                        combined_text += '\n[用户发送了图片]' if attachments else '\n[图片读取失败：机器人这边未能读取，不能推断用户网络或图片清晰度。]'
                    else:
                        description = await self.get_image_description(images, text) if images else '[视觉识别失败：机器人未能读取图片，不推断用户网络或图片模糊。]'
                        visual_failed = description.startswith('[视觉识别失败')
                        combined_text += f'\n[用户发送的图片描述：{description}]'
                    await record('image-input', 'failed' if visual_failed else 'completed', received=len(images), readable=not visual_failed)
                code_analysis = needs_code_analysis(session.history + [{'role': 'user', 'content': combined_text}])
                user_message = {'role': 'user', 'content': combined_text}
                if attachments:
                    user_message['images'] = attachments
                session.history.append(user_message)
                session.history = trim_history(session.history)
                if not ambient:
                    await self.store.save(session)
                context_args = {'visual_context': session.last_image_prompt} if session.last_image_prompt else {}
                if group_context:
                    context_args['group_context'] = group_context
                    await record('group-participation', 'completed', mode=group_context['mode'], context_messages=len(group_context['messages']))
                if ambient:
                    context_args['silent_failure'] = True
                events = [] if visual_failed or ambient or code_analysis else await self.prepare_event_memory(self.store.path, session, memory_text, scope.run_id)
                if not code_analysis and (session.memories or session.summary or events):
                    context_args['memory_context'] = {'memories': session.memories, 'summary': session.summary}
                    if events:
                        context_args['memory_context']['events'] = events
                if visual_failed and (not memory_text):
                    (reply, draw, sticker, voice) = ('这张表情包我这边没识别成功，暂时还读不到内容。', None, None, False)
                else:
                    (reply, draw, sticker, voice) = await self.get_chat_response(session.history, card=self.current_card(session), **context_args)
                if ambient:
                    reply = reply[:160] if code_analysis else ' '.join(reply.split())[:160]
                    if reply == '[不参与]':
                        reply = ''
                    (draw, sticker, voice) = (None, None, False)
                    if not reply:
                        return
                service_notice = isinstance(reply, ServiceNotice)
                if code_analysis:
                    draw, sticker, voice = None, None, False
                explicit_voice = bool(re.search('发.*语音|说句话|听听你的声音|录音', text))
                cooldown = max(0, float(os.getenv('STICKER_COOLDOWN', '30')))
                allow_sticker = time.time() - session.last_sticker_time >= cooldown or bool(re.search('表情包|动图', text))
                try:
                    delivery = await self.send_reply(reply, draw, sticker, (voice or explicit_voice) and (not ambient) and (not service_notice) and (not code_analysis), allow_sticker=allow_sticker, defer_draw=partial(self.enqueue_draw, self.store, bot, event), character=session.character, sticker_context=text + ' ' + reply)
                except Exception as exc:
                    log_send_error('reply-pipeline', exc)
                    await record('reply-pipeline', 'failed', error=type(exc).__name__)
                    return
                if isinstance(delivery, DeliveryResult):
                    if delivery.sticker_sent:
                        session.last_sticker_time = time.time()
                    if delivery.image_generated and draw:
                        session.last_image_prompt = draw[:3000]
                    remembered = reply if delivery.text_sent and not service_notice else ''
                    status = delivery.image_status()
                    if status:
                        remembered += '\n' + status
                    if remembered.strip():
                        session.history.append({'role': 'assistant', 'content': remembered.strip()})
                elif reply and not service_notice:
                    session.history.append({'role': 'assistant', 'content': reply})
                await self.store.save(session)
                if group_context and reply and isinstance(delivery, DeliveryResult) and delivery.text_sent:
                    await asyncio.to_thread(self.group_chat.add, GroupChat.room(bot, event), None, 'bot:' + str(bot.self_id) + ':to:' + str(event.get_user_id()), reply, time.time())
