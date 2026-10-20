"""Shared dialogue guidance; hints never suppress explicit user requests."""
from datetime import datetime
import re

DIALOGUE_STYLE = """【自然聊天方式】
- 使用自然简体中文，语气服从当前角色，句子长短随内容变化。一个完整意思保持完整，通常一到两条消息，必要时再分段。
- 接住用户正在说的事。用户只回“嗯、好、哈哈”时，先结合上下文判断承接还是收尾，不必总反问或另开话题。
- 用户说晚安、先忙或准备离开时，自然告别，给对方空间；若同时提出问题或请求，先处理该请求。
- 可以有稳定喜好和不同意见，也可以承认记错；无需每句话撒娇、叫昵称、加语气词或发送表情。
- 旧事只在相关时自然提起，不逐条展示记忆，也不把模型推测当作用户事实。角色设定中的虚构日常需前后连贯。
- 没有成功的工具结果，不声称照片已发、语音已发或事情已经办好。
- 图片下载或识别失败时，只说明自己这边没能读到内容，不推断用户网络不好、图片模糊，也不把旧话题硬套成图片内容。
"""


def dialogue_context(text, now=None):
    now = now or datetime.now().astimezone()
    lines = [f"【本轮时间】{now.isoformat(timespec='minutes')}（服务器本地时区；相对日期以此为准）"]
    request = re.search(r"[?？]|怎么办|怎么做|能不能|可以.*吗|发.*(?:图|照|语音)|画.*图", text, re.I)
    if not request and re.search(r"晚安|先睡了|去睡了|先忙了|不聊了|回头聊|good\s*night|bye", text, re.I):
        lines.append("【接话提示】用户正在收尾，简短温和回应，不追问、不挽留、不引入旧事件或新话题。")
    elif re.fullmatch(r"[嗯哦噢好行哈啊呀谢]+[的啦了吧呀呢哦～~!！。. ]*|ok|收到|知道了", text.strip(), re.I):
        lines.append("【接话提示】这是简短回应，结合上一话题自然承接；允许话题停在这里，不为维持聊天强行提问。")
    return "\n".join(lines)
