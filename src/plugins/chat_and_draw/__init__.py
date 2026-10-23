"""NoneBot registration only; application behavior lives in focused modules."""
from nonebot import get_driver, on_command, on_message
from .application import BotApplication
from .config import DATABASE_PATH
from .admin import register_admin

app = BotApplication(DATABASE_PATH)
group_observer = on_message(rule=app.group_message, priority=8, block=False)
group_observer.handle()(app.observe_group)

cmd_cards = on_command('cards', aliases={'角色列表'}, rule=app.addressed, priority=5, block=True)
app.cmd_cards = cmd_cards
cmd_cards.handle()(app.handle_cards)

cmd_switch = on_command('switch', aliases={'切换角色'}, rule=app.addressed, priority=5, block=True)
app.cmd_switch = cmd_switch
cmd_switch.handle()(app.handle_switch)

cmd_reset = on_command('reset', aliases={'重置记忆'}, rule=app.addressed, priority=5, block=True)
app.cmd_reset = cmd_reset
cmd_reset.handle()(app.handle_reset)

cmd_help = on_command('help', aliases={'帮助', '菜单'}, rule=app.addressed, priority=5, block=True)
app.cmd_help = cmd_help
cmd_help.handle()(app.handle_help)

cmd_proactive = on_command('proactive', aliases={'主动聊天'}, rule=app.addressed, priority=5, block=True)
app.cmd_proactive = cmd_proactive
cmd_proactive.handle()(app.handle_proactive)

cmd_draw = on_command('draw', aliases={'画图'}, rule=app.addressed, priority=5, block=True)
app.cmd_draw = cmd_draw
cmd_draw.handle()(app.handle_draw)

cmd_memory = on_command('memory', aliases={'记忆'}, rule=app.addressed, priority=5, block=True)
app.cmd_memory = cmd_memory
cmd_memory.handle()(app.handle_memory)

cmd_remember = on_command('remember', aliases={'记住'}, rule=app.addressed, priority=5, block=True)
app.cmd_remember = cmd_remember
cmd_remember.handle()(app.handle_remember)

cmd_forget = on_command('forget', aliases={'忘记'}, rule=app.addressed, priority=5, block=True)
app.cmd_forget = cmd_forget
cmd_forget.handle()(app.handle_forget)

cmd_events = on_command('events', aliases={'共同记忆'}, rule=app.addressed, priority=5, block=True)
app.cmd_events = cmd_events
cmd_events.handle()(app.handle_events)

cmd_forget_event = on_command('forgetevent', aliases={'删除事件'}, rule=app.addressed, priority=5, block=True)
app.cmd_forget_event = cmd_forget_event
cmd_forget_event.handle()(app.handle_forget_event)

chat_handler = on_message(rule=app.chat_addressed, priority=10, block=True)
app.chat_handler = chat_handler
chat_handler.handle()(app.handle_message)
driver = get_driver()
register_admin(driver.server_app, lambda: app.store)
driver.on_startup(app.start_background_task)
driver.on_shutdown(app.stop_background_task)
