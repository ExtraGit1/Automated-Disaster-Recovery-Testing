
import json
import subprocess
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle
)

PROJECT_DIR = Path(__file__).resolve().parent
REPORT_DIR = PROJECT_DIR / "reports"

HEALTH_URL = "http://127.0.0.1:5000/health"
RECORDS_URL = "http://127.0.0.1:5000/records"
RECOVERY_RESULT_URL = "http://127.0.0.1:5000/recovery-result"

EXPECTED_RECORD_ID = 1
RECOVERY_TIMEOUT = 60


def log(message):
    timestamp = datetime.now().astimezone().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def run_docker(*args):
    result = subprocess.run(
        ["docker", "compose", *args],
        cwd=PROJECT_DIR,
        capture_output=True,
        text=True,
        timeout=30
    )

    if result.returncode != 0:
        raise RuntimeError(
            result.stderr.strip() or result.stdout.strip()
        )

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


def wait_for_recovery(timeout_seconds=RECOVERY_TIMEOUT):
    deadline = time.monotonic() + timeout_seconds
    attempt = 0

    while time.monotonic() < deadline:
        attempt += 1

        if check_health():
            return True

        log(f"Waiting for recovery... attempt {attempt}")
        time.sleep(2)

    return False


def publish_recovery_metrics(report):
    payload = {
        "result": report["status"].lower(),
        "duration_seconds": report["recovery_time_seconds"],
        "data_persisted": report.get("data_persisted", False)
    }

    request_data = json.dumps(payload).encode("utf-8")

    request = urllib.request.Request(
        RECOVERY_RESULT_URL,
        data=request_data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )

    with urllib.request.urlopen(request, timeout=5) as response:
        return json.loads(response.read().decode())


def generate_pdf_report(report, pdf_path):
    document = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        rightMargin=20 * mm,
        leftMargin=20 * mm,
        topMargin=20 * mm,
        bottomMargin=20 * mm
    )

    styles = getSampleStyleSheet()
    content = []

    content.append(Paragraph(
        "Automated Disaster Recovery Test Report",
        styles["Title"]
    ))
    content.append(Spacer(1, 8))

    content.append(Paragraph(
        "Test Summary",
        styles["Heading2"]
    ))
    content.append(Spacer(1, 6))

    status = str(report.get("status", "FAILED"))
    status_color = (
        "#16803C" if status == "PASSED" else "#C62828"
    )

    content.append(Paragraph(
        f'<font color="{status_color}"><b>'
        f"FINAL RESULT: {escape(status)}"
        f"</b></font>",
        styles["Heading2"]
    ))
    content.append(Spacer(1, 12))

    fields = [
        ("Test Name", "test"),
        ("Started At (UTC)", "started_at"),
        ("Finished At (UTC)", "finished_at"),
        ("Failure Simulated", "failure_simulated"),
        ("Recovery Completed", "recovery_completed"),
        ("Data Persisted", "data_persisted"),
        ("Baseline Record ID", "baseline_record_id"),
        ("Recovery Time (seconds)", "recovery_time_seconds"),
        ("Metrics Published", "metrics_published"),
        ("Error", "error"),
        ("Metrics Error", "metrics_error")
    ]

    rows = [["Field", "Result"]]

    for label, key in fields:
        if key in report:
            value = escape(str(report[key]))
            rows.append([
                label,
                Paragraph(value, styles["BodyText"])
            ])

    table = Table(
        rows,
        colWidths=[55 * mm, 105 * mm],
        repeatRows=1
    )

    table.setStyle(TableStyle([
        (
            "BACKGROUND", (0, 0), (-1, 0),
            colors.HexColor("#1D4ED8")
        ),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        (
            "BACKGROUND", (0, 1), (-1, -1),
            colors.whitesmoke
        )
    ]))

    content.append(table)
    content.append(Spacer(1, 16))

    content.append(Paragraph(
        "Test Description",
        styles["Heading2"]
    ))
    content.append(Paragraph(
        "The test stops and restarts the PostgreSQL container, "
        "checks application recovery, verifies that the baseline "
        "record remains available, measures recovery time, and "
        "publishes monitoring metrics.",
        styles["BodyText"]
    ))

    document.build(content)


