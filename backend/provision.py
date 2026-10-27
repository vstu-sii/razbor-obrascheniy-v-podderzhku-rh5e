"""One-shot local volume provisioning. No secret values leave this process."""
from pathlib import Path
import os
import secrets

p=Path('/secrets')
p.mkdir(exist_ok=True)
for name in ('db-password','metrics-token','grafana-password'):
    f=p/name
    try:
        with f.open('x',encoding='utf-8') as out: out.write(secrets.token_urlsafe(32))
    except FileExistsError: pass
    os.chmod(f,0o644)
for name in ('/logs','/loki-data','/tempo-data','/alloy-data'):
    path=Path(name);path.mkdir(exist_ok=True);os.chown(path,10001,10001)
print('Local volumes initialized')
