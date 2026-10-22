"""QQ message routing; state and external services live in separate modules."""
import asyncio
import random
import re
import time
import os
from contextlib import suppress
from functools import partial
from datetime import datetime

from nonebot import get_bot, get_driver, logger, on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Event, Message, MessageSegment
from nonebot.params import CommandArg

from .config import DATABASE_PATH, MAX_IMAGES, feature_enabled
from .sessions import SessionStore, session_key, trim_history
from .llm import close_clients, get_chat_response, get_image_description, direct_vision
from .comfyui import generate_image
from .media import cleanup_tts_cache, get_image_base64, get_tts_audio
from .tavern import tavern_manager
from .image_history import cleanup_images, save_image
from .delivery import DeliveryResult, deliver_image, try_send, log_send_error
from .stickers import get_sticker, normalize_category
from .runtime import RuntimeStore, operation, record
from .jobs import enqueue_draw, stop_jobs, recover_jobs
from .admin import register_admin
from .memory import EventMemoryStore, prepare as prepare_event_memory
from .incoming_images import read_image

store = SessionStore(DATABASE_PATH)


async def addressed(event: Event) -> bool:
    return event.get_type() == "message" and (
        getattr(event, "message_type", None) == "private" or event.is_tome()
    )


cmd_cards = on_command("cards", aliases={"角色列表"}, rule=addressed, priority=5, block=True)
cmd_switch = on_command("switch", aliases={"切换角色"}, rule=addressed, priority=5, block=True)
cmd_reset = on_command("reset", aliases={"重置记忆"}, rule=addressed, priority=5, block=True)
cmd_help = on_command("help", aliases={"帮助", "菜单"}, rule=addressed, priority=5, block=True)
cmd_proactive = on_command("proactive", aliases={"主动聊天"}, rule=addressed, priority=5, block=True)
cmd_draw = on_command("draw", aliases={"画图"}, rule=addressed, priority=5, block=True)
cmd_memory = on_command("memory", aliases={"记忆"}, rule=addressed, priority=5, block=True)
cmd_remember = on_command("remember", aliases={"记住"}, rule=addressed, priority=5, block=True)
cmd_forget = on_command("forget", aliases={"忘记"}, rule=addressed, priority=5, block=True)
cmd_events = on_command("events", aliases={"共同记忆"}, rule=addressed, priority=5, block=True)
cmd_forget_event = on_command("forgetevent", aliases={"删除事件"}, rule=addressed, priority=5, block=True)
chat_handler = on_message(rule=addressed, priority=10, block=True)


def current_card(session):
    if session.character:
        card = tavern_manager.cards.get(session.character)
        if card:
            return card
    return tavern_manager.get_active_card()


def identify(session, bot, event):
    session.bot_id = str(bot.self_id)
    session.user_id = event.get_user_id()
    session.private = getattr(event, "group_id", None) is None


@cmd_help.handle()
async def handle_help():
    await cmd_help.send(
        "角色聊天机器人\n"
        "/cards：查看角色\n/switch 名字：切换并恢复该角色记忆\n"
        "/memory：查看角色记忆\n/remember 内容：记住长期信息\n/forget 序号：删除记忆\n"
        "/events：查看自动记忆及来源\n/forgetevent ID 或 all：删除自动记忆\n"
        "/reset：重置当前会话记忆\n/proactive on 或 off：开启或关闭主动聊天（仅私聊，默认关闭）\n"
        + ("/draw 英文标签：直接调用 ComfyUI，不经过聊天模型\n" if feature_enabled("DRAW_ENABLED") else "当前未启用生图。\n")
        + ("支持发送语音。" if feature_enabled("TTS_ENABLED") else "当前未启用语音。")
        + "群聊请先 @ 机器人。"
    )


@cmd_cards.handle()
async def handle_cards():
    cards = tavern_manager.list_cards()
    await cmd_cards.send("当前可用角色：\n" + "\n".join(cards) if cards else "characters 目录中没有可用角色卡。")


@cmd_switch.handle()
async def handle_switch(bot: Bot, event: Event, args: Message = CommandArg()):
    target = args.extract_plain_text().strip()
    if not target:
        await cmd_switch.send("请提供角色名称，例如 /switch ling")
        return
    card = tavern_manager.find_card(target)
    if not card:
        await cmd_switch.send("找不到该角色，请使用 /cards 查看列表。")
        return
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        identify(session, bot, event)
        selected = next(k for k, value in tavern_manager.cards.items() if value is card)
        session, restored = await store.switch_character(session, selected, card.first_mes)
        await cmd_switch.send(f"已切换至 {card.name}！\n" +
                              ("已恢复你与这个角色之前的记忆。" if restored else card.first_mes))


