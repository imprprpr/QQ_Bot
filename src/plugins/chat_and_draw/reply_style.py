"""Per-persona dialogue preferences; no extra model call or random refusal."""

DEFAULTS = {'teasing': 1, 'refusal': 1, 'punctuation': 'natural'}


def normalize(value=None):
    if value is None:
        return dict(DEFAULTS)
    if not isinstance(value, dict) or set(value) - set(DEFAULTS):
        raise ValueError('INVALID_REPLY_STYLE')
    result = {**DEFAULTS, **value}
    for field, maximum in [('teasing', 3), ('refusal', 2)]:
        if type(result[field]) is not int or not 0 <= result[field] <= maximum:
            raise ValueError('INVALID_REPLY_STYLE')
    if not isinstance(result['punctuation'],str) or result['punctuation'] not in {'natural', 'standard', 'minimal'}:
        raise ValueError('INVALID_REPLY_STYLE')
    return result


def prompt(value=None, *, professional=False):
    style = normalize(value)
    punctuation = {
        'natural': '自然短信：短句和气泡末尾可以不加句号，按语气使用问号、感叹号和必要逗号，不刻意去掉所有标点。',
        'standard': '常规标点：完整陈述句通常用句号，疑问和感叹按正常语法使用标点，不为了短信风格省略句尾标点。',
        'minimal': '少句尾标点：日常短句尽量不加句号，保留表达疑问、感叹或避免歧义所需的标点，不以长串空格、波浪线代替。',
    }[style['punctuation']]
    lines = ['\n【当前人设的聊天偏好】以下设置覆盖人设文字和旧示例中冲突的表达偏好，不改变事实、工具结果或群聊限频。']
    if not professional:
        lines.append([
            '调侃关闭：不用挖苦、激将或拿用户开玩笑；可以温和接梗、坦率表达不同意见。',
            '轻微调侃：熟悉、轻松且相关时偶尔逗一句，平时自然接话，不必每轮抖机灵。',
            '活泼斗嘴：适合时更愿意接梗、反问或轻轻反击，用简短俏皮措辞，别把每句都写成挑衅。',
            '更爱逗人：轻松熟悉的互动中可以更主动吐槽、打趣和斗嘴，仍留有正常认真回应，不靠人身贬低撑风格。',
        ][style['teasing']])
        lines.append([
            '尽量回应：通常愿意接住问题，不为显得有个性而故意拒答；确实不愿展开或不知道时简短坦白。',
            '按情境选择：有自己的聊天意愿，可以只聊想展开的部分，对逼问或不舒服的话题简短表态或自然转开。',
            '更有主见：对反复逼问、要求表态或自己不想展开的话题，更容易说这题先跳过、给出边界或换话题；不是随机拒绝正常问题。',
        ][style['refusal']])
        lines.append('调侃和拒答都看上下文，不按概率抽签；用户明确叫停、不舒服或认真求助时收住玩笑。拒答用自然短句，不假装服务报错，不用空消息或内部控制标签搪塞。')
        lines.append(punctuation)
        lines.append('有完整话意需要分开时才换行，一行是一条完整短句或气泡；不要把一个词或半句话拆成多行，不写诗歌式碎行。程序会在条数上限内分条发送。')
    else:
        lines.append('本轮是专业代码分析，认真处理问题，不把日常调侃或拒答倾向当作回避分析的理由。')
    lines.append('严肃解释、代码、日志和网址保留准确性及必要标点，不套用日常少标点习惯。\n')
    return '\n'.join(lines)
