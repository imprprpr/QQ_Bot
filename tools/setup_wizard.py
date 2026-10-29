"""AnimaBot first-run setup wizard.

Walks through: model provider → API key → model → connection test → QQ (NapCat) connection
→ default character, then writes .env. Uses only the Python standard library, so it runs
before the bot's dependencies are installed.

    python tools/setup_wizard.py            interactive
    python tools/setup_wizard.py --check    exit 0 if a chat model is configured, 2 if not
    python tools/setup_wizard.py --provider deepseek --api-key sk-... [--model ...] --yes
"""
import argparse
import getpass
import importlib.util
import json
import os
from pathlib import Path
import secrets
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / '.env'
TEMPLATE = ROOT / '.env.example'


def _load_providers():
    spec = importlib.util.spec_from_file_location('anima_providers', ROOT / 'src/plugins/chat_and_draw/providers.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


providers = _load_providers()


# ---------- .env helpers ----------
def read_env(path):
    values = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(path, updates, template=TEMPLATE):
    """Update keys in place (keeping comments and other settings); append keys that are missing."""
    if path.exists():
        shutil.copyfile(path, path.with_name(path.name + '.bak'))
        lines = path.read_text(encoding='utf-8').splitlines()
    else:
        lines = template.read_text(encoding='utf-8').splitlines() if template.exists() else []
    pending = dict(updates)
    for index, line in enumerate(lines):
        key = line.split('=', 1)[0].strip() if '=' in line and not line.lstrip().startswith('#') else None
        if key in pending:
            lines[index] = f'{key}={pending.pop(key)}'
    if pending:
        lines += ['', '# 由配置向导添加'] + [f'{k}={v}' for k, v in pending.items()]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    try:
        os.chmod(path, 0o600)  # contains API keys
    except OSError:
        pass


def configured(env=None, root=ROOT):
    """A chat model is usable from .env, or a profile has been selected in the admin page."""
    env = env if env is not None else {**read_env(root / '.env'), **read_env(root / '.env.local')}
    if os.getenv('LLM_CLIENT_FACTORY') or env.get('LLM_CLIENT_FACTORY'):
        return True
    models_file = Path(env.get('BOT_MODEL_CONFIG_FILE') or root / 'data' / 'models.json')
    models_file = models_file if models_file.is_absolute() else root / models_file
    try:
        if json.loads(models_file.read_text(encoding='utf-8')).get('active'):
            return True
    except (OSError, ValueError):
        pass
    base = env.get('LLM_BASE_URL', '')
    local = base.startswith('http://127.0.0.1') or base.startswith('http://localhost')
    return bool(base and env.get('LLM_MODEL') and (env.get('LLM_API_KEY') or local))


# ---------- terminal helpers ----------
def say(text=''):
    print(text, flush=True)


def ask(prompt, default=None, secret=False):
    suffix = f' [{default}]' if default not in (None, '') else ''
    reader = getpass.getpass if secret else input
    try:
        value = reader(f'{prompt}{suffix}: ').strip()
    except EOFError:
        value = ''
    return value or (default or '')


def choose(prompt, options, default=1):
    """options: list of labels. Returns a 0-based index."""
    for number, label in enumerate(options, 1):
        say(f'  {number:>2}. {label}')
    while True:
        value = ask(prompt, str(default))
        if value.isdigit() and 1 <= int(value) <= len(options):
            return int(value) - 1
        say('  请输入列表中的序号。')


def confirm(prompt, default=True):
    value = ask(prompt + (' (Y/n)' if default else ' (y/N)')).lower()
    return default if not value else value in {'y', 'yes', '是', '好'}


def step(number, title):
    say()
    say(f'━━ 第 {number} 步：{title} ━━')


# ---------- wizard steps ----------
def pick_provider(args):
    data = providers.catalog()
    if args.provider:
        entry = providers.provider(args.provider)
        if not entry:
            raise SystemExit('未知的提供商：' + args.provider + '；可选：' + ', '.join(p['id'] for p in data['providers']))
        return entry
    ordered = sorted(data['providers'], key=lambda p: (not p.get('recommended'), [g['id'] for g in data['groups']].index(p['group'])))
    groups = {g['id']: g['name'] for g in data['groups']}
    labels = [f"{p['name']}{'  ★ 推荐' if p.get('recommended') else ''}  —  {groups[p['group']]} · {p.get('badge', '')}" for p in ordered]
    say('选一个提供聊天模型的平台。拿不准就选 1（DeepSeek）：国内直连、便宜、中文好。')
    return ordered[choose('输入序号', labels)]


def ask_base_url(entry, args):
    if args.base_url:
        return providers.normalize_base_url(args.base_url)
    base = entry.get('base_url', '')
    while True:
        try:
            if entry.get('fields'):
                values = {}
                for field in entry['fields']:
                    say(f"  {field['label']}：{field.get('hint', '')}")
                    values[field['key']] = ask('  ' + field['label'])
                return providers.normalize_base_url(providers.fill_template(base, values))
            if not base:
                base = ask('API 地址（OpenAI 兼容接口，一般以 /v1 结尾）')
            return providers.normalize_base_url(base)
        except providers.ProviderError as error:
            say('  ✗ ' + str(error))
            base = '' if not entry.get('fields') else base


def ask_key(entry, args):
    if entry.get('needs_key') is False:
        return args.api_key or ''
    if args.api_key:
        return args.api_key
    if entry.get('key_url'):
        say('获取 API Key：' + entry['key_url'])
    if entry.get('note'):
        say('提示：' + entry['note'])
    say('粘贴后直接回车（为了安全，输入时屏幕上不会显示字符）。')
    while True:
        key = ask('API Key', secret=True)
        if key and not any(c.isspace() for c in key):
            return key
        say('  ✗ 没有读到有效的 Key，请重新粘贴（Windows 终端可右键粘贴）。')


def ask_proxy(entry, args, env):
    if args.proxy is not None:
        return args.proxy
    if not entry.get('needs_proxy'):
        return env.get('LLM_PROXY', '')
    say('这是海外服务，在国内通常需要代理才能连上。')
    return ask('代理地址（没有就直接回车，例如 http://127.0.0.1:7890）', env.get('LLM_PROXY', ''))


def pick_model(entry, base_url, key, proxy, args):
    if args.model:
        return args.model
    say('正在获取可用的模型列表…')
    try:
        ids = providers.list_models(base_url, key, proxy)
    except providers.ProviderError as error:
        say('  ✗ ' + str(error))
        if args.yes:
            raise SystemExit(1)
        return None  # caller offers: retype key / type model id by hand
    suggested = providers.suggest_model(entry, ids)
    chat = [m for m in ids if not providers.NOT_CHAT.search(m)] or ids
    if args.yes:
        if not suggested:
            raise SystemExit('无法自动选择模型，请用 --model 指定。可用模型：' + ', '.join(chat[:20]))
        return suggested
    shown = ([suggested] if suggested else []) + [m for m in chat if m != suggested][:19]
    say(f'共有 {len(ids)} 个模型，下面是适合聊天的{"（第 1 个是推荐）" if suggested else ""}：')
    labels = shown + ['手动输入其他模型 ID']
    index = choose('输入序号', labels)
    return shown[index] if index < len(shown) else ask_model_id()


def ask_model_id():
    while True:
        value = ask('模型 ID（可在提供商网站的文档里找到）')
        if value and len(value) <= 200 and not any(c.isspace() for c in value):
            return value
        say('  ✗ 模型 ID 不能为空，也不能包含空格。')


def ask_port(default):
    while True:
        value = ask('机器人监听端口（不冲突就直接回车）', str(default))
        if value.isdigit() and 1024 <= int(value) <= 65535:
            return int(value)
        say('  ✗ 请输入 1024 到 65535 之间的数字。')


def test_connection(base_url, key, model, proxy, args):
    if args.no_test:
        return True
    say('正在发送一条测试消息（会产生极少量费用）…')
    try:
        result = providers.test_chat(base_url, key, model, proxy)
    except providers.ProviderError as error:
        say('  ✗ ' + str(error))
        return False
    say(f"  ✓ 连接成功，用时 {result['latency_ms']} ms。模型回复：{result['reply'] or '（空）'}")
    return True


def pick_character(args, env):
    cards = []
    for path in sorted((ROOT / 'characters').glob('*.json')):
        try:
            data = json.loads(path.read_text(encoding='utf-8'))
            cards.append((path.stem, (data.get('data') or data).get('name', path.stem)))
        except (OSError, ValueError):
            continue
    if args.character or not cards:
        return args.character or env.get('DEFAULT_CHARACTER', 'ling'), ''
    if args.yes:
        current = env.get('DEFAULT_CHARACTER', cards[0][0])
        return current, dict(cards).get(current, '')
    say('想让机器人扮演哪个角色？以后可以在 characters 目录放入 SillyTavern 角色卡来添加。')
    current = env.get('DEFAULT_CHARACTER', 'ling')
    default = next((i for i, (stem, _) in enumerate(cards, 1) if stem == current), 1)
    index = choose('输入序号', [f'{name}（{stem}）' for stem, name in cards], default)
    return cards[index]


def main(argv=None):
    parser = argparse.ArgumentParser(description='AnimaBot 配置向导')
    parser.add_argument('--check', action='store_true', help='只检查是否已配置聊天模型（0=已配置，2=未配置）')
    parser.add_argument('--provider'); parser.add_argument('--api-key'); parser.add_argument('--base-url')
    parser.add_argument('--model'); parser.add_argument('--proxy'); parser.add_argument('--character')
    parser.add_argument('--port', type=int); parser.add_argument('--docker', action='store_true', help='在 Docker 中运行：监听 0.0.0.0')
    parser.add_argument('--no-test', action='store_true', help='不发送测试消息')
    parser.add_argument('--yes', action='store_true', help='不提问，使用参数和推荐值')
    parser.add_argument('--env-file', default=str(ENV_FILE))
    args = parser.parse_args(argv)
    env_path = Path(args.env_file)
    env = read_env(env_path)

    if args.check:
        return 0 if configured(None, env_path.parent) else 2

    say('╭──────────────────────────────────────────╮')
    say('│        AnimaBot 配置向导（约 3 分钟）       │')
    say('╰──────────────────────────────────────────╯')
    say('随时按 Ctrl+C 退出，已有的 .env 会先备份为 .env.bak。')
    if env_path.exists() and configured(None, env_path.parent) and not args.yes:
        if not confirm('已经配置过聊天模型。要重新配置吗？', default=False):
            return 0

    step(1, '选择模型提供商')
    entry = pick_provider(args)
    base_url = ask_base_url(entry, args)

    step(2, '填写 API Key')
    key = ask_key(entry, args)
    proxy = ask_proxy(entry, args, env)

    step(3, '选择模型')
    while True:
        model = pick_model(entry, base_url, key, proxy, args)
        if model is None:
            index = choose('怎么处理？', ['重新输入 API Key', '手动填写模型 ID', '换一个代理地址'], 1)
            if index == 0:
                key = ask_key(entry, argparse.Namespace(api_key=None))
                continue
            if index == 2:
                proxy = ask('代理地址（例如 http://127.0.0.1:7890，留空表示不用代理）')
                continue
            model = ask_model_id()
        ok = test_connection(base_url, key, model, proxy, args)
        if ok or args.yes:
            break
        index = choose('怎么处理？', ['重新输入 API Key', '换一个模型', '先保存，稍后再排查'], 1)
        if index == 0:
            key = ask_key(entry, argparse.Namespace(api_key=None))
        elif index == 2:
            break
        else:
            args.model = None
    if not ok and args.yes:
        return 1

    step(4, '连接 QQ（NapCat）')
    port = args.port or int(env.get('PORT') or 8080)
    token = env.get('ONEBOT_ACCESS_TOKEN') or secrets.token_urlsafe(18)
    if not args.yes and not args.port:
        port = ask_port(port)

    step(5, '选择角色')
    stem, name = pick_character(args, env)

    updates = {'LLM_API_KEY': key, 'LLM_BASE_URL': base_url, 'LLM_MODEL': model, 'LLM_PROXY': proxy,
               'PORT': str(port), 'ONEBOT_ACCESS_TOKEN': token, 'DEFAULT_CHARACTER': stem,
               'HOST': '0.0.0.0' if args.docker else env.get('HOST', '127.0.0.1')}
    if name and not env.get('GROUP_CHAT_NICKNAMES'):
        updates['GROUP_CHAT_NICKNAMES'] = name
    write_env(env_path, updates)

    say()
    say('✓ 配置已保存到 ' + str(env_path))
    say()
    say('━━ 最后一步：在 NapCat 里添加连接 ━━')
    say('  1. 打开 NapCat 网页后台 → 网络配置 → 新建 → 「WebSocket 客户端」（反向 WebSocket）')
    say(f'  2. URL 填写：ws://127.0.0.1:{port}/onebot/v11/ws' + ('（NapCat 也在 Docker 中时把 127.0.0.1 换成 bot）' if args.docker else ''))
    say(f'  3. Token 填写：{token}')
    say('  4. 启用并保存。')
    say()
    say('然后启动机器人：Windows 双击「一键启动机器人.bat」，Linux/macOS 运行 ./start.sh')
    say('启动后在 QQ 里给机器人发「/help」试试。')
    say(f'本机管理页：http://127.0.0.1:{port}/manage （运行 python tools/manage.py 查看登录令牌）')
    return 0


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        say('\n已取消，没有修改任何文件。')
        raise SystemExit(130)
