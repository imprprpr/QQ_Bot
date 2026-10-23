"""Bot commands callbacks."""
from datetime import datetime
from nonebot.adapters.onebot.v11 import Bot, Event, Message
from nonebot.params import CommandArg
from .config import feature_enabled
from .sessions import session_key
from .tavern import tavern_manager
from .runtime import operation
from .memory import EventMemoryStore

class CommandHandlers:

    async def handle_help(self):
        await self.cmd_help.send('角色聊天机器人\n/cards：查看角色\n/switch 名字：切换并恢复该角色记忆\n/memory：查看角色记忆\n/remember 内容：记住长期信息\n/forget 序号：删除记忆\n/events：查看自动记忆及来源\n/forgetevent ID 或 all：删除自动记忆\n/reset：重置当前会话记忆\n/proactive on 或 off：开启或关闭主动聊天（仅私聊，默认开启）\n' + ('/draw 英文画面描述：直接调用图片服务，不经过聊天模型\n' if feature_enabled('DRAW_ENABLED') else '当前未启用生图。\n') + ('支持发送语音。' if feature_enabled('TTS_ENABLED') else '当前未启用语音。') + '群聊可 @ 我或叫我的昵称；平时会限频参与。群内命令请先 @ 我。')

    async def handle_cards(self):
        cards = tavern_manager.list_cards()
        await self.cmd_cards.send('当前可用角色：\n' + '\n'.join(cards) if cards else 'characters 目录中没有可用角色卡。')

    async def handle_switch(self, bot: Bot, event: Event, args: Message=CommandArg()):
        target = args.extract_plain_text().strip()
        if not target:
            await self.cmd_switch.send('请提供角色名称，例如 /switch ling')
            return
        card = tavern_manager.find_card(target)
        if not card:
            await self.cmd_switch.send('找不到该角色，请使用 /cards 查看列表。')
            return
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            self.identify(session, bot, event)
            selected = next((k for (k, value) in tavern_manager.cards.items() if value is card))
            (session, restored) = await self.store.switch_character(session, selected, card.first_mes)
            await self.cmd_switch.send(f'已切换至 {card.name}！\n' + ('已恢复你与这个角色之前的记忆。' if restored else card.first_mes))

    async def handle_reset(self, bot: Bot, event: Event):
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            self.identify(session, bot, event)
            card = self.current_card(session)
            greeting = card.first_mes if card else ''
            session.history = [{'role': 'assistant', 'content': greeting}] if greeting else []
            session.last_image_prompt = None
            session.memories = []
            session.summary = ''
            session.touch()
            await self.store.save(session)
            await EventMemoryStore(self.store.path).forget(key, session.character)
            await self.cmd_reset.send('记忆已重置！\n' + greeting)

    async def handle_memory(self, bot: Bot, event: Event):
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            card = self.current_card(session)
            notes = '\n'.join((f'{i}. {text}' for (i, text) in enumerate(session.memories, 1))) or '暂无长期记忆，可用 /remember 内容 添加。'
            await self.cmd_memory.send(f'{(card.name if card else session.character)} 的记忆：\n{notes}' + (f'\n阶段摘要：{session.summary}' if session.summary else ''))

    async def handle_remember(self, bot: Bot, event: Event, args: Message=CommandArg()):
        text = args.extract_plain_text().strip()
        if not text or len(text) > 300:
            await self.cmd_remember.send('用法：/remember 我喜欢海边。每条最多 300 字。')
            return
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            self.identify(session, bot, event)
            if text not in session.memories:
                if len(session.memories) >= 30:
                    await self.cmd_remember.send('当前角色已有 30 条记忆，请先用 /forget 序号 删除不需要的内容。')
                    return
                session.memories.append(text)
            await self.store.save(session)
            await self.cmd_remember.send('记住啦，这条记忆只属于你与当前角色。')

    async def handle_events(self, bot: Bot, event: Event):
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            items = await EventMemoryStore(self.store.path).list(key, session.character)
            lines = [f"{item['id'][:8]} [{item['status']}] {item['statement']}\n记录于{datetime.fromtimestamp(item['mentioned_at']).isoformat(timespec='minutes')}，事件日期{item['event_date'] or '未明确'}，来源{item['source_run_id'][:8]}" for item in items[:10]]
            await self.cmd_events.send('当前角色的自动记忆（最近10条）：\n' + ('\n\n'.join(lines) if lines else '暂无。') + '\n可用 /forgetevent ID 删除；管理页可查看全部有效记录。')

    async def handle_forget_event(self, bot: Bot, event: Event, args: Message=CommandArg()):
        identity = args.extract_plain_text().strip().lower()
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            memories = EventMemoryStore(self.store.path)
            if identity == 'all':
                await memories.forget(key, session.character)
            else:
                items = await memories.list(key, session.character)
                matches = [item for item in items if len(identity) >= 8 and item['id'].startswith(identity)]
                if len(matches) != 1:
                    await self.cmd_forget_event.send('先用 /events 查看 ID，再用 /forgetevent ID 删除；all 清空当前角色的自动记忆。')
                    return
                await memories.forget(key, session.character, matches[0]['id'])
            await self.cmd_forget_event.send('已移除当前角色对应的自动记忆，后续不再用于回忆。')

    async def handle_forget(self, bot: Bot, event: Event, args: Message=CommandArg()):
        value = args.extract_plain_text().strip()
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            if value == 'all':
                session.memories = []
            elif value.isdigit() and 1 <= int(value) <= len(session.memories):
                session.memories.pop(int(value) - 1)
            else:
                await self.cmd_forget.send('先用 /memory 查看序号，再用 /forget 序号 删除；/forget all 删除全部长期记忆。')
                return
            await self.store.save(session)
            await self.cmd_forget.send('已删除当前角色对应的长期记忆条目。')

    async def handle_proactive(self, bot: Bot, event: Event, args: Message=CommandArg()):
        if getattr(event, 'group_id', None) is not None:
            await self.cmd_proactive.send('主动聊天仅支持私聊。')
            return
        value = args.extract_plain_text().strip().lower()
        if value not in {'on', 'off', '开启', '关闭'}:
            await self.cmd_proactive.send('使用 /proactive on 开启，/proactive off 关闭。')
            return
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            self.identify(session, bot, event)
            session.proactive = value in {'on', '开启'}
            session.touch()
            await self.store.save(session)
            await self.cmd_proactive.send('主动聊天已开启：按配置时区在日间随机问候，遵守闲置时间和每日上限；未回复时暂停。/proactive off 可关闭。' if session.proactive else '主动聊天已关闭。')

    async def handle_draw(self, bot: Bot, event: Event, args: Message=CommandArg()):
        if not feature_enabled('DRAW_ENABLED'):
            await self.cmd_draw.send('当前未启用图片生成功能。')
            return
        tags = args.extract_plain_text().strip()
        if not tags:
            await self.cmd_draw.send('用法：/draw 1girl, solo, black hair, casual clothes, smiling')
            return
        if len(tags) > 2000:
            await self.cmd_draw.send('生图标签过长，请控制在 2000 字符以内。')
            return
        key = session_key(bot, event)
        async with self.store.lock(key):
            session = await self.store.load(key)
            self.identify(session, bot, event)
            session.touch()
            await self.store.save(session)
            async with operation(self.runtime, key, session.character, '/draw ' + tags, getattr(event, 'message_id', None)) as scope:
                if scope:
                    await self.enqueue_draw(self.store, bot, event, tags)

    def current_card(self, session):
        if session.character:
            card = tavern_manager.cards.get(session.character)
            if card:
                return card
        return tavern_manager.get_active_card()

    def identify(self, session, bot, event):
        session.bot_id = str(bot.self_id)
        session.user_id = event.get_user_id()
        session.private = getattr(event, 'group_id', None) is None
