import os
import sys
from pathlib import Path

root = Path('/opt/jarvis/.venv/lib/python3.14/site-packages/nvidia')
libs = [str(root/'cublas/lib'), str(root/'cudnn/lib'), '/usr/lib/wsl/lib']
os.environ['LD_LIBRARY_PATH'] = ':'.join(libs)
os.environ.setdefault('ASR_MODEL', 'small')
os.execv(sys.executable,[sys.executable,'-m','uvicorn','asr:app','--host','127.0.0.1','--port','8171','--app-dir','/opt/jarvis/deploy'])