def main():
    REPORT_DIR.mkdir(exist_ok=True)

    report = {
        "test": "Automated Disaster Recovery Testing",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "FAILED",
        "failure_simulated": False,
        "recovery_completed": False,
        "data_persisted": False,
        "metrics_published": False
    }

    recovery_start = None

    print("\n" + "=" * 58)
    print("       AUTOMATED DISASTER RECOVERY TEST")
    print("=" * 58)

    try:
        log("[1/6] CHECKING APPLICATION HEALTH")

        if not check_health():
            raise RuntimeError(
                "Application/database is not healthy before the test."
            )

        log("PASS: Application and database are healthy.")

        log("[2/6] CHECKING BASELINE DATABASE RECORD")

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

        log(
            f"PASS: Baseline record ID "
            f"{EXPECTED_RECORD_ID} exists."
        )

        print("\n" + "-" * 58)
        print("          FAILURE INITIATED")
        print("-" * 58)

        log("Action: Stopping PostgreSQL container.")
        log("Command: docker compose stop db")

        # Measure from the beginning of failure simulation
        # until the application becomes healthy again.
        recovery_start = time.monotonic()

        run_docker("stop", "db")
        report["failure_simulated"] = True

        log("FAILURE SIMULATED: PostgreSQL container stopped.")

        print("\n" + "-" * 58)
        print("          RECOVERY INITIATED")
        print("-" * 58)

        log("Action: Restarting PostgreSQL container.")
        log("Command: docker compose start db")

        run_docker("start", "db")

        log("Database start command completed.")
        log("[3/6] WAITING FOR APPLICATION RECOVERY")

        report["recovery_completed"] = wait_for_recovery()

        if not report["recovery_completed"]:
            raise RuntimeError(
                f"Application/database did not recover within "
                f"{RECOVERY_TIMEOUT} seconds."
            )

        report["recovery_time_seconds"] = round(
            time.monotonic() - recovery_start, 2
        )

        log("RECOVERY SUCCESSFUL: Application is healthy again.")
        log(
            f"MEASURED RECOVERY TIME: "
            f"{report['recovery_time_seconds']:.2f} seconds"
        )

        log("[4/6] VERIFYING DATA PERSISTENCE")

        records_after = get_records()

        report["data_persisted"] = any(
            record["id"] == EXPECTED_RECORD_ID
            for record in records_after
        )

        if not report["data_persisted"]:
            raise RuntimeError(
                "Baseline record was missing after recovery."
            )

        log(
            f"PASS: Original record ID {EXPECTED_RECORD_ID} "
            "still exists after recovery."
        )

        report["status"] = "PASSED"

    except Exception as error:
        report["error"] = str(error)
        log(f"TEST ERROR: {error}")

    finally:
        if "recovery_time_seconds" not in report:
            if recovery_start is not None:
                report["recovery_time_seconds"] = round(
                    time.monotonic() - recovery_start, 2
                )
            else:
                report["recovery_time_seconds"] = 0.0

        report["finished_at"] = datetime.now(timezone.utc).isoformat()

        log("[5/6] PUBLISHING RECOVERY METRICS")

        try:
            publish_recovery_metrics(report)
            report["metrics_published"] = True
            log("PASS: Metrics published to Flask.")
        except Exception as error:
            report["metrics_error"] = str(error)
            log(f"WARNING: Metrics publishing failed: {error}")

        # Save the JSON report.
        json_path = REPORT_DIR / "latest_recovery_report.json"

        json_path.write_text(
            json.dumps(report, indent=4),
            encoding="utf-8"
        )

        log(f"JSON report saved: {json_path}")

        # Generate the PDF from the same report data.
        pdf_path = REPORT_DIR / "latest_recovery_report.pdf"

        log("[6/6] GENERATING PDF REPORT")

        try:
            generate_pdf_report(report, pdf_path)
            log(f"PDF report saved: {pdf_path}")
        except Exception as error:
            report["pdf_generated"] = False
            report["pdf_error"] = str(error)
            log(f"WARNING: PDF generation failed: {error}")

            # Update JSON so the PDF-generation error is recorded.
            json_path.write_text(
                json.dumps(report, indent=4),
                encoding="utf-8"
            )
        else:
            report["pdf_generated"] = True

            # Include the PDF-generation status in the JSON report.
            json_path.write_text(
                json.dumps(report, indent=4),
                encoding="utf-8"
            )

        print("\n" + "=" * 58)

        if report["status"] == "PASSED":
            print("          FINAL RESULT: PASSED")
        else:
            print("          FINAL RESULT: FAILED")
            print(f"Reason: {report.get('error', 'Unknown error')}")

        print(
            f"Recovery Time: "
            f"{report['recovery_time_seconds']:.2f} seconds"
        )
        print(f"Data Persisted: {report['data_persisted']}")
        print(f"Metrics Published: {report['metrics_published']}")
        print(f"PDF Generated: {report.get('pdf_generated', False)}")
        print("=" * 58)

        print("\nJSON report:", json_path)
        print("PDF report: ", pdf_path)

        if report["status"] != "PASSED":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
