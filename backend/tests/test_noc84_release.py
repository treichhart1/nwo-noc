import importlib.util
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.api.deps import require_operator
from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT/'scripts/release'/f'{name}.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_generated_config_is_private_and_never_overwritten(tmp_path):
    generate = load('configure').generate
    target = generate(tmp_path/'state')
    assert target.stat().st_mode & 0o777 == 0o600
    assert target.parent.stat().st_mode & 0o777 == 0o700
    assert load('validate_config').validate(target)['OFFLINE_MODE'] == 'true'
    original = target.read_text()
    with pytest.raises(FileExistsError): generate(target.parent)
    assert target.read_text() == original


@pytest.mark.parametrize('key,value', [('UISP_TOKEN','secret'), ('RF_COLLECTION_ENABLED','true'),
    ('DATABASE_URL','postgresql+psycopg://user:pass@production/db'), ('APP_ENV','production')])
def test_isolated_config_rejects_unsafe_values(tmp_path,key,value):
    target=load('configure').generate(tmp_path/'state')
    lines=target.read_text().splitlines()
    target.write_text('\n'.join(f'{key}={value}' if line.startswith(key+'=') else line for line in lines)+'\n')
    with pytest.raises(ValueError): load('validate_config').validate(target)


@pytest.mark.parametrize('role',['viewer','customer','unknown',''])
def test_mutations_require_operator(role):
    with pytest.raises(HTTPException) as exc: require_operator(SimpleNamespace(role=role))
    assert exc.value.status_code==403


@pytest.mark.parametrize('changes',[{'uisp_token':'unexpected'},{'uisp_base_url':'https://example.invalid'},
    {'rf_collection_enabled':True},{'rf_history_pruning_enabled':True}])
def test_offline_settings_fail_closed(changes):
    with pytest.raises(ValueError):
        Settings(_env_file=None, app_env='staging', offline_mode=True, secret_key='a'*40,
                 admin_password='b'*40,database_url='postgresql+psycopg://local/db',**changes)


def test_offline_monitoring_never_dials_external_target(monkeypatch):
    from app.services import monitoring
    monkeypatch.setattr(monitoring.settings, 'offline_mode', True)
    monkeypatch.setattr(monitoring.socket, 'create_connection', lambda *a, **k: pytest.fail('network call'))
    assert monitoring.tcp_check('192.0.2.1',443,1)[0] is False


def test_synthetic_monitoring_requires_offline_staging():
    with pytest.raises(ValueError):
        Settings(_env_file=None,app_env='production',synthetic_monitoring_enabled=True,
            secret_key='a'*40,admin_password='b'*40,database_url='postgresql+psycopg://local/db')


def test_synthetic_monitoring_never_uses_socket(monkeypatch):
    from app.services import monitoring
    monkeypatch.setattr(monitoring.settings,'offline_mode',True)
    monkeypatch.setattr(monitoring.settings,'synthetic_monitoring_enabled',True)
    monkeypatch.setattr(monitoring.socket,'create_connection',lambda *a,**k:pytest.fail('network call'))
    assert monitoring.tcp_check('198.18.0.1',443,1)==(True,1.0,None)
    assert monitoring.tcp_check('192.0.2.1',443,1)[0] is False
