"""Bounded background drawing jobs; switching roles never retargets a result."""
import asyncio
from dataclasses import replace
import os
import time

from nonebot import logger

from .comfyui import generate_image
from .delivery import DeliveryResult, deliver_image, try_send
from .runtime import current_scope, record

_jobs = set()
_reservations = 0


class MessageTarget:
    def __init__(self, bot, event):
        self.bot, self.event = bot, event

    async def send(self, message):
        return await self.bot.send(self.event, message)


async def enqueue_draw(store, bot, event, tags):
    global _reservations
    scope = current_scope.get()
    if scope is None:
        raise RuntimeError("Drawing queue requires an operation context")
    limit = max(1, int(os.getenv("DRAW_QUEUE_LIMIT", "8")))
    if len(_jobs) + _reservations >= limit:
        await record("draw-queue", "failed", error="QUEUE_FULL")
        await try_send(MessageTarget(bot, event), "图片任务排队已满，请稍后再试。", "draw-queue-full")
        return False
    # Reserve synchronously before yielding to SQLite, including other conversations.
    _reservations += 1
    try:
        identity = await scope.store.create_task(scope, tags)
    except BaseException:
        _reservations -= 1
        raise
    scope = replace(scope, task_id=identity)

    async def run():
        token = current_scope.set(scope)
        try:
            image = await generate_image(tags)
            result = DeliveryResult(image_requested=True)
            await deliver_image(MessageTarget(bot, event), image, result)
            async with store.lock(scope.key):
                await store.finish_image(scope.key, scope.character,
                                         tags if result.image_generated else None, result.image_status())
        except asyncio.CancelledError:
            await scope.store.task_update(identity, status="interrupted", error="BOT_SHUTDOWN")
            await record("draw-job", "failed", error="BOT_SHUTDOWN")
            raise
        except Exception as exc:
            logger.warning("Drawing job failed: {}", type(exc).__name__)
            await scope.store.task_update(identity, status="failed", error=type(exc).__name__)
            await record("draw-job", "failed", error=type(exc).__name__)
        finally:
            current_scope.reset(token)
            await scope.store.finish(scope.run_id)

    task = asyncio.create_task(run(), name="draw-" + identity)
    _jobs.add(task)
    _reservations -= 1
    def finished(task):
        _jobs.discard(task)
        if not task.cancelled() and task.exception():
            logger.warning("Drawing task finalization failed: {}", type(task.exception()).__name__)
    task.add_done_callback(finished)
    await record("draw-queue", "queued", task_id=identity)
    return True


async def stop_jobs():
    tasks = list(_jobs)
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def recover_jobs(runtime):
    def recover(conn):
        conn.execute("UPDATE tasks SET status='interrupted',error='PROCESS_RESTARTED',updated=? WHERE status IN ('queued','running')", (time.time(),))
        conn.execute("UPDATE runs SET status='partial',error='PROCESS_RESTARTED',ended=? WHERE status IN ('running','waiting')", (time.time(),))
    await runtime.call(recover)
