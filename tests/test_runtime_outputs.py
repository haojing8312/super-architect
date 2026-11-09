import time
import os
import subprocess
import pytest
from pathlib import Path
from PIL import Image
from architecture_workbench.runtime_outputs import collect_codex_images


def test_only_current_runtime_thread_images_collected(tmp_path):
    identifier='01234567-89ab-cdef-0123-456789abcdef'
    root=tmp_path/'generated_images';folder=root/identifier;folder.mkdir(parents=True)
    workspace=tmp_path/'workspace';workspace.mkdir()
    started=time.time()
    Image.new('RGB',(8,8),'white').save(folder/'owned.png')
    other=root/'another-thread';other.mkdir();Image.new('RGB',(8,8),'black').save(other/'unrelated.png')
    result=collect_codex_images(identifier,str(workspace),started,roots=[root])
    assert len(result)==1
    assert Path(result[0]['path']).is_relative_to(workspace)
    assert (folder/'owned.png').exists()
    assert collect_codex_images('../another-thread',str(workspace),started,roots=[root])==[]
    assert collect_codex_images(identifier,str(workspace),started+3600,roots=[root])==[]


@pytest.mark.skipif(os.name != 'nt', reason='Windows junction semantics')
def test_thread_junction_and_output_junction_rejected(tmp_path):
    root=tmp_path/'images';root.mkdir();other=root/'other';other.mkdir()
    Image.new('RGB',(8,8),'white').save(other/'image.png')
    thread='01234567-89ab-cdef-0123-456789abcdef'
    linked=root/thread
    subprocess.run(['cmd','/c','mklink','/J',str(linked),str(other)],check=True,capture_output=True)
    workspace=tmp_path/'workspace';workspace.mkdir()
    assert collect_codex_images(thread,str(workspace),time.time(),roots=[root])==[]
    destination=workspace/'outputs'
    subprocess.run(['cmd','/c','mklink','/J',str(destination),str(other)],check=True,capture_output=True)
    with pytest.raises(ValueError,match='越界链接'):
        collect_codex_images(thread,str(workspace),time.time(),roots=[root])
