import pytest
from architecture_workbench.window_bounds import maximize_geometry

@pytest.mark.parametrize('monitor,work,expected', [
    ((0,0,1920,1080),(0,0,1920,1032),(0,0,1920,1032)),
    ((0,0,1920,1080),(60,0,1920,1080),(60,0,1860,1080)),
    ((0,0,1920,1080),(0,48,1920,1080),(0,48,1920,1032)),
    ((-1920,-200,0,880),(-1920,-200,0,832),(0,0,1920,1032)),
    ((1920,0,4480,1440),(1920,0,4480,1380),(0,0,2560,1380)),
])
def test_maximize_respects_monitor_work_area(monitor,work,expected):
    assert maximize_geometry(monitor,work)==expected

def test_empty_work_area_is_rejected():
    with pytest.raises(ValueError): maximize_geometry((0,0,1920,1080),(0,0,0,0))
