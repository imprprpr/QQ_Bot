"""Single-worker, loopback-only Genie-TTS adapter for the bot.

Install Genie in its own environment. Character weights and reference recordings
are supplied by the operator; they are never part of this repository.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import io
import json
import logging
import os
from pathlib import Path
import re
import time
import wave

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("Max_Cached_Character_Models", "1")

import numpy as np
import onnxruntime as ort
from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field

_session = ort.InferenceSession


class CpuSession(_session):
    def __init__(self, *args, **kwargs):
        options = kwargs.get("sess_options") or ort.SessionOptions()
        options.intra_op_num_threads = int(os.getenv("GENIE_THREADS", "2"))
        options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        options.add_session_config_entry("session.inter_op.allow_spinning", "0")
        kwargs["sess_options"] = options
        super().__init__(*args, **kwargs)


ort.InferenceSession = CpuSession
logger = logging.getLogger("genie-service")
pool = ThreadPoolExecutor(max_workers=1)
lock = asyncio.Lock()
voices = {}
ready = False


def initialize():
    import genie_tts
    from genie_tts.Audio.ReferenceAudio import ReferenceAudio
    from genie_tts.ModelManager import model_manager
    config = json.loads(Path(os.environ["GENIE_VOICES_FILE"]).read_text(encoding="utf-8"))
    # Limit startup memory. Additional roles can share this loaded voice.
    if len(config) != 1:
        raise ValueError("Configure exactly one voice per service instance")
    for name, settings in config.items():
        genie_tts.load_character(name, settings["model_dir"], settings.get("language", "zh"))
        model = model_manager.get(name)
        if model is None:
            raise RuntimeError("MODEL_LOAD_FAILED")
        reference = ReferenceAudio(settings["reference_audio"], settings["reference_text"], model.LANGUAGE)
        if model.PROMPT_ENCODER:
            reference.update_global_emb(model.PROMPT_ENCODER)
        voices[name] = (model, reference)
    return sorted(voices)


def synthesize(text, character):
    from genie_tts.Core.Inference import tts_client
    model, reference = voices[character]
    # The upstream decoder has a per-call token limit. Bound each text chunk.
    sentences = re.findall(r"[^。！？!?；;\n]+[。！？!?；;]?", text)
    chunks = [sentence[i:i + 60] for sentence in sentences for i in range(0, len(sentence), 60)]
    output = []
    for chunk in chunks:
        if not chunk.strip():
            continue
        audio = tts_client.tts(chunk, reference, model.T2S_ENCODER, model.T2S_FIRST_STAGE_DECODER,
                               model.T2S_STAGE_DECODER, model.VITS, model.PROMPT_ENCODER, model.LANGUAGE)
        if audio is None or not audio.size or not np.isfinite(audio).all():
            raise ValueError("INVALID_GENERATED_AUDIO")
        output.append(np.asarray(audio).reshape(-1))
    if not output:
        raise ValueError("EMPTY_GENERATED_AUDIO")
    samples = np.concatenate(output)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(32000)
        out.writeframes(pcm)
    return buffer.getvalue(), len(samples) / 32000


@asynccontextmanager
async def lifespan(app):
    global ready
    started = time.perf_counter()
    names = await asyncio.get_running_loop().run_in_executor(pool, initialize)
    ready = True
    logger.warning("Genie ready: voices=%s startup_seconds=%.2f", names, time.perf_counter() - started)
    yield
    pool.shutdown(wait=False)


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


class SpeechRequest(BaseModel):
    text: str = Field(min_length=1, max_length=300)
    character: str = Field(default="default", min_length=1, max_length=80)


@app.get("/health")
async def health():
    return {"ready": ready, "voices": sorted(voices)}


@app.post("/tts")
async def tts(request: SpeechRequest):
    if request.character not in voices:
        raise HTTPException(400, "UNKNOWN_VOICE")
    try:
        await asyncio.wait_for(lock.acquire(), timeout=1)
    except asyncio.TimeoutError:
        raise HTTPException(429, "TTS_BUSY") from None
    started = time.perf_counter()
    timed_out = False
    try:
        future = asyncio.get_running_loop().run_in_executor(pool, synthesize, request.text, request.character)
        audio, duration = await asyncio.wait_for(future, timeout=float(os.getenv("GENIE_TIMEOUT", "90")))
        elapsed = time.perf_counter() - started
        logger.info("Synthesis complete: characters=%d seconds=%.2f audio_seconds=%.2f", len(request.text), elapsed, duration)
        return Response(audio, media_type="audio/wav", headers={
            "X-Synthesis-Seconds": f"{elapsed:.3f}", "X-Audio-Seconds": f"{duration:.3f}"})
    except asyncio.TimeoutError:
        # Cancelling a Python future does not stop ONNX computation. Restart the
        # dedicated service rather than admitting overlapping inference jobs.
        timed_out = True
        asyncio.get_running_loop().call_later(.5, os._exit, 1)
        raise HTTPException(504, "TTS_TIMEOUT") from None
    except Exception as exc:
        logger.error("Synthesis failed: %s", type(exc).__name__)
        raise HTTPException(502, "TTS_FAILED") from None
    finally:
        if not timed_out:
            lock.release()
