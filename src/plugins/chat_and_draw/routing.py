"""Bot routing callbacks."""
from nonebot.adapters.onebot.v11 import Bot, Event
from nonebot.typing import T_State

class MessageRouting:

    async def addressed(self, event: Event) -> bool:
        if getattr(event, 'group_id', None) is not None and self.bot_interactions.is_peer(event.get_user_id()):
            return False  # Known bots never execute human commands, including when disabled.
        return event.get_type() == 'message' and (getattr(event, 'message_type', None) == 'private' or event.is_tome())

    async def group_message(self, event: Event) -> bool:
        return event.get_type() == 'message' and getattr(event, 'group_id', None) is not None

    async def observe_group(self, bot: Bot, event: Event):
        await self.group_chat.observe(bot, event)

    async def chat_addressed(self, bot: Bot, event: Event, state: T_State) -> bool:
        if event.get_type() != 'message':
            return False
        if getattr(event, 'message_type', None) == 'private':
            return str(event.get_user_id()) != str(bot.self_id)
        if getattr(event, 'group_id', None) is None:
            return False
        if self.bot_interactions.is_peer(event.get_user_id()):
            context = await self.bot_interactions.select(bot, event)
        else:
            context = await self.group_chat.select(bot, event)
        if context is None:
            return False
        state['group_context'] = context
        return True
