"""Loopback-only management API, protected by a per-installation bearer token."""
import asyncio
import base64
import hmac
import json
import os
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from nonebot import get_bot
from nonebot.adapters.onebot.v11 import MessageSegment

from .delivery import try_send
from .runtime import ARTIFACT_DIR, RuntimeStore, operation
from .sticker_library import library
from .memory import EventMemoryStore


def admin_token(store):
    configured = os.getenv("BOT_ADMIN_TOKEN")
    if configured:
        if len(configured) < 32:
            raise ValueError("BOT_ADMIN_TOKEN must contain at least 32 characters")
        return configured
    path = store.path.parent / "admin-token"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as file:
            file.write(secrets.token_urlsafe(32))
    except FileExistsError:
        pass
    token = path.read_text(encoding="ascii").strip()
    if len(token) < 32:
        raise ValueError("Invalid admin token file")
    return token


def local_only(request):
    allowed = {"127.0.0.1", "::1", "localhost"}
    if not request.client or request.client.host not in allowed or request.url.hostname not in allowed:
        raise HTTPException(403, "仅允许本机访问")
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "跨站请求被拒绝")


async def read_json(request, limit=8 * 1024 * 1024):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > limit:
            raise HTTPException(413, "请求过大")
    try:
        value = json.loads(body)
    except (ValueError, UnicodeError):
        raise HTTPException(400, "无效 JSON")
    if not isinstance(value, dict):
        raise HTTPException(400, "需要 JSON 对象")
    return value


