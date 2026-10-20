import asyncio
import json
import os
import random
import time
from pathlib import Path

import aiohttp
from nonebot import logger

from .config import DRAW_POLL_INTERVAL, DRAW_TIMEOUT
from .runtime import record, task_update

COMFYUI_SERVER = os.getenv("COMFYUI_SERVER", "http://127.0.0.1:8188").rstrip("/")
WORKFLOW_FILE = Path(__file__).with_name("workflow_api.json")
_draw_semaphore = asyncio.Semaphore(1)


def build_workflow(dynamic_tags):
    with open(WORKFLOW_FILE, encoding="utf-8") as file:
        workflow = json.load(file)
    # 1. 设置正向提示词 (支持新工作流 Node 3，同时兼容旧版 Node 24)
    positive_node_ids = [os.getenv("DRAW_POSITIVE_NODE", "3"), "24"]
    matched = False
    for nid in positive_node_ids:
        if nid in workflow and "wildcard_text" in workflow[nid].get("inputs", {}):
            matched = True
            # Quality/style tags are configurable per deployment.
            base_quality_tags = os.getenv("DRAW_BASE_TAGS", "newest, masterpiece, best quality, score_7, score_8_up, score_9, @piromizu")
            new_text = f"{base_quality_tags}, {dynamic_tags}"
            workflow[nid]["inputs"]["wildcard_text"] = new_text
            workflow[nid]["inputs"]["populated_text"] = new_text
            break

    if not matched:
        raise ValueError("Positive prompt node missing; check DRAW_POSITIVE_NODE")

    # 2. 随机种子 (支持新工作流 Node 37，同时兼容旧版 Node 65)
    seed_node_ids = ["37", "65"]
    for sid in seed_node_ids:
        if sid in workflow and "inputs" in workflow[sid]:
            workflow[sid]["inputs"]["seed"] = random.randint(1, 1000000000000000)
            break

    # 3. 性能与画质平衡参数优化 (支持 .env 自定义调节)
    draw_steps = int(os.getenv("DRAW_STEPS", "22"))
    draw_width = int(os.getenv("DRAW_WIDTH", "640"))
    draw_height = int(os.getenv("DRAW_HEIGHT", "960"))
    draw_cfg = float(os.getenv("DRAW_CFG", "4.5"))
    draw_sampler = os.getenv("DRAW_SAMPLER", "euler_ancestral")

    # 步数 (22 步：细节完整且出图迅速)
    if "39" in workflow and "inputs" in workflow["39"]:
        workflow["39"]["inputs"]["value"] = draw_steps

    # CFG 引导系数 (4.5：色彩柔和自然，避免画面过曝/烧焦)
    if "41" in workflow and "inputs" in workflow["41"]:
        workflow["41"]["inputs"]["value"] = draw_cfg

    # 采样器 (euler_ancestral：二次元出图最快且线条灵动)
    if "5" in workflow and "inputs" in workflow["5"]:
        workflow["5"]["inputs"]["sampler_name"] = draw_sampler
        if "scheduler" in workflow["5"]["inputs"]:
            workflow["5"]["inputs"]["scheduler"] = os.getenv("DRAW_SCHEDULER", "normal")

    # 黄金平衡分辨率 640 x 960 (相比 512x768 画质细节大幅提升，显存与耗时仅增加少量)
    if "23" in workflow and "inputs" in workflow["23"]:
        workflow["23"]["inputs"]["value"] = draw_width
    if "31" in workflow and "inputs" in workflow["31"]:
        workflow["31"]["inputs"]["value"] = draw_height

    return workflow


async def _generate(dynamic_tags):
    # Queue wait is covered by the outer overall deadline as well.
    async with _draw_semaphore:
        await task_update(status="running")
        workflow = await asyncio.to_thread(build_workflow, dynamic_tags)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(f"{COMFYUI_SERVER}/prompt", json={"prompt": workflow}) as response:
                response.raise_for_status()
                data = await response.json()
            prompt_id = data.get("prompt_id")
            if not prompt_id or data.get("node_errors"):
                logger.warning("ComfyUI rejected workflow: node_error_ids={}",
                               list(data.get("node_errors", {})))
                return None
            logger.info("ComfyUI task accepted: prompt_id={}", prompt_id)
            await task_update(prompt_id=prompt_id)
            await record("comfyui", "accepted", prompt_id=prompt_id)
            while True:
                await asyncio.sleep(DRAW_POLL_INTERVAL)
                async with session.get(f"{COMFYUI_SERVER}/history/{prompt_id}") as response:
                    response.raise_for_status()
                    history = await response.json()
                item = history.get(prompt_id)
                if item is None:
                    continue
                status = item.get("status", {})
                if status.get("status_str") == "error":
                    logger.warning("ComfyUI reported an execution error")
                    return None
                outputs = item.get("outputs", {})
                # Prefer actual SaveImage output over intermediate previews.
                ordered = sorted(outputs, key=lambda nid: workflow.get(nid, {}).get("class_type") != "SaveImage")
                for node_id in ordered:
                    images = outputs[node_id].get("images", [])
                    if not images:
                        continue
                    info = images[0]
                    params = {"filename": info["filename"], "subfolder": info.get("subfolder", ""),
                              "type": info.get("type", "output")}
                    async with session.get(f"{COMFYUI_SERVER}/view", params=params) as response:
                        response.raise_for_status()
                        return await response.read() or None
                if status.get("completed") or status.get("status_str") == "success":
                    return None


async def generate_image(dynamic_tags: str) -> bytes | None:
    started = time.monotonic()
    logger.info("ComfyUI generation requested: timeout={}s", DRAW_TIMEOUT)
    try:
        image = await asyncio.wait_for(_generate(dynamic_tags), timeout=DRAW_TIMEOUT)
        logger.info("ComfyUI task finished: elapsed={:.1f}s, image_bytes={}",
                    time.monotonic() - started, len(image) if image else 0)
        await record("comfyui", "completed" if image else "failed", elapsed=round(time.monotonic() - started, 3), bytes=len(image) if image else 0)
        return image
    except (aiohttp.ClientError, asyncio.TimeoutError, OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning("Image generation failed: {}", type(exc).__name__)
        await record("comfyui", "failed", error=type(exc).__name__, elapsed=round(time.monotonic() - started, 3))
        return None
