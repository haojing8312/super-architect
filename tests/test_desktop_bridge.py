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