@cmd_reset.handle()
async def handle_reset(bot: Bot, event: Event):
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        identify(session, bot, event)
        card = current_card(session)
        greeting = card.first_mes if card else ""
        session.history = [{"role": "assistant", "content": greeting}] if greeting else []
        session.last_image_prompt = None
        session.memories = []
        session.summary = ""
        session.touch()
        await store.save(session)
        await EventMemoryStore(store.path).forget(key, session.character)
        await cmd_reset.send("记忆已重置！\n" + greeting)


@cmd_memory.handle()
async def handle_memory(bot: Bot, event: Event):
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        card = current_card(session)
        notes = "\n".join(f"{i}. {text}" for i, text in enumerate(session.memories, 1)) or "暂无长期记忆，可用 /remember 内容 添加。"
        await cmd_memory.send(f"{card.name if card else session.character} 的记忆：\n{notes}" +
                              (f"\n阶段摘要：{session.summary}" if session.summary else ""))


@cmd_remember.handle()
async def handle_remember(bot: Bot, event: Event, args: Message = CommandArg()):
    text = args.extract_plain_text().strip()
    if not text or len(text) > 300:
        await cmd_remember.send("用法：/remember 我喜欢海边。每条最多 300 字。")
        return
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        identify(session, bot, event)
        if text not in session.memories:
            if len(session.memories) >= 30:
                await cmd_remember.send("当前角色已有 30 条记忆，请先用 /forget 序号 删除不需要的内容。")
                return
            session.memories.append(text)
        await store.save(session)
        await cmd_remember.send("记住啦，这条记忆只属于你与当前角色。")


@cmd_events.handle()
async def handle_events(bot: Bot, event: Event):
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        items = await EventMemoryStore(store.path).list(key, session.character)
        lines = [f"{item['id'][:8]} [{item['status']}] {item['statement']}\n"
                 f"记录于{datetime.fromtimestamp(item['mentioned_at']).isoformat(timespec='minutes')}，"
                 f"事件日期{item['event_date'] or '未明确'}，来源{item['source_run_id'][:8]}"
                 for item in items[:10]]
        await cmd_events.send("当前角色的自动记忆（最近10条）：\n" + ("\n\n".join(lines) if lines else "暂无。") +
                              "\n可用 /forgetevent ID 删除；管理页可查看全部有效记录。")


@cmd_forget_event.handle()
async def handle_forget_event(bot: Bot, event: Event, args: Message = CommandArg()):
    identity = args.extract_plain_text().strip().lower()
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        memories = EventMemoryStore(store.path)
        if identity == "all":
            await memories.forget(key, session.character)
        else:
            items = await memories.list(key, session.character)
            matches = [item for item in items if len(identity) >= 8 and item["id"].startswith(identity)]
            if len(matches) != 1:
                await cmd_forget_event.send("先用 /events 查看 ID，再用 /forgetevent ID 删除；all 清空当前角色的自动记忆。")
                return
            await memories.forget(key, session.character, matches[0]["id"])
        await cmd_forget_event.send("已移除当前角色对应的自动记忆，后续不再用于回忆。")


@cmd_forget.handle()
async def handle_forget(bot: Bot, event: Event, args: Message = CommandArg()):
    value = args.extract_plain_text().strip()
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        if value == "all":
            session.memories = []
        elif value.isdigit() and 1 <= int(value) <= len(session.memories):
            session.memories.pop(int(value) - 1)
        else:
            await cmd_forget.send("先用 /memory 查看序号，再用 /forget 序号 删除；/forget all 删除全部长期记忆。")
            return
        await store.save(session)
        await cmd_forget.send("已删除当前角色对应的长期记忆条目。")


@cmd_proactive.handle()
async def handle_proactive(bot: Bot, event: Event, args: Message = CommandArg()):
    if getattr(event, "group_id", None) is not None:
        await cmd_proactive.send("主动聊天仅支持私聊。")
        return
    value = args.extract_plain_text().strip().lower()
    if value not in {"on", "off", "开启", "关闭"}:
        await cmd_proactive.send("使用 /proactive on 开启，/proactive off 关闭。")
        return
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        identify(session, bot, event)
        session.proactive = value in {"on", "开启"}
        session.touch()
        await store.save(session)
        await cmd_proactive.send("主动聊天已开启，服务器本地时间 00:00–06:30 勿扰。" if session.proactive else "主动聊天已关闭。")


