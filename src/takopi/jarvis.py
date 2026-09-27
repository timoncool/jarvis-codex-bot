from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

from takopi.config import ProjectConfig
from takopi.context import RunContext
from takopi.model import ResumeToken
from takopi.telegram.commands.reply import make_reply
from takopi.telegram.engine_overrides import EngineOverrides

HELP = '''Я работаю через твой Codex. Одна тема — отдельный разговор и папка.
Присылай текст, фото, документы или голосовое. Попроси создать Word, Excel, презентацию, PDF, схему или HTML. Готовые файлы пришлю в эту тему.

/cancel — остановить текущую задачу
/clear — новый разговор, память остаётся
/compact — сжать историю
/name имя — назвать разговор
/resume — вернуться к сохранённому разговору
/model — доступные модели Codex
/mode — сразу делать или сначала план
/language — язык ответов
/weekpassed — вспомнить итоги
/memory — показать память
/help — эта подсказка

«Запомни: …», «забудь …» и «сохрани файл …» можно писать обычными словами.'''


def data_root() -> Path:
    return Path(os.environ['JARVIS_DATA']).resolve()


def topic_dir(chat: int, thread: int | None) -> Path:
    return data_root() / 'topics' / f'{int(chat)}_{int(thread or 0)}'


def db():
    conn = sqlite3.connect(data_root() / 'jarvis.sqlite', timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript('''
        CREATE TABLE IF NOT EXISTS conversations (
          chat INTEGER, topic INTEGER, session TEXT, name TEXT, updated REAL,
          PRIMARY KEY(chat,topic,session));
        CREATE TABLE IF NOT EXISTS preferences (
          chat INTEGER, topic INTEGER, key TEXT, value TEXT,
          PRIMARY KEY(chat,topic,key));
    ''')
    return conn


def preference(chat, topic, key, value=None):
    with db() as conn:
        if value is not None:
            conn.execute('INSERT OR REPLACE INTO preferences VALUES(?,?,?,?)',
                         (chat, topic or 0, key, value))
        row = conn.execute('SELECT value FROM preferences WHERE chat=? AND topic=? AND key=?',
                           (chat, topic or 0, key)).fetchone()
        return row['value'] if row else None


async def bind_topic(cfg, msg, store):
    if not os.environ.get('JARVIS_DATA'):
        return
    path = topic_dir(msg.chat_id, msg.thread_id)
    path.mkdir(parents=True, exist_ok=True)
    for name in ('incoming', 'output', 'saved', 'sessions'):
        (path / name).mkdir(exist_ok=True)
    if not (path / 'memory.md').exists():
        (path / 'memory.md').write_text('# Память этой темы\n', encoding='utf-8')
    user_dir = data_root() / 'users' / str(msg.sender_id)
    user_dir.mkdir(parents=True, exist_ok=True)
    memory = user_dir / 'memory.md'
    if not memory.exists():
        memory.write_text('# Память пользователя\n', encoding='utf-8')
    (path / 'user-memory.md').write_text(memory.read_text(), encoding='utf-8')
    rules = Path('/opt/jarvis/deploy/TOPIC.md').read_text()
    lang = preference(msg.chat_id, msg.thread_id, 'language') or 'русский'
    mode = preference(msg.chat_id, msg.thread_id, 'mode') or 'act'
    rules += f'\nЯзык ответа: {lang}.\n'
    if mode == 'plan':
        rules += 'Сначала предложи план и дождись явного подтверждения пользователя до исполнения.\n'
    (path / 'AGENTS.md').write_text(rules, encoding='utf-8')
    alias = f't{abs(msg.chat_id)}x{msg.thread_id or 0}'
    cfg.runtime._projects.projects[alias] = ProjectConfig(
        alias=alias, path=path, worktrees_dir=Path('.worktrees'), default_engine='codex')
    context = RunContext(project=alias, branch=None)
    if store is not None and msg.thread_id is not None:
        await store.set_context(msg.chat_id, msg.thread_id, context)
    return context


def sandbox_policy(cwd):
    path = Path(cwd).resolve()
    root = data_root() / 'topics'
    if not path.is_relative_to(root) or path == root:
        raise ValueError('Codex workspace must be a topic directory')
    return {
        'type': 'workspaceWrite', 'writableRoots': [str(path)],
        'readOnlyAccess': {'type': 'restricted', 'includePlatformDefaults': True,
                           'readableRoots': [str(path), '/opt/jarvis/.venv']},
        'networkAccess': True, 'excludeTmpdirEnvVar': True, 'excludeSlashTmp': True,
    }


def active_session(cwd):
    chat, topic = Path(cwd).name.split('_')
    value = preference(int(chat), int(topic), 'session')
    return ResumeToken(engine='codex', value=value) if value else None


def remember_session(cwd, session):
    chat, topic = Path(cwd).name.split('_')
    preference(int(chat), int(topic), 'session', session)


def model_override(cwd):
    chat, topic = Path(cwd).name.split('_')
    return preference(int(chat), int(topic), 'model')


async def client_for(cfg):
    runner = cfg.runtime.resolve_runner(resume_token=None, engine_override='codex').runner
    await runner._client.start()
    return runner._client


async def preprocess(cfg, msg, store):
    if not os.environ.get('JARVIS_DATA'):
        return msg
    reply = make_reply(cfg, msg)
    text = msg.text.strip()
    cmd, _, args = text.partition(' ')
    cmd = cmd.lower().split('@')[0]
    if msg.chat_id < 0 and msg.thread_id in (None, 1):
        if cmd == '/help':
            await reply(text=HELP)
        return None
    await bind_topic(cfg, msg, store)
    topic = msg.thread_id or 0
    path = topic_dir(msg.chat_id, topic)
    token = active_session(path)
    if token:
        with db() as conn:
            conn.execute('INSERT INTO conversations VALUES(?,?,?,?,?) ON CONFLICT(chat,topic,session) DO UPDATE SET updated=excluded.updated',
                         (msg.chat_id, topic, token.value, 'Разговор', time.time()))
    if cmd in ('/start', '/help'):
        from takopi.jarvis_ui import welcome
        await welcome(cfg,msg)
        return None
    if cmd in ('/kill', '/stop'):
        return replace(msg, text='/cancel')
    if cmd in ('/clear', '/new'):
        preference(msg.chat_id, topic, 'session', '')
        await reply(text='Следующее сообщение начнёт новый разговор. Память и файлы сохранены.')
        return None
    if cmd == '/name':
        if not token or not args.strip():
            await reply(text='Сначала начни разговор, затем /name имя.')
        else:
            with db() as conn:
                conn.execute('UPDATE conversations SET name=? WHERE chat=? AND topic=? AND session=?',
                             (args.strip()[:100], msg.chat_id, topic, token.value))
            await reply(text='Разговор назван: ' + args.strip()[:100])
        return None
    if cmd == '/resume':
        with db() as conn:
            rows = conn.execute('SELECT session,name FROM conversations WHERE chat=? AND topic=? ORDER BY updated DESC',
                                (msg.chat_id, topic)).fetchall()
        if args.isdigit() and 1 <= int(args) <= len(rows):
            row = rows[int(args)-1]
            preference(msg.chat_id, topic, 'session', row['session'])
            await reply(text='Продолжаем: ' + row['name'])
        elif rows:
            await reply(text='Разговоры этой темы:\n' + '\n'.join(f"/resume {i} — {r['name']}" for i,r in enumerate(rows,1)))
        else:
            await reply(text='Сохранённых разговоров пока нет.')
        return None
    if cmd == '/compact':
        if not token:
            await reply(text='Разговор ещё не начат.')
        else:
            client = await client_for(cfg)
            await client.ensure_thread_loaded(token.value)
            await client.request('thread/compact/start', {'threadId': token.value})
            await reply(text='Codex начал сжатие истории; память темы сохранена.')
        return None
    if cmd == '/model':
        client = await client_for(cfg)
        result = await client.request('model/list', {'limit': 100})
        models = result['data']
        if args:
            if args not in {m['model'] for m in models}:
                await reply(text='Модель отсутствует в каталоге твоего Codex. Введи /model.')
            else:
                preference(msg.chat_id, topic, 'model', args)
                await reply(text='Модель этой темы: ' + args)
        else:
            await reply(text='Доступные модели:\n' + '\n'.join('/model ' + m['model'] for m in models))
        return None
    if cmd == '/mode':
        if args in ('act','plan'):
            preference(msg.chat_id, topic, 'mode', args)
            await reply(text='Режим: ' + ('сначала план и подтверждение' if args == 'plan' else 'сразу выполнять'))
        else:
            await reply(text='/mode act — сразу выполнять\n/mode plan — сначала план и подтверждение')
        return None
    if cmd == '/language':
        if args:
            preference(msg.chat_id, topic, 'language', args[:60])
            await reply(text='Язык ответа: ' + args[:60])
        else:
            await reply(text='Укажи язык: /language русский')
        return None
    if cmd in ('/memory',) or text.lower() == 'пришли память':
        await reply(text=(path/'memory.md').read_text() + '\n' + (path/'user-memory.md').read_text())
        return None
    if text.lower().startswith('запомни обо мне:'):
        note = text.split(':',1)[1].strip()
        memory = data_root()/'users'/str(msg.sender_id)/'memory.md'
        with memory.open('a') as f:
            f.write('\n- ' + note + '\n')
        await reply(text='Записал в общую память: «' + note + '»')
        return None
    if cmd == '/weekpassed':
        return replace(msg, text='Прочитай память и историю этой темы. Кратко: что сделано, что зависло, следующие действия, что стоит перепроверить.')
    if cmd == '/finish':
        return replace(msg, text='Заверши разговор: подведи итоги и сохрани важные решения в memory.md. После этого сообщи, что разговор завершён.')
    if cmd == '/showmethemoney':
        await reply(text='Основной помощник работает через подписку ChatGPT. Генерация медиа и дополнительные модели используют OpenRouter; расход учитывается отдельно.')
        return None
    if cmd == '/playbook':
        await reply(text='Codex и Telegram настраиваются локально. Google, публикация сайтов и подключение Windows ещё не настроены; это отмечено в отчёте развёртывания.')
        return None
    if cmd in ('/agent','/ctx','/topic'):
        await reply(text='Здесь используется Codex и закреплённая папка текущей темы.')
        return None
    return msg


def output_snapshot(cwd):
    if not os.environ.get('JARVIS_DATA') or cwd is None:
        return {}
    root = Path(cwd)/'output'
    return {str(p): (p.stat().st_mtime_ns,p.stat().st_size) for p in root.rglob('*') if p.is_file() and not p.is_symlink()}


async def deliver_outputs(exec_cfg, cwd, before, chat, thread, task_id=None):
    if not os.environ.get('JARVIS_DATA') or cwd is None:
        return
    current = output_snapshot(cwd)
    bot = exec_cfg.transport._bot
    for filename, stamp in current.items():
        if before.get(filename) == stamp:
            continue
        path = Path(filename)
        if not path.resolve().is_relative_to(Path(cwd).resolve()/'output'):
            raise ValueError('Output path escaped topic')
        if stamp[1] > 50*1024*1024:
            await bot.send_message(chat, 'Файл больше 50 МБ. Могу сжать его или разделить на части.', message_thread_id=thread)
            continue
        from takopi import jarvis_jobs as jobs
        delivery_state = jobs.delivery(chat,task_id,path,stamp)
        if delivery_state == 'sent':
            continue
        if delivery_state == 'sending':
            await bot.send_message(chat_id=chat, message_thread_id=thread, text='Файл готов и сохранён. Связь оборвалась во время отправки: если файл не появился, напиши «пришли готовый файл ещё раз».')
            jobs.delivery(chat,task_id,path,stamp,'uncertain')
            continue
        jobs.delivery(chat,task_id,path,stamp,'sending')
        suffix = path.suffix.lower()
        method, field = ('sendPhoto', 'photo') if suffix in ('.jpg', '.jpeg', '.png', '.webp') and stamp[1] <= 10*1024*1024 else (('sendVoice', 'voice') if suffix in ('.mp3', '.ogg', '.m4a') else (('sendVideo', 'video') if suffix == '.mp4' else ('sendDocument', 'document')))
        api = getattr(bot, '_client', bot)
        if hasattr(api, '_request'):
            payload = {'chat_id': str(chat)}
            if thread is not None:
                payload['message_thread_id'] = str(thread)
            sent = await api._request(method, data=payload, files={field: (path.name, path.read_bytes())})
            if sent is None and method != 'sendDocument':
                sent = await bot.send_document(chat_id=chat, filename=path.name, content=path.read_bytes(), message_thread_id=thread)
        else:
            sent = await bot.send_document(chat_id=chat, filename=path.name, content=path.read_bytes(), message_thread_id=thread)
        if sent is None:
            jobs.delivery(chat,task_id,path,stamp,'failed')
            raise RuntimeError('Не удалось доставить файл в Telegram')
        sent_id = sent.get('message_id') if isinstance(sent,dict) else sent.message_id
        jobs.delivery(chat,task_id,path,stamp,'sent',sent_id)
