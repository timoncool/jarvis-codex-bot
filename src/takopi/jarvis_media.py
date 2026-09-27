import base64
import json
import mimetypes
import os
import re
import sqlite3
import time
from pathlib import Path

import httpx


async def generate(kind,prompt,folder,reference=None):
    db=sqlite3.connect('/opt/jarvis/data/media.sqlite',timeout=15)
    db.execute('CREATE TABLE IF NOT EXISTS calls(id INTEGER PRIMARY KEY,kind TEXT,created REAL,cost REAL,status TEXT,generation TEXT)')
    db.execute('BEGIN IMMEDIATE')
    count=db.execute('SELECT count(*) FROM calls').fetchone()[0]
    if count>=20:
        db.rollback()
        raise RuntimeError('Достигнут лимит 20 вызовов демонстрации. Владелец может увеличить лимит.')
    call_id=db.execute('INSERT INTO calls(kind,created,status) VALUES(?,?,?)',(kind,time.time(),'started')).lastrowid
    db.commit()
    headers={'Authorization':'Bearer '+os.environ['OPENROUTER_API_KEY']}
    folder=Path(folder)
    folder.mkdir(parents=True,exist_ok=True)
    try:
        async with httpx.AsyncClient(timeout=240) as client:
            if kind=='image':
                payload={'model':os.environ['OPENROUTER_IMAGE_MODEL'],'prompt':prompt,'n':1,'provider':{'allow_fallbacks':False}}
                if reference:
                    ref=Path(reference)
                    mime=mimetypes.guess_type(ref.name)[0] or 'image/png'
                    payload['input_references']=[{'type':'image_url','image_url':{'url':'data:'+mime+';base64,'+base64.b64encode(ref.read_bytes()).decode()}}]
                r=await client.post('https://openrouter.ai/api/v1/images',headers=headers,json=payload)
                if r.status_code!=200:
                    raise RuntimeError(f'OpenRouter image: HTTP {r.status_code}: '+r.text[:350])
                data=r.json()
                item=data['data'][0]
                ext={'image/png':'.png','image/jpeg':'.jpg','image/webp':'.webp'}.get(item.get('media_type'),'.png')
                path=folder/f'image-{call_id}{ext}'
                path.write_bytes(base64.b64decode(item['b64_json']))
                cost=data.get('usage',{}).get('cost')
                generation=data.get('id')
            else:
                payload={'model':os.environ['OPENROUTER_TTS_MODEL'],'input':prompt,'voice':os.environ['OPENROUTER_TTS_VOICE'],'response_format':'mp3','provider':{'allow_fallbacks':False}}
                r=await client.post('https://openrouter.ai/api/v1/audio/speech',headers=headers,json=payload)
                if r.status_code!=200 or not r.headers.get('content-type','').startswith('audio/'):
                    raise RuntimeError(f'OpenRouter TTS: HTTP {r.status_code}: '+r.text[:350])
                path=folder/f'speech-{call_id}.mp3'
                path.write_bytes(r.content)
                cost=None
                generation=r.headers.get('x-generation-id')
            db.execute('UPDATE calls SET cost=?,status=?,generation=? WHERE id=?',(cost,'completed',generation,call_id))
            db.commit()
            return path
    except Exception:
        db.execute('UPDATE calls SET status=? WHERE id=?',('failed',call_id))
        db.commit()
        raise
    finally:
        db.close()


async def maybe_media(cfg,msg,path):
    text=msg.text.strip()
    lowered=text.lower()
    kind=None
    if re.match(r'^(нарисуй|сгенерируй\s+(картинку|изображение|фото)|создай\s+(картинку|изображение|иллюстрацию)|/image\b)',lowered): kind='image'
    if re.match(r'^(озвучь|прочитай вслух|/tts\b)',lowered): kind='tts'
    if kind is None: return False
    progress=await cfg.bot.send_message(chat_id=msg.chat_id,message_thread_id=msg.thread_id,text='Создаю изображение…' if kind=='image' else 'Готовлю аудио…')
    try:
        result=await generate(kind,text,path/'output')
        sent=await cfg.bot.send_document(chat_id=msg.chat_id,message_thread_id=msg.thread_id,filename=result.name,content=result.read_bytes(),caption='Готово. Можно попросить изменить результат.')
        if sent is None: raise RuntimeError('Telegram не принял результат')
        if progress: await cfg.bot.delete_message(chat_id=msg.chat_id,message_id=progress.message_id)
    except Exception as exc:
        message='Не удалось завершить генерацию. '+str(exc)
        if progress: await cfg.bot.edit_message_text(chat_id=msg.chat_id,message_id=progress.message_id,text=message[:3500])
        else: await cfg.bot.send_message(chat_id=msg.chat_id,message_thread_id=msg.thread_id,text=message[:3500])
    return True
