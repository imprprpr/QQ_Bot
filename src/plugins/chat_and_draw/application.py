"""Application state and explicit replaceable service boundaries."""
from .commands import CommandHandlers
from .chat_flow import ChatFlow
from .lifecycle import BackgroundLifecycle
from .routing import MessageRouting
from .sessions import SessionStore
from .group_chat import GroupChat
from .bot_interaction import BotInteractions
from .bot_interaction_flow import BotInteractionFlow
from nonebot import get_bot, get_bots
from .llm import close_clients, get_chat_response, get_image_description, direct_vision
from .comfyui import generate_image
from .media import get_image_base64, get_tts_audio
from .stickers import get_sticker
from .jobs import enqueue_draw, stop_jobs, recover_jobs
from .memory import prepare as prepare_event_memory
from .runtime import RuntimeStore
from .delivery import try_send

class BotApplication(CommandHandlers, ChatFlow, BotInteractionFlow, BackgroundLifecycle, MessageRouting):
    def __init__(self, path):
        self.store = SessionStore(path)
        self._group_chat = None
        self._background_task = None
        self._runtime = None
        self._bot_interactions = None

    @property
    def runtime(self):
        if self._runtime is None or self._runtime.path != self.store.path:
            self._runtime = RuntimeStore(self.store.path)
        return self._runtime

    @property
    def group_chat(self):
        if self._group_chat is None or self._group_chat.path != self.store.path:
            self._group_chat = GroupChat(self.store.path)
        return self._group_chat

    @property
    def bot_interactions(self):
        if self._bot_interactions is None or self._bot_interactions.path != self.store.path:
            self._bot_interactions = BotInteractions(self.store.path)
        return self._bot_interactions

    get_bots = staticmethod(get_bots)
    close_clients = staticmethod(close_clients)
    direct_vision = staticmethod(direct_vision)
    enqueue_draw = staticmethod(enqueue_draw)
    generate_image = staticmethod(generate_image)
    get_bot = staticmethod(get_bot)
    get_chat_response = staticmethod(get_chat_response)
    get_image_base64 = staticmethod(get_image_base64)
    get_image_description = staticmethod(get_image_description)
    get_sticker = staticmethod(get_sticker)
    get_tts_audio = staticmethod(get_tts_audio)
    prepare_event_memory = staticmethod(prepare_event_memory)
    recover_jobs = staticmethod(recover_jobs)
    stop_jobs = staticmethod(stop_jobs)
    try_send = staticmethod(try_send)
