import os
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, UploadFile, File, HTTPException
from faster_whisper import WhisperModel

app = FastAPI()
lock = threading.Lock()
device = os.environ.get('ASR_DEVICE', 'cpu')
model = WhisperModel(os.environ.get('ASR_MODEL', 'small'), device=device, compute_type=os.environ.get('ASR_COMPUTE_TYPE', 'int8' if device == 'cpu' else 'float16'))


@app.get('/health')
def health():
    return {'status':'ok','device':device,'model':os.environ.get('ASR_MODEL', 'small')}


@app.post('/v1/audio/transcriptions')
def transcribe(file: UploadFile = File(...)):
    with tempfile.NamedTemporaryFile(suffix='.audio') as tmp:
        size = 0
        while chunk := file.file.read(1024*1024):
            size += len(chunk)
            if size > 500*1024*1024:
                raise HTTPException(413,'Audio exceeds 500 MB')
            tmp.write(chunk)
        tmp.flush()
        with lock:
            segments, info = model.transcribe(tmp.name, language='ru', vad_filter=True, beam_size=5)
            text = ''.join(segment.text for segment in segments).strip()
    return {'text':text, 'language':info.language}
