"""Bot lifecycle callbacks."""
import asyncio
import random
import time
from contextlib import suppress
from datetime import datetime
from nonebot import logger
from .media import cleanup_tts_cache
from .image_history import cleanup_images
from .memory import EventMemoryStore

class BackgroundLifecycle:

    async def run_proactive_once(self, now=None):
        from .proactive import local_now, in_window, eligible, next_time
        from .sessions import trim_history
        now = local_now() if now is None else now
        if not in_window(now):
            return
        epoch = now.timestamp()
        for key in await self.store.due(epoch):
            async with self.store.lock(key):
                session = await self.store.load(key)
                if not session.proactive or not session.private or session.next_proactive > epoch:
                    continue
                session.next_proactive = next_time(now, max(session.last_interaction, epoch))
                if not eligible(session, now):
                    await self.store.save(session)
                    continue
                day = now.date().isoformat()
                session.proactive_attempts = (session.proactive_attempts if session.proactive_day == day else 0) + 1
                session.proactive_day = day
                await self.store.save(session)  # Reserve budget before any API call, survives restart.
                last_interaction, character = session.last_interaction, session.character
            try:
                bot = self.get_bot(session.bot_id)
                history = [{'role': m['role'], 'content': m['content']} for m in session.history[-6:]]
                history.append({'role': 'user', 'content':
                    f'[主动问候提示：当地时间{now:%H:%M}，用户已一段时间没有聊天。按人设自然开启一个轻松话题或简短问候，最多80字。'
                    '仅文字，不输出任何工具标签；不声称知道用户正在做什么，不催促或责怪对方回复，也不编造网络故障。]'})
                (reply, _, _, _) = await self.get_chat_response(history, card=self.current_card(session),
                    silent_failure=True, memory_context={'memories': session.memories, 'summary': session.summary})
                reply = ' '.join(reply.split())[:160]
                if not reply:
                    continue
                async with self.store.lock(key):
                    current = await self.store.load(key)
                    # A human message or role/opt-in change during generation cancels this greeting.
                    if (not current.proactive or current.proactive_unanswered
                            or current.last_interaction != last_interaction or current.character != character):
                        continue
                    current.proactive_unanswered = True
                    await self.store.save(current)  # Ambiguous send failures must not duplicate a greeting.
                    await asyncio.wait_for(bot.send_private_msg(user_id=int(current.user_id), message=reply), 20)
                    current.history = trim_history(current.history + [{'role': 'assistant', 'content': reply}])
                    await self.store.save(current)
            except Exception as exc:
                logger.warning('Proactive message failed: {}', type(exc).__name__)

    async def proactive_chat(self):
        while True:
            await asyncio.sleep(300)
            try:
                await self.run_proactive_once()
                await asyncio.to_thread(cleanup_tts_cache)
                await asyncio.to_thread(cleanup_images)
                await asyncio.to_thread(self.group_chat.cleanup)
            except Exception as exc:
                logger.warning('Proactive cycle failed: {}', type(exc).__name__)

    async def start_background_task(self):
        if self._background_task is None or self._background_task.done():
            await asyncio.to_thread(self.store.initialize)
            await asyncio.to_thread(self.runtime.initialize)
            await asyncio.to_thread(EventMemoryStore(self.store.path).initialize)
            await self.store.migrate_legacy()
            await asyncio.to_thread(self.group_chat.cleanup)
            await self.recover_jobs(self.runtime)
            self._background_task = asyncio.create_task(self.proactive_chat(), name='proactive-chat')

    async def stop_background_task(self):
        if self._background_task:
            self._background_task.cancel()
            with suppress(asyncio.CancelledError):
                await self._background_task
            self._background_task = None
        await self.stop_jobs()
        await self.close_clients()
