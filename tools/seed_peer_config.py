"""Seed a NapCat shared config without revealing IDs, secrets or overwriting files."""
import argparse
import json
import os
from pathlib import Path
import re
import uuid

from dotenv import dotenv_values


def parse(value):
    values = sorted(set(filter(None, re.split(r'[\s,，;；]+', str(value or '')))))
    if len(values) > 1000 or any(not re.fullmatch(r'[1-9][0-9]{0,19}', item) for item in values):
        raise ValueError('名单包含非数字项或超过1000项；未写入任何配置。')
    return values


def seed(output, root):
    values = {**dotenv_values(root/'.env'), **dotenv_values(root/'.env.local')}
    targets, groups = parse(values.get('BOT_INTERACTION_TARGETS')), parse(values.get('BOT_INTERACTION_GROUPS'))
    data = {'schema_version':1, 'revision':uuid.uuid4().hex,
            'enabled':str(values.get('BOT_INTERACTION_ENABLED', 'true')).lower() in {'true','1','on','yes'},
            'targets':targets, 'groups':groups, 'known_targets':targets}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return {'targets':len(targets), 'groups':len(groups)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print('Created shared peer configuration:', seed(args.output, Path(__file__).resolve().parents[1]))