def register_admin(app, get_store):
    async def guard(request):
        local_only(request)
        expected = await asyncio.to_thread(admin_token, get_store())
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode("utf-8"), ("Bearer " + expected).encode("utf-8")):
            raise HTTPException(401, "请输入本机管理令牌")

    @app.get("/manage", response_class=HTMLResponse, include_in_schema=False)
    async def page(request: Request):
        local_only(request)
        return HTMLResponse(Path(__file__).with_name("manage.html").read_text(encoding="utf-8"),
                            headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY",
                                     "Content-Security-Policy": "default-src 'self'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src 'self' blob:; frame-ancestors 'none'"})

    @app.get("/manage/api/participation", include_in_schema=False)
    async def participation(request: Request):
        await guard(request)
        from .participation_status import snapshot
        return await asyncio.to_thread(snapshot,get_store().path)

    @app.get("/manage/api/proactive", include_in_schema=False)
    async def proactive_status(request: Request):
        await guard(request)
        from .proactive_status import snapshot
        return await asyncio.to_thread(snapshot,get_store().path)

    @app.get("/manage/api/persona-catalog", include_in_schema=False)
    async def persona_catalog(request: Request):
        await guard(request)
        from .personas import catalog
        return await asyncio.to_thread(catalog,get_store().path)

    @app.get("/manage/api/overview", include_in_schema=False)
    async def overview(request: Request):
        await guard(request)
        store = get_store()
        runtime = RuntimeStore(store.path)
        runs, tasks = await asyncio.gather(runtime.list_rows("runs"), runtime.list_rows("tasks"))
        def roles():
            conn = store._connect()
            try:
                return [{"session_key": r[0], "character": r[1], "updated": r[3],
                         "history_count": len((data := json.loads(r[2])).get("history", [])),
                         "memories": data.get("memories", []), "summary": data.get("summary", "")}
                        for r in conn.execute("SELECT session_key,character,data,updated FROM role_states ORDER BY updated DESC LIMIT 200")]
            finally:
                conn.close()
        def usage(conn):
            totals = {"input_tokens": 0, "output_tokens": 0, "thought_tokens": 0, "cached_tokens": 0, "total_tokens": 0, "calls": 0}
            for row in conn.execute("SELECT detail FROM run_events WHERE stage='usage'"):
                detail = json.loads(row[0])
                totals["calls"] += 1
                for name in totals:
                    value = detail.get(name, 0)
                    if name != "calls" and isinstance(value, (int, float)):
                        totals[name] += value
            return totals
        return {"runs": runs, "tasks": tasks, "roles": await asyncio.to_thread(roles),
                "usage": await runtime.call(usage), "provider": "openai-compatible",
                "model": os.getenv("LLM_MODEL", "deepseek-chat")}

    @app.get("/manage/api/runs/{identity}", include_in_schema=False)
    async def run_detail(identity: str, request: Request):
        await guard(request)
        data = await RuntimeStore(get_store().path).run_detail(identity)
        if not data:
            raise HTTPException(404, "记录不存在")
        return data

    @app.put("/manage/api/memory", include_in_schema=False)
    async def memory(request: Request):
        await guard(request)
        data = await read_json(request, 20000)
        notes, summary = data.get("memories", []), data.get("summary", "")
        if not isinstance(notes, list) or len(notes) > 30 or any(not isinstance(n, str) or len(n) > 300 for n in notes) or not isinstance(summary, str) or len(summary) > 4000:
            raise HTTPException(400, "记忆最多30条，每条300字；摘要最多4000字")
        key, character = data.get("session_key"), data.get("character")
        if not isinstance(key, str) or not isinstance(character, str):
            raise HTTPException(400, "缺少会话与角色标识")
        store = get_store()
        async with store.lock(key):
            try:
                await store.update_memory(key, character, list(dict.fromkeys(n.strip() for n in notes if n.strip())), summary.strip())
            except ValueError:
                raise HTTPException(404, "角色记忆不存在")
        return {"ok": True}

    @app.get("/manage/api/events", include_in_schema=False)
    async def events(request: Request, session_key: str, character: str):
        await guard(request)
        return await EventMemoryStore(get_store().path).list(session_key, character)

    @app.post("/manage/api/events/forget", include_in_schema=False)
    async def forget_event(request: Request):
        await guard(request)
        data = await read_json(request, 4000)
        key, character, identity = data.get("session_key"), data.get("character"), data.get("id")
        if not all(isinstance(value, str) and value for value in (key, character, identity)):
            raise HTTPException(400, "需要会话、角色和记忆ID")
        store = get_store()
        async with store.lock(key):
            changed = await EventMemoryStore(store.path).forget(key, character, identity)
        if not changed:
            raise HTTPException(404, "该角色的自动记忆不存在")
        return {"ok": True}

    async def artifact(identity):
        task = await RuntimeStore(get_store().path).task(identity)
        if not task or not task.get("artifact"):
            raise HTTPException(404, "图片尚未生成或未保留")
        path = Path(task["artifact"]).resolve()
        if not path.is_relative_to(ARTIFACT_DIR.resolve()) or not path.is_file():
            raise HTTPException(404, "图片文件不可用")
        return task, path

    @app.get("/manage/api/tasks/{identity}/image", include_in_schema=False)
    async def task_image(identity: str, request: Request):
        await guard(request)
        _, path = await artifact(identity)
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "no-store"})

    @app.post("/manage/api/tasks/{identity}/resend", include_in_schema=False)
    async def resend(identity: str, request: Request):
        await guard(request)
        task, path = await artifact(identity)
        try:
            bot_id, kind, group_id, user_id = json.loads(task["session_key"])
            bot = get_bot(bot_id)
            if kind not in {"group", "private"} or not str(user_id).isdigit() or (kind == "group" and not str(group_id).isdigit()):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(409, "原机器人未连接或会话不可用")
        runtime = RuntimeStore(get_store().path)
        class OriginalTarget:
            async def send(self, message):
                if kind == "group":
                    return await bot.send_group_msg(group_id=int(group_id), message=message)
                return await bot.send_private_msg(user_id=int(user_id), message=message)
        if path.stat().st_size > 32 * 1024 * 1024:
            raise HTTPException(413, "图片过大，请手动处理")
        async with operation(runtime, task["session_key"], task["character"], "[管理员重发已有图片]"):
            data = await asyncio.to_thread(path.read_bytes)
            ok = await try_send(OriginalTarget(), MessageSegment.image(data), "manual-image-resend")
            if ok:
                await runtime.task_update(identity, status="delivered", error=None)
                store = get_store()
                async with store.lock(task["session_key"]):
                    await store.finish_image(task["session_key"], task["character"], None,
                                             "[工具执行结果：管理员已重发已有图片，QQ发送接口返回成功。]")
        return {"ok": ok}

    @app.get("/manage/api/stickers", include_in_schema=False)
    async def stickers(request: Request):
        await guard(request)
        return await asyncio.to_thread(library.list)

    @app.post("/manage/api/stickers", include_in_schema=False)
    async def add_sticker(request: Request):
        await guard(request)
        data = await read_json(request)
        try:
            blob = base64.b64decode(data.get("base64", ""), validate=True)
            result = await asyncio.to_thread(library.add, blob, data)
        except (ValueError, TypeError, OSError):
            raise HTTPException(400, "文件或表情标签无效，支持5MiB以内的GIF/PNG/JPEG/WebP")
        return result

    @app.delete("/manage/api/stickers/{identity}", include_in_schema=False)
    async def delete_sticker(identity: str, request: Request):
        await guard(request)
        try:
            await asyncio.to_thread(library.delete, identity)
        except ValueError:
            raise HTTPException(404, "表情不存在")
        return {"ok": True}

    @app.get("/manage/api/stickers/{identity}/image", include_in_schema=False)
    async def sticker_image(identity: str, request: Request):
        await guard(request)
        try:
            path = await asyncio.to_thread(library.asset, identity)
        except ValueError:
            raise HTTPException(404, "表情不存在")
        return FileResponse(path)
