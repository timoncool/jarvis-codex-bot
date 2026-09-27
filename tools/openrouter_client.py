"""Credential-free client for the workspace-scoped OpenRouter worker."""
import argparse
import json
import time
import uuid
from pathlib import Path


def request(operation, arguments=None, *, timeout=600):
    root = Path.cwd().resolve()
    queue = root / '.openrouter'
    queue.mkdir(exist_ok=True)
    ident = uuid.uuid4().hex
    pending = queue / (ident + '.pending')
    temporary = queue / (ident + '.tmp')
    temporary.write_text(json.dumps({'operation': operation, 'arguments': arguments or {}}, ensure_ascii=False))
    temporary.replace(pending)
    result = queue / (ident + '.result')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if result.exists():
            data = json.loads(result.read_text())
            if not data.get('ok'):
                raise RuntimeError(data.get('error', 'OpenRouter request failed'))
            return data['result']
        time.sleep(.3)
    raise TimeoutError('Запрос ещё выполняется. Проверь .openrouter/' + ident + '.result; не запускай повторную платную генерацию.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('operation', help='catalog, schema, models.list, images.generate, tts.create_speech, video_generation.generate, chat.send, ...')
    parser.add_argument('--json', default='{}')
    parser.add_argument('--file', help='JSON arguments file')
    args = parser.parse_args()
    payload = json.loads(Path(args.file).read_text() if args.file else args.json)
    print(json.dumps(request(args.operation, payload), ensure_ascii=False))
