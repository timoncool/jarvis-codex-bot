import os
from pathlib import Path

ROOT = Path('/opt/jarvis')
os.umask(0o077)
envfile = ROOT/'.env'
if envfile.stat().st_mode & 0o077:
    raise SystemExit('[ERROR] /opt/jarvis/.env must have mode 600')
values = {}
for line in envfile.read_text().splitlines():
    if line and not line.startswith('#') and '=' in line:
        key,value = line.split('=',1)
        values[key] = value
required = ('TELEGRAM_BOT_TOKEN',)
missing = [key for key in required if not values.get(key)]
if missing:
    raise SystemExit('[ERROR] Missing configuration: ' + ', '.join(missing))
chat = int(values.get('TELEGRAM_CHAT_ID', '0'))
os.environ.update(values)
os.environ['TAKOPI__TRANSPORTS__TELEGRAM__BOT_TOKEN'] = values['TELEGRAM_BOT_TOKEN']
os.environ['TAKOPI__TRANSPORTS__TELEGRAM__CHAT_ID'] = str(chat)
os.environ['TAKOPI__TRANSPORTS__TELEGRAM__ALLOWED_USER_IDS'] = '[]'
os.environ['TAKOPI__TRANSPORTS__TELEGRAM__FILES__ALLOWED_USER_IDS'] = '[]'
os.environ['JARVIS_PUBLIC'] = '1'
os.environ['CODEX_HOME'] = '/home/jarvis/.codex'
os.environ['JARVIS_DATA'] = '/opt/jarvis/data'
os.environ['PATH'] = '/opt/jarvis/.venv/bin:/usr/local/bin:/usr/bin:/bin'
Path('/opt/jarvis/data').mkdir(exist_ok=True)
(ROOT/'data/draining').unlink(missing_ok=True)
os.chdir('/opt/jarvis/data')
os.execv('/opt/jarvis/.venv/bin/takopi', ['takopi'])
