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
        now = datetime.now() if now is None else now
        if now.hour < 6 or (now.hour == 6 and now.minute < 30):
            return
        for key in await self.store.due(time.time()):
            async with self.store.lock(key):
                session = await self.store.load(key)
                if not session.proactive or not session.private or session.next_proactive > time.time():
                    continue
                session.next_proactive = time.time() + random.uniform(3600, 7200)
                await self.store.save(session)
                try:
                    bot = self.get_bot(session.bot_id)
                    history = session.history[-5:] + [{'role': 'user', 'content': '[定时聊天提示：用户暂时没有发消息，请按当前人设主动说一句简短问候，仅文字。]'}]
                    (reply, _, _, _) = await self.get_chat_response(history, card=self.current_card(session), silent_failure=True, memory_context={'memories': session.memories, 'summary': session.summary})
                    if reply:
                        await bot.send_private_msg(user_id=int(session.user_id), message=reply)
                        session.history.append({'role': 'assistant', 'content': reply})
                        await self.store.save(session)
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
