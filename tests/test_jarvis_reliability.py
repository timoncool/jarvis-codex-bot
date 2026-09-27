import json
import os
import subprocess
import sys
from types import SimpleNamespace

import httpx
import pytest

from takopi import jarvis_jobs as jobs
from takopi.progress import ProgressTracker
from takopi.telegram.bridge import TelegramPresenter
from takopi.telegram.client_api import HttpBotClient
from takopi.telegram.types import TelegramIncomingMessage
from takopi.transport import RenderedMessage
from takopi.jarvis import deliver_outputs


def message():
    return TelegramIncomingMessage(transport='telegram',chat_id=123,message_id=7,text='Ответь голосом',reply_to_message_id=None,reply_to_text=None,sender_id=123,chat_type='private')


def test_task_survives_process_crash(tmp_path, monkeypatch):
    monkeypatch.setenv('JARVIS_DATA',str(tmp_path))
    assert jobs.accept(message())
    assert not jobs.accept(message())
    code = "from takopi import jarvis_jobs as j; from takopi.transport import RenderedMessage; import os; j.begin(123,7,{}); j.progress(123,7,42); j.save_result(123,7,RenderedMessage(text='Готово')); os._exit(9)"
    result=subprocess.run([sys.executable,'-c',code],env=os.environ.copy())
    assert result.returncode == 9
    row=jobs.get(123,7)
    assert row['state']=='ready' and row['progress']==42
    assert jobs.saved_render(row).text=='Готово'
    assert len(list(jobs.unfinished()))==1
    jobs.state(123,7,'done')
    assert list(jobs.unfinished())==[]
    assert not jobs.accept(message())


def test_progress_hides_commands_context_and_engine(monkeypatch,tmp_path):
    monkeypatch.setenv('JARVIS_DATA',str(tmp_path))
    state=ProgressTracker(engine='codex').snapshot(context_line='ctx: secret')
    presenter=TelegramPresenter(message_overflow='split')
    text=presenter.render_progress(state,elapsed_s=2).text
    assert text=='Думаю над запросом…'
    final=presenter.render_final(state,elapsed_s=3,status='done',answer='Ответ пользователю')
    assert final.text=='Ответ пользователю'
    assert 'ctx' not in final.text and 'codex' not in final.text


@pytest.mark.anyio
async def test_unchanged_edit_is_success_not_replacement():
    requests=[]
    def handle(request):
        requests.append(request)
        return httpx.Response(400,json={'ok':False,'description':'Bad Request: message is not modified'},request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        api=HttpBotClient('test-token',http_client=client)
        result=await api.edit_message_text(chat_id=123,message_id=42,text='Готовлю ответ…')
    assert result is not None and result.message_id==42
    assert result.chat.type=='private'
    assert len(requests)==1


@pytest.mark.anyio
async def test_native_voice_delivery_not_repeated_after_restart(tmp_path,monkeypatch):
    monkeypatch.setenv('JARVIS_DATA',str(tmp_path))
    jobs.accept(message())
    out=tmp_path/'output';out.mkdir()
    (out/'speech.ogg').write_bytes(b'ogg-test')
    calls=[]
    class API:
        async def _request(self,method,**kwargs):
            calls.append(method)
            return {'message_id':99}
    bot=SimpleNamespace(_client=API())
    cfg=SimpleNamespace(transport=SimpleNamespace(_bot=bot))
    await deliver_outputs(cfg,tmp_path,{},123,None,7)
    await deliver_outputs(cfg,tmp_path,{},123,None,7)
    assert calls==['sendVoice']


@pytest.mark.anyio
async def test_uncertain_delivery_does_not_send_again(tmp_path,monkeypatch):
    monkeypatch.setenv('JARVIS_DATA',str(tmp_path))
    jobs.accept(message())
    out=tmp_path/'output';out.mkdir()
    p=out/'speech.ogg';p.write_bytes(b'ogg-test')
    jobs.delivery(123,7,p,(p.stat().st_mtime_ns,p.stat().st_size),'sending')
    notes=[]
    class Bot:
        async def send_message(self,**kwargs): notes.append(kwargs['text'])
    await deliver_outputs(SimpleNamespace(transport=SimpleNamespace(_bot=Bot())),tmp_path,{},123,None,7)
    assert len(notes)==1
