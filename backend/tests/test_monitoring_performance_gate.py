"""The release must fail closed on incomplete/faster-but-weaker benchmark profiles."""
import importlib.util
import json
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("noc121_pilot_benchmark", ROOT / "scripts/release/pilot_benchmark.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)


def evidence():
    return {
        "limits": {"backend_cpus":2,"backend_memory_mib":768,"database_cpus":2,
                   "database_memory_mib":1024,"polling_threads":8,"pool_size":10},
        "volume_filesystems":{"postgres":"ext4","monitoring_outbox":"ext4"},
        "postgres_settings":["fsync=on","full_page_writes=on","synchronous_commit=on"],
        "target_status":"PASS","target_seconds":15,"measured_rounds":18,
        "records":[{"repeat":rep,"devices":size,"sites":40,"concurrency":8,
                    "spool_parent":"explicit_private_volume","samples_and_unique_receipts":size*6,
                    "rounds":[{"round":n,"samples":size,"elapsed_seconds":15.0,
                               "phase_profile_inclusive":{"durability.fsync":{"calls":size*3}}}
                              for n in range(1,7)]}
                   for rep in (1,2,3) for size in (250,500,1000)],
    }


def test_exact_full_durable_boundary_passes():
    gate.require_acceptance(evidence())


@pytest.mark.parametrize("change", [
    "miss","nan","bool","zero","samples","receipts","duplicates","round","round_missing",
    "tmpfs_db","tmpfs_spool","missing_fs","durability","limits","threads","sites",
    "spool","fsync","status","target","measured",
])
def test_misses_or_weaker_profiles_fail_closed(change):
    e=evidence()
    row=e["records"][-1]
    if change=="miss":row["rounds"][-1]["elapsed_seconds"]=15.001
    elif change=="nan":row["rounds"][0]["elapsed_seconds"]=float("nan")
    elif change=="bool":row["rounds"][0]["elapsed_seconds"]=True
    elif change=="zero":row["rounds"][0]["elapsed_seconds"]=0
    elif change=="samples":row["rounds"][0]["samples"]-=1
    elif change=="receipts":row["samples_and_unique_receipts"]-=1
    elif change=="duplicates":e["records"][-1]=e["records"][0]
    elif change=="round":row["rounds"][-1]["round"]=1
    elif change=="round_missing":row["rounds"].pop()
    elif change=="tmpfs_db":e["volume_filesystems"]["postgres"]="tmpfs"
    elif change=="tmpfs_spool":e["volume_filesystems"]["monitoring_outbox"]="ramfs"
    elif change=="missing_fs":e["volume_filesystems"].pop("monitoring_outbox")
    elif change=="durability":e["postgres_settings"].remove("fsync=on")
    elif change=="limits":e["limits"]["backend_cpus"]=4
    elif change=="threads":row["concurrency"]=16
    elif change=="sites":row["sites"]=39
    elif change=="spool":row["spool_parent"]="temporary_storage_not_durable_acceptance"
    elif change=="fsync":row["rounds"][0]["phase_profile_inclusive"]["durability.fsync"]["calls"]-=1
    elif change=="status":e["target_status"]="FAIL"
    elif change=="target":e["target_seconds"]=16
    elif change=="measured":e["measured_rounds"]=17
    with pytest.raises(ValueError):
        gate.require_acceptance(e)


def test_strict_cli_failure_retains_written_evidence(tmp_path, monkeypatch):
    data=evidence()
    data["records"][-1]["rounds"][0]["elapsed_seconds"]=15.001
    output=tmp_path/"evidence.json"
    def measured(backend, postgres, path):
        path.write_text(json.dumps(data))
        return data
    monkeypatch.setattr(gate, "measure", measured)
    with pytest.raises(ValueError, match="15-second target"):
        gate.main(["--backend","synthetic","--postgres","synthetic","--output",str(output),"--require-target"])
    assert json.loads(output.read_text())["records"][-1]["rounds"][0]["elapsed_seconds"] == 15.001
