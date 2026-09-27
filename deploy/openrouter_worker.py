"""Official SDK connector. Credentials remain outside Codex workspaces."""
import asyncio
import base64
import inspect
import json
import logging
import mimetypes
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx
from openrouter import OpenRouter

ROOT = Path('/opt/jarvis/data/topics')
ALLOWED = {
    'models': ('list', 'get', 'list_for_user'),
    'images': ('generate', 'list_models', 'list_model_endpoints'),
    'tts': ('create_speech',),
    'stt': ('create_transcription',),
    'video_generation': ('generate', 'get_generation', 'get_video_content', 'list_videos_models'),
    'chat': ('send',),
    'responses': ('send',),
    'embeddings': ('generate', 'list_models'),
    'rerank': ('rerank',),
    'endpoints': ('list',),
    'providers': ('list',),
}
BLOCKED_ARGS = {'server_url', 'http_headers', 'retries', 'callback_url', 'timeout_ms', 'stream', 'http_referer', 'x_open_router_title', 'x_open_router_categories'}
GENERATE = {'images.generate', 'tts.create_speech', 'stt.create_transcription', 'video_generation.generate', 'chat.send', 'responses.send', 'embeddings.generate', 'rerank.rerank'}
logging.basicConfig(level=logging.INFO, format='%(levelname)s %(message)s')


def ledger():
    db = sqlite3.connect('/opt/jarvis/data/media.sqlite', timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,kind TEXT,created REAL,cost REAL,status TEXT,generation TEXT)')
    db.execute('CREATE TABLE IF NOT EXISTS sdk_jobs(workspace TEXT,request TEXT UNIQUE,operation TEXT,status TEXT,remote_id TEXT,result TEXT)')
    return db


def inside(path, root):
    resolved = path.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError('Файл должен находиться в текущем разговоре')
    return resolved


def materialize(value, root):
    if isinstance(value, dict):
        if '$file' in value:
            path = inside(root / value['$file'], root)
            if path.stat().st_size > 50 * 1024 * 1024:
                raise ValueError('Файл для модели превышает 50 МБ')
            encoded = base64.b64encode(path.read_bytes()).decode()
            if value.get('encoding', 'data_url') == 'base64':
                return encoded
            return 'data:' + (mimetypes.guess_type(path.name)[0] or 'application/octet-stream') + ';base64,' + encoded
        return {k: materialize(v, root) for k, v in value.items()}
    if isinstance(value, list):
        return [materialize(v, root) for v in value]
    return value


def method(sdk, operation):
    group, name = operation.split('.', 1)
    if name not in ALLOWED.get(group, ()):
        raise ValueError('Неизвестная операция. Используй catalog.')
    return getattr(getattr(sdk, group), name + '_async')


def dump(value):
    # Paginated SDK responses wrap the payload alongside a callable `next`.
    if hasattr(value, 'result') and hasattr(value, 'next'):
        value = value.result
    return value.model_dump(mode='json', by_alias=True, exclude_none=True) if hasattr(value, 'model_dump') else value


def store_media(value, root, ident, files, counter):
    if isinstance(value, list):
        return [store_media(v, root, ident, files, counter) for v in value]
    if not isinstance(value, dict):
        return value
    value = dict(value)
    encoded = value.pop('b64_json', None)
    data_url = value.get('url', '')
    mime = value.get('media_type', 'image/png')
    if isinstance(data_url, str) and data_url.startswith('data:') and ';base64,' in data_url:
        mime, encoded = data_url[5:].split(';base64,', 1)
        value.pop('url')
    # Chat-completion audio uses {data: base64, transcript: ...}.
    if 'transcript' in value and isinstance(value.get('data'), str):
        encoded = value.pop('data')
        mime = 'audio/wav'
    if encoded:
        counter[0] += 1
        ext = {'image/png': '.png', 'image/jpeg': '.jpg', 'image/webp': '.webp', 'image/svg+xml': '.svg', 'audio/wav': '.wav'}.get(mime, '.bin')
        path = inside(root / 'output' / f'{ident}-{counter[0]}{ext}', root)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(base64.b64decode(encoded))
        value['file'] = str(path.relative_to(root))
        files.append(value['file'])
    return {k: store_media(v, root, ident, files, counter) for k, v in value.items()}


