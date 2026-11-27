"""Linux performance evidence is optional and never invents healthy counters."""
from app.services.process_resources import counters, counter_delta


def test_missing_linux_counters_are_unavailable(tmp_path):
    result = counters(tmp_path / 'missing', tmp_path / 'missing')
    assert all(v is None for v in result['cgroup_v2'].values())
    assert all(v is None for v in result['host_pressure'].values())


def test_linux_throttling_oom_io_and_host_pressure_are_distinct(tmp_path):
    root = tmp_path / 'cgroup'
    proc = tmp_path / 'proc'
    root.mkdir()
    (proc / 'pressure').mkdir(parents=True)
    (root / 'cpu.stat').write_text('usage_usec 100\nnr_throttled 2\nthrottled_usec 40\n')
    (root / 'memory.events').write_text('oom 0\noom_kill 0\n')
    (root / 'memory.current').write_text('1024\n')
    (root / 'memory.peak').write_text('2048\n')
    (root / 'io.stat').write_text('8:0 rbytes=100 wbytes=200 rios=1 wios=2\n')
    (proc / 'pressure' / 'io').write_text('some avg10=0.00 avg60=0.00 avg300=0.00 total=123\nfull avg10=0.00 avg60=0.00 avg300=0.00 total=100\n')
    result = counters(root, proc)
    assert result['cgroup_v2']['cpu.stat']['throttled_usec'] == 40
    assert result['cgroup_v2']['memory.events']['oom_kill'] == 0
    assert result['cgroup_v2']['io.stat']['8:0']['wbytes'] == 200
    assert result['host_pressure']['io'] == {'some': 123, 'full': 100}
    assert counter_delta({'count': 4, 'reset': 8, 'absent': None}, {'count': 7, 'reset': 1, 'absent': 0}) == {'count': 3, 'reset': None, 'absent': None}