@cmd_draw.handle()
async def handle_draw(bot: Bot, event: Event, args: Message = CommandArg()):
    if not feature_enabled("DRAW_ENABLED"):
        await cmd_draw.send("当前未启用图片生成功能。")
        return
    tags = args.extract_plain_text().strip()
    if not tags:
        await cmd_draw.send("用法：/draw 1girl, solo, black hair, casual clothes, smiling")
        return
    if len(tags) > 2000:
        await cmd_draw.send("生图标签过长，请控制在 2000 字符以内。")
        return
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        identify(session, bot, event)
        session.touch()
        await store.save(session)
        async with operation(RuntimeStore(store.path), key, session.character, "/draw " + tags,
                             getattr(event, "message_id", None)) as scope:
            if scope:
                await enqueue_draw(store, bot, event, tags)



async def send_reply(reply_text, draw_tags, sticker, voice, *, allow_sticker=True, defer_draw=None,
                     character=None, sticker_context=""):
    draw_tags = draw_tags if feature_enabled("DRAW_ENABLED") else None
    voice = voice and feature_enabled("TTS_ENABLED")
    result = DeliveryResult(image_requested=bool(draw_tags))
    if reply_text:
        if voice:
            audio = await get_tts_audio(reply_text, character=character)
            if audio:
                result.text_sent = await try_send(chat_handler, MessageSegment.record(audio), "voice")
            if not result.text_sent:
                result.text_sent = await try_send(chat_handler, MessageSegment.text(reply_text), "voice-text-fallback")
        else:
            parts = [p.strip() for p in reply_text.splitlines() if p.strip()]
            if len(parts) > 3:
                parts = parts[:2] + ["\n".join(parts[2:])]
            for index, part in enumerate(parts):
                if index:
                    await asyncio.sleep(random.uniform(0.5, 1.2))
                sent = await try_send(chat_handler, MessageSegment.text(part), f"text-{index + 1}")
                result.text_sent = result.text_sent or sent
    if sticker and allow_sticker:
        category = normalize_category(sticker)
        if category:
            sticker_args = {"character": character, "context": sticker_context} if character or sticker_context else {}
            animation = await get_sticker(category, **sticker_args)
            if animation:
                result.sticker_sent = await try_send(chat_handler, MessageSegment.image(animation), "anime-sticker")
    if draw_tags:
        if defer_draw:
            result.image_queued = await defer_draw(draw_tags)
        else:
            image = await generate_image(draw_tags)
            await deliver_image(chat_handler, image, result)
    return result