async def execute(sdk, operation, arguments, root, ident):
    if operation == 'catalog':
        return {group: [name for name in names if hasattr(getattr(sdk, group), name + '_async')] for group, names in ALLOWED.items()}
    if operation == 'schema':
        fn = method(sdk, arguments['operation'])
        return {'operation': arguments['operation'], 'parameters': {name: {'required': p.default is inspect.Parameter.empty, 'type': str(p.annotation)} for name, p in inspect.signature(fn).parameters.items() if name not in BLOCKED_ARGS}, 'description': inspect.getdoc(fn)}
    fn = method(sdk, operation)
    if BLOCKED_ARGS.intersection(arguments):
        raise ValueError('Параметры транспорта задаёт подключение, а не агент')
    arguments = materialize(arguments, root)
    call_id = None
    with ledger() as db:
        if operation.startswith('video_generation.get_'):
            owned = db.execute('SELECT 1 FROM sdk_jobs WHERE workspace=? AND remote_id=?', (str(root), arguments.get('job_id'))).fetchone()
            if not owned:
                raise ValueError('Видеозадача не принадлежит этому разговору')
        if operation in GENERATE:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT COUNT(*) FROM calls').fetchone()[0] >= int(os.environ.get('OPENROUTER_CALL_LIMIT', '20')):
                raise ValueError('Лимит генераций демо исчерпан')
            call_id = db.execute('INSERT INTO calls(kind,created,status) VALUES(?,?,?)', (operation, time.time(), 'started')).lastrowid
    signature = inspect.signature(fn).parameters
    if 'provider' in signature and operation in {'images.generate', 'chat.send', 'responses.send', 'embeddings.generate'}:
        arguments.setdefault('provider', {}).setdefault('allow_fallbacks', False)
    try:
        response = await fn(**arguments, retries=None, timeout_ms=240000)
        files = []
        if isinstance(response, httpx.Response):
            content = await response.aread()
            mime = response.headers.get('content-type', '').split(';')[0]
            if not mime.startswith(('audio/', 'video/', 'application/octet-stream')):
                raise ValueError('Сервис не вернул ожидаемый медиафайл')
            ext = '.mp4' if operation.startswith('video') else ('.pcm' if mime.startswith('audio/pcm') else '.mp3')
            path = inside(root / 'output' / (ident + ext), root)
            path.parent.mkdir(exist_ok=True)
            if ext == '.pcm':
                content_type = response.headers.get('content-type', '')
                rate = re.search(r'rate=(\d+)', content_type)
                channels = re.search(r'channels=(\d+)', content_type)
                rate = rate.group(1) if rate else '24000'
                channels = channels.group(1) if channels else '1'
                path = path.with_suffix('.ogg')
                proc = await asyncio.create_subprocess_exec('ffmpeg', '-hide_banner', '-loglevel', 'error', '-f', 's16le', '-ar', rate, '-ac', channels, '-i', 'pipe:0', '-c:a', 'libopus', '-b:a', '64k', '-y', str(path), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
                _, stderr = await proc.communicate(content)
                if proc.returncode:
                    raise ValueError('Не удалось подготовить голосовое сообщение')
            else:
                path.write_bytes(content)
            files.append(str(path.relative_to(root)))
            result = {'files': files, 'generation_id': response.headers.get('x-generation-id')}
            await response.aclose()
        else:
            result = dump(response)
            result = store_media(result, root, ident, files, [0])
            if files:
                result['files'] = files
        generation = result.get('id') or result.get('generation_id')
        with ledger() as db:
            if call_id:
                db.execute('UPDATE calls SET status=?,cost=?,generation=? WHERE id=?', ('completed', (result.get('usage') or {}).get('cost'), generation, call_id))
            db.execute('UPDATE sdk_jobs SET remote_id=? WHERE request=?', (generation, ident))
        return result
    except Exception:
        if call_id:
            with ledger() as db:
                db.execute('UPDATE calls SET status=? WHERE id=?', ('failed', call_id))
        raise


async def process(sdk, pending, semaphore):
    root = pending.parent.parent
    ident = pending.stem
    if not root.name.replace('-', '').replace('_', '').isdigit() or not root.is_relative_to(ROOT):
        return
    if pending.is_symlink() or pending.parent.is_symlink() or root.is_symlink():
        return
    work = pending.with_suffix('.working')
    try:
        pending.rename(work)
    except (FileNotFoundError, PermissionError):
        return
    try:
        async with semaphore:
            if work.stat().st_size > 20 * 1024 * 1024:
                raise ValueError('Слишком большой запрос')
            data = json.loads(work.read_text())
            with ledger() as db:
                if db.execute('SELECT 1 FROM sdk_jobs WHERE request=?', (ident,)).fetchone():
                    raise ValueError('Запрос уже обработан')
                db.execute('INSERT INTO sdk_jobs VALUES(?,?,?,?,?,?)', (str(root), ident, data['operation'], 'running', None, None))
            result = await execute(sdk, data['operation'], data.get('arguments', {}), root, ident)
            payload = {'ok': True, 'result': result}
    except Exception as exc:
        # Never serialize SDK request objects or headers containing credentials.
        status = getattr(exc, 'status_code', None)
        detail = str(exc) if isinstance(exc, (ValueError, TypeError, AttributeError)) else type(exc).__name__ + (f' HTTP {status}' if status else '')
        api_message = getattr(exc, 'message', None)
        if isinstance(api_message, str):
            detail += ': ' + api_message[:1000]
        response = getattr(exc, 'raw_response', None)
        if isinstance(response, httpx.Response):
            try:
                error = response.json().get('error', {})
                if isinstance(error, dict) and isinstance(error.get('message'), str):
                    detail += ': ' + error['message'][:1000]
            except Exception:
                pass
        secret = os.environ.get('OPENROUTER_API_KEY')
        if secret:
            detail = detail.replace(secret, '[redacted]')
        payload = {'ok': False, 'error': detail[:1500]}
        logging.warning('OpenRouter operation failed: %s', type(exc).__name__)
    with ledger() as db:
        db.execute('UPDATE sdk_jobs SET status=?,result=? WHERE request=?', ('completed' if payload['ok'] else 'failed', json.dumps(payload), ident))
    temp = work.with_suffix('.result.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False))
    temp.replace(work.with_suffix('.result'))
    work.rename(work.with_suffix('.done'))


async def main():
    os.umask(0o077)
    for line in Path('/opt/jarvis/.env').read_text().splitlines():
        if line.startswith('OPENROUTER_') and '=' in line:
            key, value = line.split('=', 1)
            os.environ[key] = value
    # An interrupted paid request is never automatically repeated.
    for work in ROOT.glob('*/.openrouter/*.working'):
        work.with_suffix('.result').write_text(json.dumps({'ok': False, 'error': 'Сервис перезапустился во время запроса. Повторная генерация автоматически не запущена.'}))
        work.rename(work.with_suffix('.interrupted'))
    tasks = set()
    semaphore = asyncio.Semaphore(4)
    async with OpenRouter(api_key=os.environ['OPENROUTER_API_KEY']) as sdk:
        while True:
            for pending in ROOT.glob('*/.openrouter/*.pending'):
                task = asyncio.create_task(process(sdk, pending, semaphore))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
            await asyncio.sleep(.25)


if __name__ == '__main__':
    asyncio.run(main())
