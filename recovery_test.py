
import json
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent
REPORT_DIR = PROJECT_DIR / "reports"
HEALTH_URL = "http://127.0.0.1:5000/health"
RECORDS_URL = "http://127.0.0.1:5000/records"
EXPECTED_RECORD_ID = 1


def run_docker(*args):
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=30
    )

    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout)

    return result.stdout.strip()


def check_health():
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=3) as response:
            data = json.loads(response.read().decode())
            return response.status == 200 and data.get("status") == "healthy"
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return False


def get_records():
    with urllib.request.urlopen(RECORDS_URL, timeout=3) as response:
        return json.loads(response.read().decode())


def wait_for_recovery(timeout_seconds=60):
    deadline = time.monotonic() + timeout_seconds

    while time.monotonic() < deadline:
        if check_health():
            return True
        time.sleep(2)

    return False


def main():
    REPORT_DIR.mkdir(exist_ok=True)

    report = {
        "test": "Automated Disaster Recovery Testing",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "FAILED"
    }

    start_time = time.monotonic()

    try:
        # Confirm the application and database are healthy before testing.
        if not check_health():
            raise RuntimeError(
                "Application/database is not healthy before the test."
            )

        # Confirm the baseline record exists before simulating failure.
        records_before = get_records()
        baseline_exists = any(
            record["id"] == EXPECTED_RECORD_ID
            for record in records_before
        )

        if not baseline_exists:
            raise RuntimeError(
                f"Baseline record {EXPECTED_RECORD_ID} was not found."
            )

        report["baseline_record_id"] = EXPECTED_RECORD_ID

        # Simulate a database failure without deleting its data or volume.
        run_docker("stop", "db")
        report["failure_simulated"] = True

        # Restart the database and wait for the app to reconnect.
        run_docker("start", "db")
        report["recovery_completed"] = wait_for_recovery()

        if not report["recovery_completed"]:
            raise RuntimeError(
                "Application/database did not recover within 60 seconds."
            )

        # Verify that the baseline record survived the restart.
        records_after = get_records()
        report["data_persisted"] = any(
            record["id"] == EXPECTED_RECORD_ID
            for record in records_after
        )

        if not report["data_persisted"]:
            raise RuntimeError("Baseline record was missing after recovery.")

        report["status"] = "PASSED"

    except Exception as error:
        report["error"] = str(error)

    finally:
        report["recovery_time_seconds"] = round(
            time.monotonic() - start_time, 2
        )
        report["finished_at"] = datetime.now(timezone.utc).isoformat()

        report_path = REPORT_DIR / "latest_recovery_report.json"
        report_path.write_text(
            json.dumps(report, indent=4),
            encoding="utf-8"
        )

        print(json.dumps(report, indent=4))
        print(f"\nReport saved to: {report_path}")


if __name__ == "__main__":
    main()
