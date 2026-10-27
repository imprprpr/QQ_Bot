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
        from .proactive import local_now, in_window, eligible, next_time, choose_topic, opener_prompt, opener_text
        from .llm import ServiceNotice
        from .sessions import trim_history
        from .proactive_config import settings
        config = settings()
        realtime = now is None
        now = local_now(config) if realtime else now
        if not in_window(now, config):
            return
        await self.store.reschedule_proactive(now, config)
        epoch = now.timestamp()
        for key in await self.store.due(epoch):
            async with self.store.lock(key):
                session = await self.store.load(key)
                if not session.proactive or not session.private or session.next_proactive > epoch:
                    continue
                session.next_proactive = next_time(now, max(session.last_interaction, epoch), max(session.proactive_last_attempt, epoch), config=config)
                if not eligible(session, now, config):
                    await self.store.save(session)
                    continue
                day = now.date().isoformat()
                topic = choose_topic(session)
                reserved = await self.store.reserve_proactive(session, day, epoch, topic, config['global_daily_limit'])
                if reserved is None:
                    await self.store.save(session)
                    return
                session = reserved
                last_interaction, character = session.last_interaction, session.character
            try:
                bot = self.get_bot(session.bot_id)
                history = [{'role': m['role'], 'content': m['content']} for m in session.history[-6:]]
                history.append({'role': 'user', 'content': opener_prompt(session, topic)})
                (reply, _, _, _) = await self.get_chat_response(history, card=self.current_card(session),
                    silent_failure=True, memory_context={'memories': session.memories, 'summary': session.summary})
                reply = '' if isinstance(reply, ServiceNotice) else opener_text(reply, session.proactive_last_text)
                if not reply:
                    return
                async with self.store.lock(key):
                    current = await self.store.load(key)
                    latest = settings()
                    if (latest['_revision'] != config['_revision']
                            or not in_window(local_now(latest) if realtime else now, latest)):
                        return
                    # A human message or role/opt-in change during generation cancels this greeting.
                    if (not current.proactive or current.proactive_unanswered
                            or current.last_interaction != last_interaction or current.character != character):
                        return
                    current.proactive_unanswered = True
                    current.proactive_last_text = reply
                    await self.store.save(current)  # Ambiguous send failures must not duplicate a greeting.
                    await asyncio.wait_for(bot.send_private_msg(user_id=int(current.user_id), message=reply), 20)
                    current.history = trim_history(current.history + [{'role': 'assistant', 'content': reply}])
                    await self.store.save(current)
            except Exception as exc:
                logger.warning('Proactive message failed: {}', type(exc).__name__)
            # At most one reserved attempt per cycle, including failures/skips.
            return

    async def proactive_chat(self):
        while True:
            await asyncio.sleep(300)
            try:
                await self.run_proactive_once()
                await self.run_bot_interaction_once()
                await asyncio.to_thread(cleanup_tts_cache)
                await asyncio.to_thread(cleanup_images)
                await asyncio.to_thread(self.group_chat.cleanup)
            except Exception as exc:
                logger.warning('Proactive cycle failed: {}', type(exc).__name__)

    async def start_background_task(self):
        if self._background_task is None or self._background_task.done():
            await asyncio.to_thread(self.store.initialize)
            await asyncio.to_thread(self.runtime.initialize)
            await asyncio.to_thread(self.bot_interactions.initialize)
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
