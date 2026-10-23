"""Bot-only text delivery: one real @, no commands, memories or media tools."""
import asyncio
import random
import time

from nonebot import logger
from nonebot.adapters.onebot.v11 import MessageSegment

from .bot_interaction import ids
from .group_chat import number
from .llm import ServiceNotice
from .runtime import operation, record
from .tavern import tavern_manager


class BotInteractionFlow:
    async def handle_bot_interaction(self, bot, context, text='', external_id=None):
        policy = self.bot_interactions
        sent = False
        key = 'bot-interaction:' + context['room'] + ':' + context['target']
        card = tavern_manager.get_active_card()
        try:
            async with operation(self.runtime, key, getattr(card, 'name', 'bot-interaction'),
                                 text or '[主动向配置的机器人搭话]', external_id) as scope:
                if scope is None:
                    return
                context = dict(context)
                context['messages'] = await asyncio.to_thread(self.group_chat.public_context, context['room'], time.time())
                prompt = text or '[在当前群聊话题下，向配置的机器人主动搭话，傲娇地开个轻松玩笑。]'
                reply, _, _, _ = await self.get_chat_response(
                    [{'role': 'user', 'content': prompt}], card=card,
                    group_context=context, silent_failure=True)
                if isinstance(reply, ServiceNotice):
                    return
                reply = ' '.join(reply.split())[:120]
                if not reply or reply.strip('。.!！ ') == '[不参与]':
                    return
                if not await asyncio.to_thread(policy.valid, context, time.time()):
                    return
                # User-configured target only. Model text cannot select recipients.
                message = MessageSegment.at(context['target']) + MessageSegment.text(' ' + reply)
                response = await asyncio.wait_for(bot.send_group_msg(
                    group_id=int(context['group_id']), message=message), 20)
                sent = True
                message_id = response.get('message_id') if isinstance(response, dict) else None
                await scope.store.outgoing(scope, 'bot-interaction', reply, 'sent', message_id)
                await record('bot-interaction', 'completed', target=context['target'],
                             round=context['round'], kind=context['kind'])
                await asyncio.to_thread(self.group_chat.add, context['room'], None,
                                        'bot:' + str(bot.self_id) + ':to:' + context['target'], reply, time.time())
        except Exception as exc:
            # Never retry an ambiguous QQ send; the reserved turn stays consumed.
            logger.warning('Bot interaction failed: {}', type(exc).__name__)
        finally:
            await asyncio.to_thread(policy.finish, context, sent, time.time())

    async def run_bot_interaction_once(self, now=None):
        now = time.time() if now is None else now
        policy = self.bot_interactions
        targets, groups = sorted(ids('BOT_INTERACTION_TARGETS')), sorted(ids('BOT_INTERACTION_GROUPS'))
        if not targets or not groups or not policy.proactive_window(now):
            return
        if random.random() >= min(1, number('BOT_INTERACTION_PROACTIVE_PROBABILITY', .2)):
            return
        # At most one opener per cycle, only in a recently active public group.
        for bot in self.get_bots().values():
            for group in groups[:20]:
                room = policy.room(bot.self_id, group)
                if not await asyncio.to_thread(self.group_chat.recent_activity, room, now):
                    continue
                peers = targets[:20]
                random.shuffle(peers)
                for peer in peers:
                    context = await asyncio.to_thread(policy.reserve, bot.self_id, group, peer, now, 'proactive')
                    if context:
                        await self.handle_bot_interaction(bot, context)
                        return
