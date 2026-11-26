"""Readiness is read-only; unknown host controls never pass."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import pytest
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('lab_preflight',ROOT/'scripts/host/preflight.py')
pf=importlib.util.module_from_spec(spec);spec.loader.exec_module(pf)


def report(monkeypatch,**overrides):
    cpu='\n\n'.join('processor : '+str(n)+'\nmodel name : Synthetic CPU\nflags : pni ssse3 sse4_1 sse4_2 popcnt cx16 lahf_lm' for n in range(4))
    files={'/etc/os-release':'ID=ubuntu\nVERSION_ID="24.04"','/proc/cpuinfo':cpu,'/proc/meminfo':'MemTotal: 16777216 kB\nMemAvailable: 8388608 kB'}
    files.update(overrides.pop('files',{}))
    outputs={'findmnt':'ext4','lsblk':json.dumps({'blockdevices':[{'type':'disk','rota':False,'tran':'nvme'}]}),'docker':'28.0.1','compose':'v2.38.2','ip':json.dumps([{'operstate':'UP','addr_info':[{'local':'192.0.2.10'}]}]),'timedatectl':'yes','ss':'','ufw':'Status: active','getent':'192.0.2.10'}
    outputs.update(overrides);calls=[]
    def run(args):
        calls.append(args)
        return outputs['compose' if args[:2]==['docker','compose'] else args[0]]
    monkeypatch.setattr(pf.platform,'machine',lambda:'x86_64');monkeypatch.setattr(pf.os,'geteuid',lambda:1000)
    monkeypatch.setattr(pf.shutil,'disk_usage',lambda _:SimpleNamespace(total=250*pf.GIB,free=150*pf.GIB))
    result=pf.collect(run=run,reader=lambda path:files.get(path,''))
    return {row['check']:row for row in result['checks']},calls


def test_default_no_dns_mutation_or_address_disclosure(monkeypatch):
    rows,calls=report(monkeypatch)
    assert rows['dns_resolution']['status']=='NOT VERIFIED'
    assert not any(c[0]=='getent' for c in calls)
    assert {c[0] for c in calls}<={'findmnt','lsblk','docker','ip','timedatectl','ss','ufw'}
    assert '192.0.2.10' not in json.dumps(rows)
    for name in ('security_approval','static_ip_dns','resource_limits'):assert rows[name]['status']=='NOT VERIFIED'
    for name in ('firewall','storage_medium'):assert rows[name]['status']=='WARNING'


@pytest.mark.parametrize('field,output',[('docker','bad'),('docker','23.0.0'),('compose','1.29.2'),('compose','2.23.9'),('compose','3.0.0')])
def test_version_failures(monkeypatch,field,output):
    rows,_=report(monkeypatch,**{field:output});assert rows[field]['status']=='FAIL'


@pytest.mark.parametrize('fs,status',[('tmpfs','FAIL'),('ramfs','FAIL'),('overlay','FAIL'),('nfs','NOT VERIFIED'),(None,'NOT VERIFIED'),('ext4','PASS'),('xfs','PASS')])
def test_persistent_filesystem(monkeypatch,fs,status):
    rows,_=report(monkeypatch,findmnt=fs);assert rows['filesystem']['status']==status


def test_one_bad_cpu(monkeypatch):
    rows,_=report(monkeypatch,files={'/proc/cpuinfo':'processor : 0\nflags : pni ssse3 sse4_1 sse4_2 popcnt cx16 lahf_lm\nprocessor : 1\nflags : pni'})
    assert rows['cpu_abi']['status']=='FAIL'


def test_memory_os(monkeypatch):
    rows,_=report(monkeypatch,files={'/etc/os-release':'ID=debian\nVERSION_ID=12','/proc/meminfo':'MemTotal: 2097152 kB\nMemAvailable: 1048576 kB'})
    assert rows['os']['status']=='FAIL' and rows['memory']['status']=='FAIL'


def test_clock_daemon(monkeypatch):
    rows,_=report(monkeypatch,timedatectl='no',docker=None)
    assert rows['time_sync']['status']=='FAIL' and rows['docker']['status']=='FAIL'


def test_port_network(monkeypatch):
    rows,_=report(monkeypatch,ss='LISTEN 0 128 127.0.0.1:18090 0.0.0.0:*',ip='bad')
    assert rows['ports']['status']=='FAIL' and rows['network']['status']=='NOT VERIFIED'


def test_unknown_probes(monkeypatch):
    rows,_=report(monkeypatch,ss=None,ufw=None,timedatectl=None,lsblk=None)
    for name in ('ports','firewall','time_sync','storage_medium'):assert rows[name]['status']=='NOT VERIFIED'


@pytest.mark.parametrize('name',['--help','https://private.invalid','a;id','a\nb','x'*254])
def test_dns_input(name):
    with pytest.raises(SystemExit) as exc:pf.main(['--dns-name',name])
    assert exc.value.code==2


def test_cli_explicit_dns_exit(monkeypatch,capsys):
    args={}
    def collect(**kw):
        args.update(kw);return {'failed':1,'checks':[{'check':'dns_resolution','status':'FAIL','detail':'synthetic'}]}
    monkeypatch.setattr(pf,'collect',collect)
    assert pf.main(['--dns-name','approved.example.invalid','--json'])==1
    assert args['dns_name']=='approved.example.invalid'
    assert json.loads(capsys.readouterr().out)['failed']==1


def test_bounded_command_no_stderr():
    assert pf.command([sys.executable,'-c','import sys;sys.stderr.write("secret");print("safe")'])=='safe'
    assert pf.command([sys.executable,'-c','print("x"*300000)']) is None
    assert pf.command(['/nonexistent-readiness-command']) is None


def test_supported_expat_floor():
    text=(ROOT/'backend/Dockerfile').read_text()
    assert 'dpkg --compare-versions' in text and 'ge 2.6.1-2ubuntu0.7' in text
    assert 'apt-get -o APT::Update::Error-Mode=any update' in text


@pytest.mark.parametrize('total,free,status',[(99,80,'FAIL'),(100,29,'FAIL'),(100,30,'PASS')])
def test_capacity_boundaries(monkeypatch,total,free,status):
    rows,calls=report(monkeypatch)
    monkeypatch.setattr(pf.shutil,'disk_usage',lambda _:SimpleNamespace(total=total*pf.GIB,free=free*pf.GIB))
    result=pf.collect(run=lambda argv:None,reader=lambda path:'')
    assert next(r for r in result['checks'] if r['check']=='disk_capacity')['status']==status


def test_existing_install_is_never_overwritten(monkeypatch):
    original=pf.Path.exists
    monkeypatch.setattr(pf.Path,'exists',lambda p:True if str(p)=='/opt/nwo-noc' else original(p))
    rows,_=report(monkeypatch)
    assert rows['existing_installation']['status']=='FAIL'
    assert '/opt/nwo-noc' in rows['existing_installation']['detail']
