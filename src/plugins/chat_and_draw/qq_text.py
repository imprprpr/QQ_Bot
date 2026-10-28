"""Conservative QQ plain text rendering; preserve fenced code literally."""
import re


class QQText(str):
    """Already-rendered text: avoid reinterpreting literal source as markup."""


CQ_CODE = re.compile(r'\[\s*CQ\s*:', re.I)


def contains_cq_code(text):
    """True when text would be parsed as a OneBot CQ segment if sent as a plain string."""
    return isinstance(text, str) and bool(CQ_CODE.search(text))


def _inline(text):
    code = []
    def keep(match):
        code.append(match.group(2))
        return '\x00CODE' + str(len(code)-1) + '\x00'
    text = re.sub(r'(`+)(.+?)\1', keep, text)
    text = re.sub(r'!?\[([^\]\n]*)\]\((https?://[^\s)]+)\)', r'\1（\2）', text)
    text = re.sub(r'\*\*(?=\S)(.+?\S|\S)\*\*', r'\1', text)
    text = re.sub(r'(?<!\w)\*(?=\S)([^*\n]+?\S|[^*\s])\*(?!\w)', r'\1', text)
    # Do not strip underscores: Python names such as __init__ are meaningful.
    text = re.sub(r'~~(?=\S)(.+?\S|\S)~~', r'\1', text)
    for index, value in enumerate(code):
        text = text.replace('\x00CODE'+str(index)+'\x00', value)
    return text


def qq_plain_text(text):
    if isinstance(text, QQText):
        return text
    if not text or not text.strip():
        return ''
    lines, result = text.replace('\r\n', '\n').split('\n'), []
    fence = None
    table = False
    for index, line in enumerate(lines):
        marker = re.match(r'^\s*(`{3,}|~{3,})(.*)$', line)
        if marker:
            if fence is None:
                fence = marker.group(1)
                continue
            if marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) and not marker.group(2).strip():
                fence = None
                continue
        if fence:
            result.append(line)
            continue
        if re.fullmatch(r'\s*([-*_])(?:\s*\1){2,}\s*', line):
            continue
        if re.fullmatch(r'\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)+\|?\s*', line):
            continue
        if index+1 < len(lines) and '|' in line and re.fullmatch(r'[\s|:\-]+', lines[index+1]) and '-' in lines[index+1]:
            table = True
        if table and '|' in line:
            result.append('；'.join(_inline(value.strip()) for value in line.strip().strip('|').split('|')))
            continue
        table = False
        # Preserve ordinary source comments when adjacent to clear code.
        next_line = lines[index+1] if index+1 < len(lines) else ''
        source_comment = line.lstrip().startswith('# ') and bool(re.match(
            r'\s*(?:def |class |async def |import |from |return\b|\w+\s*=)', next_line))
        if not source_comment:
            line = re.sub(r'^\s{0,3}#{1,6}\s+', '', line)
        line = re.sub(r'^\s{0,3}>\s?', '', line)
        line = re.sub(r'^(\s*)[-*+]\s+', r'\1', line)
        result.append(_inline(line))
    return QQText('\n'.join(result).strip('\n'))


def qq_chunks(text, limit=1600):
    """Split at readable boundaries without removing source indentation."""
    text = qq_plain_text(text)
    result = []
    while len(text) > limit:
        cut = text.rfind('\n\n', 0, limit)
        if cut >= limit//3:
            cut += 2
        else:
            cut = text.rfind('\n', 0, limit)
            cut = cut+1 if cut >= limit//3 else limit
        result.append(text[:cut].strip('\n'))
        text = text[cut:]
    if text.strip():
        result.append(text.strip('\n'))
    return result


def source_text(text):
    """Leave code and logs intact even when delivery is called without a mode."""
    return bool(re.search(r'```|(?:^|\n)(?: {4}|\t|async def |def |class |import |from \w+ import |Traceback \()', text))


def sms_chunks(text, max_bubbles=3, target=75):
    """Pack meaning-sized chat bubbles, preserving order and all nonspace text."""
    text = qq_plain_text(text)
    if not text.strip():
        return []
    max_bubbles = max(1, min(3, max_bubbles))
    atoms = []
    def protect(match):
        atoms.append(match.group())
        return '\x00ATOM' + str(len(atoms)-1) + '\x00'
    protected = re.sub(r'https?://[^\s<>]+', protect, str(text))
    bubbles = []
    for paragraph in re.split(r'\n+', protected):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        sentences = re.findall(r'.+?(?:[。！？!?；;]+[”’」』）》）"\x27]*|$)', paragraph)
        units = []
        for sentence in sentences:
            # Very long sentences can pause at clauses, never within a URL.
            if len(sentence) > target * 1.8:
                units.extend(re.findall(r'.+?(?:[，,]+|$)', sentence))
            else:
                units.append(sentence)
        current = ''
        for unit in units:
            if current and len(current + unit) > target:
                bubbles.append(current.strip())
                current = ''
            current += unit
        if current.strip():
            bubbles.append(current.strip())
    # Merge tiny fragments, not complete short clauses separated by the model.
    index = 0
    while index < len(bubbles)-1:
        if len(re.sub(r'\s','',bubbles[index])) < 5:
            bubbles[index:index+2] = [bubbles[index]+' '+bubbles[index+1]]
        else:
            index += 1
    while len(bubbles) > max_bubbles:
        index = min(range(len(bubbles)-1), key=lambda i:len(bubbles[i])+len(bubbles[i+1]))
        bubbles[index:index+2] = [bubbles[index]+' '+bubbles[index+1]]
    result = []
    for bubble in bubbles:
        for index, atom in enumerate(atoms):
            bubble = bubble.replace('\x00ATOM'+str(index)+'\x00', atom)
        result.append(QQText(bubble))
    return result