@chat_handler.handle()
async def handle_message(bot: Bot, event: Event):
    text_parts, image_urls, memory_text_parts = [], [], []
    for seg in event.get_message():
        if seg.type == "text":
            text_parts.append(seg.data.get("text", ""))
            memory_text_parts.append(seg.data.get("text", ""))
        elif seg.type == "face":
            text_parts.append(f"[表情{seg.data.get('id', '')}]")
        elif seg.type in {"mface", "image"}:
            image_urls.append(dict(seg.data))
            if seg.data.get("summary"):
                text_parts.append("[QQ提供的表情名称：" + str(seg.data["summary"])[:100] + "]")
    text = "".join(text_parts).strip()
    memory_text = "".join(memory_text_parts).strip()
    if not text and not image_urls:
        return
    key = session_key(bot, event)
    async with store.lock(key):
        session = await store.load(key)
        identify(session, bot, event)
        async with operation(RuntimeStore(store.path), key, session.character, text or "[图片]",
                             getattr(event, "message_id", None)) as scope:
            if scope is None:
                return
            session.touch()
            await store.save(session)
            combined_text = text
            attachments = []
            visual_failed = False
            if image_urls:
                await record("image-input", "started", count=min(len(image_urls), MAX_IMAGES))
                images = await asyncio.gather(*(read_image(bot, data, get_image_base64) for data in image_urls[:MAX_IMAGES]))
                images = [image for image in images if image]
                if direct_vision():
                    for image in images:
                        try:
                            attachments.append(await asyncio.to_thread(save_image, image))
                        except (OSError, ValueError) as exc:
                            logger.warning("Image attachment failed: {}", type(exc).__name__)
                    visual_failed = not attachments
                    combined_text += "\n[用户发送了图片]" if attachments else "\n[图片读取失败：机器人这边未能读取，不能推断用户网络或图片清晰度。]"
                else:
                    description = await get_image_description(images, text) if images else "[视觉识别失败：机器人未能读取图片，不推断用户网络或图片模糊。]"
                    visual_failed = description.startswith("[视觉识别失败")
                    combined_text += f"\n[用户发送的图片描述：{description}]"
                await record("image-input", "failed" if visual_failed else "completed", received=len(images), readable=not visual_failed)
            user_message = {"role": "user", "content": combined_text}
            if attachments:
                user_message["images"] = attachments
            session.history.append(user_message)
            session.history = trim_history(session.history)
            await store.save(session)
            context_args = {"visual_context": session.last_image_prompt} if session.last_image_prompt else {}
            events = [] if visual_failed else await prepare_event_memory(store.path, session, memory_text, scope.run_id)
            if session.memories or session.summary or events:
                context_args["memory_context"] = {"memories": session.memories, "summary": session.summary}
                if events:
                    context_args["memory_context"]["events"] = events
            if visual_failed and not memory_text:
                # An image-only failure has no reliable conversational content to improvise on.
                reply, draw, sticker, voice = "这张表情包我这边没识别成功，暂时还读不到内容。", None, None, False
            else:
                reply, draw, sticker, voice = await get_chat_response(session.history, card=current_card(session), **context_args)
            explicit_voice = bool(re.search(r"发.*语音|说句话|听听你的声音|录音", text))
            cooldown = max(0, float(os.getenv("STICKER_COOLDOWN", "30")))
            allow_sticker = time.time() - session.last_sticker_time >= cooldown or bool(re.search(r"表情包|动图", text))
            try:
                delivery = await send_reply(reply, draw, sticker, voice or explicit_voice, allow_sticker=allow_sticker, defer_draw=partial(enqueue_draw, store, bot, event),
                                            character=session.character, sticker_context=text + " " + reply)
            except Exception as exc:
                log_send_error("reply-pipeline", exc)
                await record("reply-pipeline", "failed", error=type(exc).__name__)
                return
            if isinstance(delivery, DeliveryResult):
                if delivery.sticker_sent:
                    session.last_sticker_time = time.time()
                if delivery.image_generated and draw:
                    session.last_image_prompt = draw[:3000]
                remembered = reply if delivery.text_sent else ""
                status = delivery.image_status()
                if status:
                    remembered += "\n" + status
                if remembered.strip():
                    session.history.append({"role": "assistant", "content": remembered.strip()})
            elif reply:  # Compatibility with alternate reply senders.
                session.history.append({"role": "assistant", "content": reply})
            await store.save(session)


async def run_proactive_once(now=None):
    now = datetime.now() if now is None else now
    if now.hour < 6 or (now.hour == 6 and now.minute < 30):
        return
    for key in await store.due(time.time()):
        async with store.lock(key):
            session = await store.load(key)
            if not session.proactive or not session.private or session.next_proactive > time.time():
                continue
            # Schedule before the network call so failures cannot create a rapid retry loop.
            session.next_proactive = time.time() + random.uniform(3600, 7200)
            await store.save(session)
            try:
                bot = get_bot(session.bot_id)
                history = session.history[-5:] + [{"role": "user", "content":
                    "[定时聊天提示：用户暂时没有发消息，请按当前人设主动说一句简短问候，仅文字。]"}]
                reply, _, _, _ = await get_chat_response(history, card=current_card(session), silent_failure=True,
                                                       memory_context={"memories": session.memories, "summary": session.summary})
                if reply:
                    await bot.send_private_msg(user_id=int(session.user_id), message=reply)
                    session.history.append({"role": "assistant", "content": reply})
                    await store.save(session)
            except Exception as exc:
                logger.warning("Proactive message failed: {}", type(exc).__name__)


async def proactive_chat():
    while True:
        await asyncio.sleep(300)
        try:
            await run_proactive_once()
            await asyncio.to_thread(cleanup_tts_cache)
            await asyncio.to_thread(cleanup_images)
        except Exception as exc:
            logger.warning("Proactive cycle failed: {}", type(exc).__name__)


_background_task = None
driver = get_driver()
register_admin(driver.server_app, lambda: store)


@driver.on_startup
async def start_background_task():
    global _background_task
    if _background_task is None or _background_task.done():
        await store.migrate_legacy()
        await recover_jobs(RuntimeStore(store.path))
        _background_task = asyncio.create_task(proactive_chat(), name="proactive-chat")


@driver.on_shutdown
async def stop_background_task():
    global _background_task
    if _background_task:
        _background_task.cancel()
        with suppress(asyncio.CancelledError):
            await _background_task
        _background_task = None
    await stop_jobs()
    await close_clients()
