from datetime import datetime,timedelta,timezone
import pytest
from pydantic import ValidationError
from app.services.rf_analytics_engine import analyze_history,score
from app.services.rf_analytics_policy import AnalyticsPolicy

NOW=datetime(2026,11,13,18,tzinfo=timezone.utc)
NORMAL=dict(signal_dbm=-60,remote_signal_dbm=-62,snr_db=30,noise_floor_dbm=-95,modulation_bits=8,mcs_index=9,
    capacity_mbps=500,tx_throughput_mbps=100,rx_throughput_mbps=50,utilization_percent=30,quality_percent=98,
    tx_errors_per_second=1,rx_errors_per_second=1,retries_per_second=1,uptime_seconds=1000,frequency_mhz=5800,bandwidth_mhz=40)


def baseline(**changes):return {k:dict(ready=True,median=v,p10=v-1,p90=v+1,count=50,valid_fraction=1,span_seconds=50000) for k,v in (NORMAL|changes).items()}


def point(i=0,**changes):return dict(valid=True,sampled_at=NOW+timedelta(seconds=i*60),mapping_digest='a'*64,radio_state='up')|NORMAL|{'uptime_seconds':1000+i*60}|changes


def run(points,normal=None):return analyze_history(points,baseline() if normal is None else normal,now=points[-1]['sampled_at'])


def test_healthy_score_explains_coverage_and_baseline_support():
    report=run([point(i) for i in range(6)])
    assert report['state']=='observed_stable' and report['score']['value']==100
    assert report['score']['metric_coverage']==1 and report['confidence']['level']=='high'
    assert all(c['penalty']==0 for c in report['score']['contributions']) and not report['execution_enabled']


def test_cold_start_no_fabricated_baseline_and_sparse_score_is_not_confidence():
    report=run([point(i) for i in range(6)],{})
    assert report['state']=='insufficient_baseline' and report['confidence']['level']=='low'
    assert report['score']['value']==100  # Good available values do not prove baseline reliability.
    sparse=score({'signal_dbm':-60},{})
    assert sparse['value']==100 and sparse['metric_coverage']<.2 and 'snr' in sparse['missing_factors']
    assert score({},{} )['value'] is None
    down=score(NORMAL|{'radio_state':'down'},baseline())
    assert down['value']<=20 and down['caps_applied'][0]['reason']=='reported_radio_down'


@pytest.mark.parametrize('changes,pattern',[
    ({'signal_dbm':-85},'weak_signal'),({'signal_dbm':-70},'signal_decline'),
    ({'modulation_bits':4},'modulation_fallback'),({'capacity_mbps':200},'capacity_reduction'),
    ({'utilization_percent':95},'high_utilization'),({'tx_throughput_mbps':480},'throughput_saturation'),
    ({'tx_errors_per_second':50},'high_error_retry_rate'),({'retries_per_second':50},'increasing_error_retry_rate'),
    ({'radio_state':'down'},'radio_disconnected')])
def test_three_sample_validation_for_each_pattern(changes,pattern):
    points=[point(i,**changes) for i in range(3)]
    assert pattern not in run(points[:2])['patterns']
    assert pattern in run(points)['patterns']


def test_joint_noise_snr_evidence_is_only_suspected_interference():
    report=run([point(i,noise_floor_dbm=-82,snr_db=12) for i in range(6)])
    assert report['classification']=='suspected_interference' and report['interference_status']=='suspected'
    assert report['root_cause']=='unconfirmed' and report['evidence']['noise_change_db']==13
    assert 'noise_snr_pattern' in report['patterns']
    only_noise=run([point(i,noise_floor_dbm=-82) for i in range(6)])
    assert only_noise['interference_status']=='not_established'


def test_modulation_drop_with_stable_signal_needs_additional_error_evidence():
    weak=run([point(i,modulation_bits=4) for i in range(6)])
    assert weak['interference_status']=='not_established'
    strong=run([point(i,modulation_bits=4,retries_per_second=50) for i in range(6)])
    assert strong['interference_status']=='suspected' and 'stable_signal_fallback' in strong['patterns']
    faded=run([point(i,modulation_bits=4,retries_per_second=50,signal_dbm=-80) for i in range(6)])
    assert faded['interference_status']=='not_established'
    mcs_only=run([point(i,modulation_bits=None,mcs_index=1) for i in range(6)])
    assert 'mcs_index_change' in mcs_only['patterns'] and 'modulation_fallback' not in mcs_only['patterns']
    assert mcs_only['interference_status']=='not_established'


