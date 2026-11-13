from datetime import timedelta
import pytest
from app.models.entities import RFAnalyticsPoint
from app.services.rf_operations import point_state,health
from app.services.rf_analytics_policy import AnalyticsPolicy
from app.api.rf_operations_routes import Review,redact
from app.core.operator_text import clean_operator_text
from rf_analytics_fixtures import NOW

@pytest.mark.parametrize('when,valid,expected',[(NOW-timedelta(hours=1),True,'stale'),(NOW+timedelta(seconds=1),True,'future'),(NOW,False,'invalid')])
def test_unusable_measurements_cannot_be_healthy(when,valid,expected):
    p=RFAnalyticsPoint(sampled_at=when,valid=valid,signal_dbm=-60,radio_state='up')
    r=point_state(p,NOW,AnalyticsPolicy())
    assert r['state']=='unknown' and r['freshness']==expected and not r['flags']

def test_missing_values_are_not_zero_and_configured_freshness_is_respected():
    p=RFAnalyticsPoint(sampled_at=NOW,valid=True)
    assert point_state(p,NOW,AnalyticsPolicy())['state']=='unknown'
    assert health([])['state']=='unknown'
    p.radio_state='up'
    assert point_state(p,NOW,AnalyticsPolicy())['state']=='unknown'  # Connectivity alone is not RF metric coverage.
    p.signal_dbm=-85
    assert 'weak_signal' in point_state(p,NOW,AnalyticsPolicy())['flags']
    p.sampled_at=NOW-timedelta(seconds=120)
    assert point_state(p,NOW,AnalyticsPolicy(stale_after_seconds=60))['freshness']=='stale'

@pytest.mark.parametrize('key',['rf-analytics:device:0','rf-analytics:device:1;DROP TABLE users','integration:password:1'])
def test_review_keys_cannot_address_arbitrary_records(key):
    with pytest.raises(ValueError):Review(alert_key=key,observed_at=NOW,operation='acknowledge')

def test_review_requires_source_timezone_and_known_operation():
    with pytest.raises(ValueError):Review(alert_key='rf-analytics:device:1',observed_at=NOW.replace(tzinfo=None),operation='acknowledge')
    with pytest.raises(ValueError):Review(alert_key='rf-analytics:device:1',observed_at=NOW,operation='reboot')

def test_audit_redaction_and_operator_text_injection_safety():
    record={'changes':{'token':'canary'},'note':'password=canary not relevant','status':'investigating'}
    assert 'canary' not in str(redact(record)) and redact(record)['status']=='investigating'
    with pytest.raises(ValueError):clean_operator_text('API-key: canary')
    # Markup stays plain data for React escaping, never executable HTML.
    assert clean_operator_text('<script>untrusted</script>')=='<script>untrusted</script>'
