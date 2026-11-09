from pathlib import Path
import zipfile
import pytest
from architecture_workbench.remote import admin_base, extract_skill
from architecture_workbench.remote import RemoteClient
from architecture_workbench.store import Store
import asyncio
import httpx


def test_remote_url_and_skill_archive_boundary(tmp_path):
    assert admin_base({'admin_url':'http://127.0.0.1:8888/'}) == 'http://127.0.0.1:8888'
    assert admin_base({'admin_url':'https://example.invalid'}) == 'https://example.invalid'
    for url in ['http://example.invalid','https://user:secret@example.invalid','https://example.invalid?secret=1']:
        with pytest.raises(ValueError):
            admin_base({'admin_url':url})
    good=tmp_path/'good.zip'
    with zipfile.ZipFile(good,'w') as archive:
        archive.writestr('demo/SKILL.md','---\nname: demo\n---\nContent')
        archive.writestr('demo/LICENSE','MIT')
    out=tmp_path/'out'; out.mkdir()
    root=extract_skill(good,out,'demo')
    assert (root/'LICENSE').read_text() == 'MIT'
    bad=tmp_path/'bad.zip'
    with zipfile.ZipFile(bad,'w') as archive:
        archive.writestr('../outside.txt','bad')
    with pytest.raises(ValueError):
        extract_skill(bad,out,'unsafe')
    assert not (tmp_path/'outside.txt').exists()


def test_member_credentials_bound_to_original_server(tmp_path, monkeypatch):
    store=Store(tmp_path)
    store.save_settings({'admin_url':'https://original.example'})
    service=RemoteClient(store)
    service.session=lambda:{'base':'https://original.example','access_token':'access-test','refresh_token':'refresh-test'}
    service.save=lambda _value:None
    calls=[]
    original_client=httpx.AsyncClient
    def reply(request):
        calls.append(str(request.url))
        store.save_settings({'admin_url':'https://different.example'})
        return httpx.Response(401,json={'code':401})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original_client(transport=httpx.MockTransport(reply),**kwargs))
    with pytest.raises(ValueError,match='地址已改变'):
        asyncio.run(service.me())
    assert len(calls)==1 and 'refresh-test' not in calls[0]
    calls.clear()
    with pytest.raises(ValueError,match='地址已改变'):
        asyncio.run(service.me())
    assert not calls
