"""Runtime acceptance: only the generated offline stack; stdlib HTTP client."""
import json
import os
import secrets
from pathlib import Path
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts/release'))
from validate_config import validate
STATE = os.environ.get('NOC_ACCEPTANCE_STATE')


@unittest.skipUnless(STATE, 'Set NOC_ACCEPTANCE_STATE to explicit isolated state directory')
class Acceptance(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.run_id = secrets.token_hex(4)
        cls.restore_target = 'restore_' + cls.run_id
        cls.env = validate(Path(STATE) / 'acceptance.env')
        cls.base = 'http://127.0.0.1:' + os.environ.get('NOC_FRONTEND_PORT', '18090')
        cls.token = cls.login('acceptance')
        cls.manage('exec', '-T', 'backend', 'python', '-m', 'app.acceptance_seed')
        cls.device = cls.request('/api/devices')[1][0]['id']

    @classmethod
    def manage(cls, *args, check=True):
        return subprocess.run([str(ROOT/'scripts/release/manage.sh'), STATE, *args],
            check=check, capture_output=True, text=True)

    @classmethod
    def request(cls, path, method='GET', body=None, token=True):
        headers = {}
        if token:
            headers['Authorization'] = 'Bearer ' + (cls.token if token is True else token)
        if body is not None:
            headers['Content-Type'] = 'application/json'
            body = json.dumps(body).encode()
        req = urllib.request.Request(cls.base+path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = response.read()
                try: data = json.loads(data)
                except ValueError: data = data.decode()
                return response.status, data
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    @classmethod
    def login(cls, username):
        req = urllib.request.Request(cls.base+'/api/auth/login', data=urllib.parse.urlencode(
            dict(username=username, password=cls.env['ADMIN_PASSWORD'])).encode())
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.load(response)['access_token']

    def test_01_health_frontend(self):
        self.assertEqual(self.request('/health')[1]['status'], 'ok')
        self.assertIn('<div id="root">', self.request('/')[1])

    def test_02_authentication(self):
        self.assertEqual(self.request('/api/devices', token=False)[0], 401)
        self.assertEqual(self.request('/api/auth/me', token='invalid')[0], 401)
        self.assertEqual(self.request('/api/auth/me')[1]['username'], 'acceptance')

    def test_03_inventory_and_operations(self):
        for endpoint in ['devices','sites','links','sectors','monitoring/summary',
                         'monitoring/events','operations/readiness','incidents','alerts','topology','dashboard','operations/summary']:
            with self.subTest(endpoint=endpoint):
                self.assertEqual(self.request('/api/'+endpoint)[0], 200)
        a = self.request('/api/sites', 'POST', {'name':'NOC84 API A '+self.run_id})[1]['id']
        z = self.request('/api/sites', 'POST', {'name':'NOC84 API Z '+self.run_id})[1]['id']
        self.assertEqual(self.request('/api/links','POST',dict(name='NOC84 Link '+self.run_id,a_site_id=a,z_site_id=z))[0],201)
        self.assertEqual(self.request('/api/sectors','POST',dict(name='NOC84 Sector '+self.run_id,site_id=a))[0],201)
        self.assertEqual(self.request('/api/incidents','POST',dict(title='NOC84 Synthetic Incident',site_id=a))[0],201)

    def test_04_role_authorization(self):
        viewer = self.login('acceptance-viewer')
        self.assertEqual(self.request('/api/sites','POST',{'name':'Forbidden'},token=viewer)[0],403)
        self.assertEqual(self.request(f'/api/devices/{self.device}/rf-diagnostics',token=viewer)[0],403)

    def test_05_rf_simulation(self):
        self.assertEqual(self.request(f'/api/devices/{self.device}/rf-diagnostics')[0],200)
        result = self.request(f'/api/devices/{self.device}/rf-self-healing/preview')[1]
        self.assertFalse(result['execution_enabled'])
        self.assertEqual(result['mode'],'simulation_only')
        self.assertEqual(self.request(f'/api/devices/{self.device}/rf-self-healing/execute','POST',{})[0],404)
        self.assertEqual(self.request('/api/integrations/uisp/sync','POST',{})[0],403)

    def test_06_database_migrations(self):
        self.manage('exec','-T','backend','alembic','check')
        self.manage('exec','-T','backend','alembic','upgrade','head')

    def test_07_worker_and_monitoring(self):
        for _ in range(25):
            result = self.request(f'/api/devices/{self.device}')[1]
            if result['monitor_status']=='up': break
            time.sleep(1)
        self.assertEqual(result['monitor_status'],'up')
        self.manage('exec','-T','worker','python','-m','app.worker_health')
        self.assertIn('PONG',self.manage('exec','-T','redis','redis-cli','ping').stdout)

    def test_08_runtime_isolation(self):
        rendered=json.loads(self.manage('config','--format','json').stdout)
        self.assertTrue(rendered['networks']['isolated']['internal'])
        for service in rendered['services'].values():
            for port in service.get('ports',[]): self.assertEqual(port['host_ip'],'127.0.0.1')
        code='''import socket
for host in ['192.0.2.1', '198.51.100.1', '203.0.113.1']:
 try:
  socket.create_connection((host,443),timeout=1)
 except OSError: continue
 raise SystemExit('Unexpected outbound connectivity')
print('TEST-NET outbound denied')'''
        self.manage('exec','-T','backend','python','-c',code.replace('RESTORE_TARGET',self.restore_target))

    def test_09_restart_and_failure_recovery(self):
        self.manage('stop','redis')
        self.assertNotEqual(self.manage('exec','-T','worker','python','-m','app.worker_health',check=False).returncode,0)
        self.manage('start','redis')
        self.manage('restart','backend','frontend','worker')
        for _ in range(60):
            try:
                if self.request('/api/auth/me')[0]==200 and self.manage('exec','-T','worker','python','-m','app.worker_health',check=False).returncode==0: return
            except (OSError,urllib.error.URLError): pass
            time.sleep(1)
        self.fail('Services did not recover after Redis failure/restart')

    def test_09b_database_restart_recovery(self):
        self.manage('restart','db','worker')
        self.manage('up','-d','--no-deps','--force-recreate','backend')
        for _ in range(60):
            try:
                if self.request('/api/operations/readiness')[0]==200 and self.manage('exec','-T','worker','python','-m','app.worker_health',check=False).returncode==0:
                    return
            except (OSError,urllib.error.URLError): pass
            time.sleep(1)
        self.fail('Database restart did not recover API and worker')

    def test_10_backup_restore(self):
        self.manage('stop','worker')
        dump=str(Path(STATE)/('acceptance-'+self.run_id+'.dump'))
        subprocess.run([str(ROOT/'scripts/release/backup.sh'),STATE,dump],check=True)
        subprocess.run([str(ROOT/'scripts/release/restore.sh'),STATE,dump,self.restore_target],check=True)
        query="SELECT count(*) FROM devices WHERE external_id='noc84-synthetic'"
        for database in ['nwo_noc',self.restore_target]:
            self.assertEqual(self.manage('exec','-T','db','psql','-U','noc_test','-d',database,'-Atc',query).stdout.strip(),'1')
        code="""from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from app.core.config import settings
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
snapshots=[]
for database in ['nwo_noc','RESTORE_TARGET']:
 engine=create_engine(make_url(settings.database_url).set(database=database))
 with engine.connect() as connection:
  heads=set(MigrationContext.configure(connection).get_current_heads())
  assert heads==set(ScriptDirectory.from_config(Config('alembic.ini')).get_heads())
  snapshots.append({table:connection.execute(text('SELECT count(*) FROM "'+table+'"')).scalar_one() for table in inspect(engine).get_table_names()})
assert snapshots[0]==snapshots[1], 'Restored table counts differ'
print('All table counts and migration heads match')"""
        self.manage('exec','-T','backend','python','-c',code.replace('RESTORE_TARGET',self.restore_target))
        self.manage('start','worker')
        result=subprocess.run([str(ROOT/'scripts/release/restore.sh'),STATE,dump,'nwo_noc'],capture_output=True)
        self.assertNotEqual(result.returncode,0)


if __name__=='__main__': unittest.main(verbosity=2)
