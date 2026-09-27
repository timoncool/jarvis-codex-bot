"""Durable Telegram task journal and recovery; no credentials in task payloads."""
import json
import os
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path

TERMINAL = ('done', 'failed', 'cancelled')


def enabled():
    return bool(os.environ.get('JARVIS_DATA'))


def connection():
    c = sqlite3.connect(Path(os.environ['JARVIS_DATA']) / 'tasks.sqlite', timeout=15)
    c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    c.execute('PRAGMA synchronous=FULL')
    c.executescript('''
    CREATE TABLE IF NOT EXISTS tasks(chat INTEGER,message INTEGER,payload TEXT,state TEXT,progress INTEGER,result TEXT,snapshot TEXT,attempts INTEGER DEFAULT 0,updated REAL,PRIMARY KEY(chat,message));
    CREATE TABLE IF NOT EXISTS deliveries(chat INTEGER,message INTEGER,path TEXT,stamp TEXT,state TEXT,telegram_id INTEGER,PRIMARY KEY(chat,message,path,stamp));
    ''')
    return c


def accept(msg):
    if not enabled() or not hasattr(msg, 'text') or msg.text.startswith('/'):
        return True
    if msg.chat_id < 0 and msg.thread_id in (None, 1):
        return True
    with connection() as c:
        row = c.execute('SELECT state FROM tasks WHERE chat=? AND message=?', (msg.chat_id,msg.message_id)).fetchone()
        if row:
            return False
        c.execute('INSERT INTO tasks(chat,message,payload,state,updated) VALUES(?,?,?,?,?)', (msg.chat_id,msg.message_id,json.dumps(asdict(msg),ensure_ascii=False),'queued',time.time()))
    return True


def unfinished():
    from takopi.telegram.types import TelegramIncomingMessage, TelegramVoice, TelegramDocument
    with connection() as c:
        rows=c.execute("SELECT payload FROM tasks WHERE state NOT IN ('done','failed','cancelled') ORDER BY updated").fetchall()
    for row in rows:
        data=json.loads(row['payload'])
        if data.get('voice'): data['voice']=TelegramVoice(**data['voice'])
        if data.get('document'): data['document']=TelegramDocument(**data['document'])
        yield TelegramIncomingMessage(**data)


def get(chat,message):
    if not enabled(): return None
    with connection() as c:
        row=c.execute('SELECT * FROM tasks WHERE chat=? AND message=?',(chat,message)).fetchone()
        return dict(row) if row else None


def state(chat,message,value):
    if not enabled(): return
    with connection() as c:
        c.execute('UPDATE tasks SET state=?,updated=? WHERE chat=? AND message=?',(value,time.time(),chat,message))


def begin(chat,message,snapshot):
    row=get(chat,message)
    if not row: return snapshot,False
    old=json.loads(row['snapshot']) if row['snapshot'] else snapshot
    with connection() as c:
        c.execute('UPDATE tasks SET snapshot=?,state=?,attempts=attempts+1,updated=? WHERE chat=? AND message=?',(json.dumps(old),'running',time.time(),chat,message))
    return {k:tuple(v) for k,v in old.items()},row['attempts']>0


def progress(chat,message,ident):
    if not enabled(): return
    with connection() as c:
        c.execute('UPDATE tasks SET progress=?,updated=? WHERE chat=? AND message=?',(ident,time.time(),chat,message))


def save_result(chat,message,rendered):
    if not enabled(): return
    with connection() as c:
        c.execute('UPDATE tasks SET result=?,state=?,updated=? WHERE chat=? AND message=?',(json.dumps(asdict(rendered),ensure_ascii=False),'ready',time.time(),chat,message))


def saved_render(row):
    from takopi.transport import RenderedMessage
    data=json.loads(row['result'])
    if 'followups' in data.get('extra',{}):
        data['extra']['followups']=[RenderedMessage(**m) for m in data['extra']['followups']]
    return RenderedMessage(**data)


def delivery(chat,message,path,stamp,status=None,telegram_id=None):
    if not enabled() or message is None: return None
    key=(chat,message,str(path),json.dumps(stamp))
    with connection() as c:
        if status:
            c.execute('INSERT INTO deliveries VALUES(?,?,?,?,?,?) ON CONFLICT(chat,message,path,stamp) DO UPDATE SET state=excluded.state,telegram_id=excluded.telegram_id',(*key,status,telegram_id))
        row=c.execute('SELECT state FROM deliveries WHERE chat=? AND message=? AND path=? AND stamp=?',key).fetchone()
    return row['state'] if row else None
