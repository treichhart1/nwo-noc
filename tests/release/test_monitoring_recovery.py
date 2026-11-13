"""Actual isolated Compose process interruptions; never runs against a live stack."""

import json, os, re, subprocess, sys, time, unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/release"))
from validate_config import validate

STATE = os.environ.get("NOC_ACCEPTANCE_STATE")


@unittest.skipUnless(
    STATE and os.environ.get("NOC92_RECOVERY") == "true",
    "Explicit isolated NOC92 recovery required",
)
class Recovery(unittest.TestCase):
    @classmethod
    def manage(cls, *args):
        return subprocess.run(
            [str(ROOT / "scripts/release/manage.sh"), STATE, *args],
            capture_output=True,
            text=True,
            check=True,
            timeout=90,
        ).stdout

    @classmethod
    def status(cls):
        return json.loads(
            cls.manage(
                "exec",
                "-T",
                "backend",
                "python",
                "-m",
                "app.monitoring_rehearsal",
                "status",
            )
        )

    @classmethod
    def wait(cls, predicate, timeout=65):
        start = time.monotonic()
        while time.monotonic() - start < timeout:
            try:
                s = cls.status()
                if predicate(s):
                    return s, round(time.monotonic() - start, 3)
            except (subprocess.CalledProcessError, json.JSONDecodeError):
                pass
            time.sleep(1)
        raise AssertionError(
            "Synthetic monitoring failed to recover within bounded observation window"
        )

    @classmethod
    def setUpClass(cls):
        env = validate(Path(STATE) / "acceptance.env")
        assert re.fullmatch(r"noc84-[a-z0-9-]+", os.environ.get("NOC_PROJECT", ""))
        assert (
            env.get("RF_SCHEDULER_ENABLED") == "true"
            and env.get("SYNTHETIC_MONITORING_ENABLED") == "true"
        )
        cls.manage(
            "exec", "-T", "backend", "python", "-m", "app.monitoring_rehearsal", "seed"
        )
        cls.wait(lambda s: s["receipts"] >= 1)

    def test_01_redis_interruption_does_not_stop_postgres_polling(self):
        before = self.status()
        self.manage("stop", "redis")
        try:
            s, elapsed = self.wait(
                lambda s: s["receipts"] > before["receipts"]
                and s["workers"][0]["redis_state"] == "unavailable"
            )
            print(
                json.dumps(
                    {
                        "redis_down_polling_continued_seconds": elapsed,
                        "receipts_added": s["receipts"] - before["receipts"],
                    }
                ),
                flush=True,
            )
        finally:
            self.manage("up", "-d", "--wait", "redis")
        s, elapsed = self.wait(
            lambda s: s["events"].get("monitoring.redis_recovered", 0)
            > before["events"].get("monitoring.redis_recovered", 0)
        )
        print(json.dumps({"redis_recovery_seconds": elapsed}), flush=True)

    def test_02_database_interruption_resumes_durable_job(self):
        before = self.status()
        self.manage("stop", "db")
        try:
            time.sleep(12)
        finally:
            self.manage("up", "-d", "--wait", "db")
        s, elapsed = self.wait(
            lambda s: s["receipts"] > before["receipts"]
            and s["events"].get("monitoring.database_recovered", 0)
            > before["events"].get("monitoring.database_recovered", 0)
        )
        print(
            json.dumps(
                {
                    "database_recovery_after_start_seconds": elapsed,
                    "job_key_unchanged": s["job_key"] == before["job_key"],
                }
            ),
            flush=True,
        )

    def test_03_worker_restart_resumes_schedule(self):
        before = self.status()
        self.manage("restart", "worker")
        s, elapsed = self.wait(
            lambda s: s["receipts"] > before["receipts"]
            and s["workers"][0]["id"] != before["workers"][0]["id"]
        )
        assert s["job_key"] == before["job_key"] and s["last_error"] is None
        print(
            json.dumps(
                {
                    "worker_recovery_after_restart_seconds": elapsed,
                    "new_worker_id": s["workers"][0]["id"],
                }
            ),
            flush=True,
        )

    def test_04_real_time_rf_detection_and_stable_recovery(self):
        started = time.monotonic()
        self.manage(
            "exec", "-T", "backend", "python", "-m", "app.monitoring_rehearsal", "weak"
        )
        s, _ = self.wait(lambda s: s["rf_signal_alert"] == "active")
        detection = round(time.monotonic() - started, 3)
        assert s["rf_signal_episodes"] == 1
        started = time.monotonic()
        self.manage(
            "exec",
            "-T",
            "backend",
            "python",
            "-m",
            "app.monitoring_rehearsal",
            "normal",
        )
        s, _ = self.wait(lambda s: s["rf_signal_alert"] == "cleared")
        recovery = round(time.monotonic() - started, 3)
        assert s["rf_signal_episodes"] == 1 and s["receipts"] == s["sample_count"]
        print(
            json.dumps(
                {
                    "real_time_rf_detection_seconds": detection,
                    "real_time_stable_recovery_seconds": recovery,
                    "poll_interval_seconds": 15,
                    "consecutive_samples": 3,
                    "unique_receipts_match_samples": True,
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
