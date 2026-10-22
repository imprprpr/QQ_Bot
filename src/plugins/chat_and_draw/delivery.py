"""Independent OneBot sends: optional messages must never block image generation."""
import asyncio
from dataclasses import dataclass
import re
import uuid

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

from .config import ROOT
from .runtime import current_scope, record, save_artifact, task_update

FAILED_IMAGE_DIR = ROOT / "data" / "failed_images"


@dataclass
class DeliveryResult:
    text_sent: bool = False
    image_requested: bool = False
    image_generated: bool = False
    image_sent: bool = False
    saved_path: str | None = None
    sticker_sent: bool = False
    image_queued: bool = False

    def image_status(self):
        if not self.image_requested:
            return ""
        if self.image_queued:
            return "[工具执行结果：图片任务已排队，尚未确认生成或发送。]"
        if self.image_sent:
            return "[工具执行结果：图片已生成，QQ 发送接口返回成功。]"
        if self.image_generated:
            return "[工具执行结果：图片已生成，但 QQ 发送失败，用户尚未收到图片。]"
        return "[工具执行结果：图片服务未成功生成图片，用户尚未收到图片。]"


def log_send_error(stage, exc):
    info = getattr(exc, "info", {})
    code = info.get("retcode") if isinstance(info, dict) else None
    detail = str(info.get("wording") or info.get("message") or "") if isinstance(info, dict) else ""
    detail = re.sub(r"(?:https?://|file:///|base64://)\S+", "[redacted payload]", detail)
    detail = re.sub(r"(?i)(bearer\s+|sk-)[A-Za-z0-9._-]+", "[redacted credential]", detail)
    logger.warning("QQ delivery failed: stage={}, error={}, retcode={}, detail={}",
                   stage, type(exc).__name__, code, detail[:300])


async def try_send(matcher, message, stage):
    kind = getattr(message, "type", "text")
    text = message if isinstance(message, str) else getattr(message, "data", {}).get("text", "")
    try:
        response = await matcher.send(message)
        message_id = response.get("message_id") if isinstance(response, dict) else None
        scope = current_scope.get()
        if scope:
            try:
                await scope.store.outgoing(scope, kind, str(text), "sent", message_id)
            except Exception as exc:
                logger.warning("Message sent; audit write failed: {}", type(exc).__name__)
        await record("send:" + stage, "completed", message_id=message_id, kind=kind)
        logger.info("QQ delivery accepted: stage={}", stage)
        return True
    except Exception as exc:
        log_send_error(stage, exc)
        info = getattr(exc, "info", {})
        await record("send:" + stage, "failed", error=type(exc).__name__,
                     retcode=info.get("retcode") if isinstance(info, dict) else None)
        return False


async def deliver_image(matcher, image, result):
    if image is None:
        await task_update(status="failed", error="GENERATION_FAILED")
        await try_send(matcher, MessageSegment.text("图片生成失败或超时，请稍后再试。"), "draw-failure-notice")
        return
    result.image_generated = True
    try:
        result.saved_path = await save_artifact(image)
    except OSError as exc:
        await record("artifact", "failed", error=type(exc).__name__)
    result.image_sent = await try_send(matcher, MessageSegment.image(image), "image")
    await task_update(status="delivered" if result.image_sent else "delivery_failed",
                      error=None if result.image_sent else "ONEBOT_SEND_FAILED")
    if result.image_sent:
        return

    def save():
        FAILED_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
        path = FAILED_IMAGE_DIR / f"image_{uuid.uuid4().hex}.png"
        path.write_bytes(image)
        return str(path.resolve())
    try:
        if not result.saved_path:
            result.saved_path = await asyncio.to_thread(save)
        logger.warning("Generated image could not be sent to QQ; saved locally: {}", result.saved_path)
    except OSError as exc:
        logger.warning("Unable to save undelivered image: {}", type(exc).__name__)
    await try_send(matcher, MessageSegment.text("图片已生成，但 QQ 发送失败了；已尝试保存在机器人本地，请查看日志。"),
                   "image-failure-notice")
