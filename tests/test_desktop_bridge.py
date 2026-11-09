from pathlib import Path
import pytest
from architecture_workbench.desktop import DesktopApi


class Window:
    def __init__(self,url): self.url=url
    def get_current_url(self): return self.url
    def destroy(self): self.destroyed=True


def test_native_bridge_rejects_foreign_navigation_and_executable_links(tmp_path):
    api=DesktopApi('http://127.0.0.1:33821')
    api._window=Window('https://external.example/')
    with pytest.raises(ValueError,match='权限'):
        api.pick_folder()
    api._window.url='http://127.0.0.1:33822'
    with pytest.raises(ValueError): api.open_path(str(tmp_path))
    api._window.url='http://127.0.0.1:33821/'
    for suffix in ('.lnk','.url','.hta','.wsf','.exe','.cmd'):
        path=tmp_path/('blocked'+suffix);path.write_text('test')
        with pytest.raises(ValueError,match='仅直接打开'): api.open_path(str(path))

@pytest.mark.parametrize('ready',[True,False])
def test_update_bridge_waits_for_matching_window_receipt(monkeypatch,ready):
    import httpx
    api=DesktopApi('http://127.0.0.1:33821');api._window=Window(api._origin)
    class Client:
        def __init__(self,**kwargs):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def get(self,path):return httpx.Response(200,json={'token':'controlled-local-capability'})
        def post(self,path,**kwargs):
            assert api.update_intent and kwargs['json']=={'version':'9.0.0'}
            return httpx.Response(200,json={'ready':ready,'version':'9.0.0'})
    monkeypatch.setattr('architecture_workbench.desktop.httpx.Client',Client)
    if ready:
        assert api.install_update('9.0.0')['ready']
        assert api.force_exit and api._window.destroyed
    else:
        with pytest.raises(ValueError,match='保持打开'):api.install_update('9.0.0')
        assert not api.force_exit and not hasattr(api._window,'destroyed')
    assert not api.update_intent
