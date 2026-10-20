import nonebot
from nonebot.adapters.onebot.v11 import Adapter as ONEBOT_V11Adapter

# Initialize nonebot
# NoneBot's DEBUG initialization dumps the full .env, including API keys.
# Use INFO during initialization even if an older .env sets LOG_LEVEL=DEBUG.
nonebot.init(log_level="INFO")
app = nonebot.get_asgi()

# Register adapters
driver = nonebot.get_driver()
driver.register_adapter(ONEBOT_V11Adapter)

# Load plugins
nonebot.load_builtin_plugins("echo")
nonebot.load_plugin("src.plugins.chat_and_draw")

if __name__ == "__main__":
    nonebot.run()
