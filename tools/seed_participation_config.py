"""Seed the shared NapCat policy from existing environment settings, without overwrite."""
import argparse
import json
import math
import os
from pathlib import Path
import uuid
from dotenv import dotenv_values


def seed(output, root):
    fields=json.loads((root/'deploy/napcat/napcat-plugin-anima-peer-manager/participation.schema.json').read_text(encoding='utf-8'))
    env={**dotenv_values(root/'.env'),**dotenv_values(root/'.env.local'),**os.environ}
    values={}
    for f in fields:
        raw=env.get(f['key'],f['default'])
        if f['type']=='boolean':
            value=str(raw).strip().lower() in {'true','1','yes','on'}
        else:
            value=float(raw)
            if not math.isfinite(value) or (f['integer'] and not value.is_integer()):
                raise ValueError('Invalid policy value: '+f['key'])
            value=max(f['min'],min(f['max'],value))
            if f['integer']:value=int(value)
        values[f['key']]=value
    data={'schema_version':2,'revision':str(uuid.uuid4()),'values':values}
    output.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(output,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o640)
    with os.fdopen(fd,'w',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2)
        stream.write('\n')
    return len(values)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    print('Policy fields created:',seed(args.output,Path(__file__).resolve().parents[1]))
