from datetime import timedelta
import pytest
from sqlalchemy import func, select
from app.models.entities import Device, NetworkLink, TelemetrySample, Site
from app.services.wave_evidence import Observation, stage_wave, history, KEY
from test_rf_postgres_integration import postgres_db
from test_wave_evidence import NOW, observation


def device(db, **changes):
    d=Device(**(dict(source='synthetic',external_id='wave-test',name='Synthetic Wave',vendor='Ubiquiti',model='Wave AP',enabled=True)|changes))
    db.add(d);db.flush();return d


def test_dedup_history_and_conservative_recovery(postgres_db):
    db=postgres_db;d=device(db)
    for i in range(3):
        o=observation(observed_at=NOW+timedelta(seconds=60*i),active_path='primary',primary_state='up')
        a=stage_wave(db,d.id,o,now=o.observed_at,offline_authorized=True)
        assert stage_wave(db,d.id,o,now=o.observed_at,offline_authorized=True).id==a.id
    assert db.scalar(select(func.count()).select_from(TelemetrySample))==3
    result=history(db,d.id,now=NOW+timedelta(seconds=121))
    assert result['analysis']['recovery_verified']
    assert result['native_compatibility']=='BLOCKED'
    assert 'rf_metrics_v1' not in a.raw_data  # cannot pollute live diagnostics
    assert d.monitor_status=='unknown'
    with pytest.raises(ValueError,match='Conflicting'):
        stage_wave(db,d.id,observation(availability='down'),now=NOW,offline_authorized=True)
    assert history(db,d.id,now=NOW+timedelta(hours=1))['analysis']['availability']=='unknown'


@pytest.mark.parametrize('changes', [dict(source='uisp'),dict(enabled=False),dict(model='Wave LR'),dict(vendor='Cambium')])
def test_no_live_inventory_or_model_confusion(postgres_db,changes):
    d=device(postgres_db,**changes)
    with pytest.raises(ValueError):
        stage_wave(postgres_db,d.id,observation(),now=NOW,offline_authorized=True)


def test_associations_require_canonical_inventory_and_link(postgres_db):
    db=postgres_db;a=device(db);b=device(db,external_id='wave-peer',model='Wave Pro')
    for n in ('A','B'):
        db.add(Site(name=n))
    db.flush();sites=db.scalars(select(Site)).all()
    link=NetworkLink(name='synthetic-wave-link',a_site_id=sites[0].id,z_site_id=sites[1].id,a_device_id=a.id,z_device_id=b.id)
    db.add(link);db.flush()
    o=Observation(model='Wave AP',role='ptp',availability='unknown',observed_at=NOW,link_id=link.id,remote_device_id=b.id)
    stage_wave(db,a.id,o,now=NOW,offline_authorized=True)
    assert db.scalar(select(func.count()).select_from(NetworkLink))==1
    with pytest.raises(ValueError,match='endpoints'):
        stage_wave(db,a.id,o.model_copy(update={'link_id':9999}),now=NOW,offline_authorized=True)
    with pytest.raises(ValueError,match='Self'):
        stage_wave(db,a.id,o.model_copy(update={'remote_device_id':a.id}),now=NOW,offline_authorized=True)


def test_invalid_history_never_leaks_and_rollback(postgres_db):
    db=postgres_db;d=device(db)
    with pytest.raises(RuntimeError):
        with db.begin_nested():
            stage_wave(db,d.id,observation(),now=NOW,offline_authorized=True)
            raise RuntimeError('synthetic interruption')
    assert not history(db,d.id,now=NOW)['samples']
    db.add(TelemetrySample(device_id=d.id,sampled_at=NOW,raw_data={KEY:{'secret':'canary'}}));db.flush()
    result=history(db,d.id,now=NOW)
    assert result['analysis']['availability']=='unknown' and result['invalid_samples']==1
    assert 'canary' not in str(result)
