
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
RECOVERY_RESULT_URL = "http://127.0.0.1:5000/recovery-result"

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
            return (
                response.status == 200
                and data.get("status") == "healthy"
            )
    except (
        urllib.error.URLError,
        TimeoutError,
        OSError,
        json.JSONDecodeError
    ):
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


def publish_recovery_metrics(report):
    payload = {
        "result": report["status"].lower(),
        "duration_seconds": report["recovery_time_seconds"],
        "data_persisted": report.get("data_persisted", False)
    }

    request_data = json.dumps(payload).encode("utf-8")

    req = urllib.request.Request(
        RECOVERY_RESULT_URL,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(req, timeout=5) as response:
        return json.loads(response.read().decode())


def main():
    REPORT_DIR.mkdir(exist_ok=True)

    report = {
        "test": "Automated Disaster Recovery Testing",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "FAILED",
        "failure_simulated": False,
        "recovery_completed": False,
        "data_persisted": False
    }

    start_time = time.monotonic()

    try:
        if not check_health():
            raise RuntimeError(
                "Application/database is not healthy before the test."
            )

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

        # Stop and restart only the database container.
        # Do not remove containers or volumes.
        run_docker("stop", "db")
        report["failure_simulated"] = True

        run_docker("start", "db")

        report["recovery_completed"] = wait_for_recovery()

        if not report["recovery_completed"]:
            raise RuntimeError(
                "Application/database did not recover within 60 seconds."
            )

        records_after = get_records()

        report["data_persisted"] = any(
            record["id"] == EXPECTED_RECORD_ID
            for record in records_after
        )

        if not report["data_persisted"]:
            raise RuntimeError(
                "Baseline record was missing after recovery."
            )

        report["status"] = "PASSED"

    except Exception as error:
        report["error"] = str(error)

    finally:
        report["recovery_time_seconds"] = round(
            time.monotonic() - start_time, 2
        )
        report["finished_at"] = datetime.now(timezone.utc).isoformat()

        try:
            publish_recovery_metrics(report)
            report["metrics_published"] = True
        except Exception as error:
            report["metrics_published"] = False
            report["metrics_error"] = str(error)

        report_path = REPORT_DIR / "latest_recovery_report.json"

        report_path.write_text(
            json.dumps(report, indent=4),
            encoding="utf-8"
        )

        print(json.dumps(report, indent=4))
        print(f"\nReport saved to: {report_path}")

        if report["status"] != "PASSED":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