def test_likely_congestion_requires_utilization_real_throughput_and_stable_RF():
    report=run([point(i,utilization_percent=95,tx_throughput_mbps=480) for i in range(6)])
    assert report['classification']=='likely_congestion' and report['interference_status']=='not_established'
    assert report['evidence']['directional_throughput_capacity_ratio']==.96
    missing=run([point(i,utilization_percent=95,tx_throughput_mbps=None,rx_throughput_mbps=None) for i in range(6)])
    assert missing['classification']=='utilization_unclassified'
    phy_only=run([point(i,utilization_percent=95,tx_throughput_mbps=None,rx_throughput_mbps=None,tx_phy_mbps=1000) for i in range(6)])
    assert phy_only['classification']!='likely_congestion'


def test_sudden_step_persists_and_gradual_decline_uses_valid_baseline():
    sudden=run([point(0)]+[point(i,signal_dbm=-70) for i in range(1,5)])
    assert 'sudden_signal_change' in sudden['patterns'] and 'signal_decline' in sudden['patterns']
    gradual=run([point(i,signal_dbm=-60-i*2) for i in range(7)])
    assert 'signal_decline' in gradual['patterns'] and 'sudden_signal_change' not in gradual['patterns']


def test_hysteresis_spikes_missing_and_confirmed_clear():
    points=[point(i,noise_floor_dbm=-82,snr_db=12) for i in range(3)]
    points.extend(point(i,noise_floor_dbm=-91,snr_db=26) for i in (3,4,5))
    assert 'noise_snr_pattern' in run(points)['patterns']
    points.extend(point(i) for i in (6,7,8))
    assert run(points)['patterns']==[]
    spike=[point(0),point(1,noise_floor_dbm=-82,snr_db=12),point(2),point(3)]
    assert run(spike)['patterns']==[]
    missing=run([point(i,noise_floor_dbm=None) for i in range(6)])
    assert missing['state']=='partial_telemetry' and 'noise_floor_dbm' in missing['missing_expected_metrics']
    assert missing['interference_status']=='not_established'


@pytest.mark.parametrize('change',[{'valid':False},{'uptime_seconds':1},{'mapping_digest':'b'*64},{'frequency_mhz':6000}])
def test_invalid_restart_and_context_changes_break_streaks(change):
    points=[point(i,noise_floor_dbm=-82,snr_db=12) for i in range(3)]
    points.append(point(3,noise_floor_dbm=-82,snr_db=12,**change))
    assert 'noise_snr_pattern' not in run(points)['patterns']


def test_stale_future_gaps_and_repeated_outages():
    points=[point(i,radio_state=s) for i,s in enumerate(['up','down','up','down','up','down'])]
    assert 'recurring_disconnects' in run(points)['patterns']
    assert analyze_history(points,baseline(),now=NOW+timedelta(days=1))['score']['value'] is None
    assert analyze_history([point(2)],baseline(),now=NOW)['freshness']=='future'
    gap=point(50,noise_floor_dbm=-82,snr_db=12)
    assert 'noise_snr_pattern' not in run([point(0,noise_floor_dbm=-82,snr_db=12),point(1,noise_floor_dbm=-82,snr_db=12),gap])['patterns']
    with pytest.raises(ValueError):run([point(1),point(0)])


@pytest.mark.parametrize('changes',[{'weights':{'password':1}},{'weights':{'signal':float('nan')}},
    {'analysis_timezone':'not/a_zone'},{'utilization_clear':95},{'consecutive_samples':1},{'signal_clear':-80},
    {'minimum_observed_days':3,'minimum_recurring_days':4}])
def test_invalid_policy_rejected(changes):
    with pytest.raises(ValidationError):AnalyticsPolicy(**changes)
