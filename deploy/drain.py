"""ExecStop: stop intake, then let accepted tasks finish before stopping the bot."""
import os
import sqlite3
import time
from pathlib import Path

root=Path('/opt/jarvis/data')
(root/'draining').touch()
deadline=time.monotonic()+540
while time.monotonic()<deadline:
    try:
        with sqlite3.connect(root/'tasks.sqlite') as c:
            active=c.execute("SELECT COUNT(*) FROM tasks WHERE state IN ('running','ready','delivering')").fetchone()[0]
    except sqlite3.OperationalError:
        active=0
    if not active: break
    time.sleep(.5)
