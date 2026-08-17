"""PDF generation for exam result reports, using reportlab (pure Python, no compiled toolchain
needed unlike the face/OCR deps).
"""
from datetime import datetime
from io import BytesIO

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

BRAND_COLOR = colors.HexColor("#2563eb")
MUTED_COLOR = colors.HexColor("#64748b")
BORDER_COLOR = colors.HexColor("#e2e8f0")


def build_result_report(*, student_name: str, roll_number: str | None, exam_title: str, attempt_id: int,
                         started_at: datetime, submitted_at: datetime | None, status: str,
                         total_marks: int, scored_marks: int, percentage: float,
                         correct_count: int, incorrect_count: int, unattempted_count: int,
                         violation_count: int) -> bytes:
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    width, height = A4
    margin = 20 * mm

    # Header
    c.setFillColor(BRAND_COLOR)
    c.setFont("Helvetica-Bold", 20)
    c.drawString(margin, height - margin, "AI Exam Proctor")
    c.setFillColor(MUTED_COLOR)
    c.setFont("Helvetica", 11)
    c.drawString(margin, height - margin - 18, "Exam Result Report")
    c.setStrokeColor(BORDER_COLOR)
    c.line(margin, height - margin - 28, width - margin, height - margin - 28)

    # Meta block
    y = height - margin - 55
    c.setFillColor(colors.black)
    c.setFont("Helvetica-Bold", 13)
    c.drawString(margin, y, exam_title)
    y -= 20

    meta_rows = [
        ("Student", student_name),
        ("Roll Number", roll_number or "-"),
        ("Attempt ID", str(attempt_id)),
        ("Status", status.replace("_", " ").title()),
        ("Started", started_at.strftime("%Y-%m-%d %H:%M UTC")),
        ("Submitted", submitted_at.strftime("%Y-%m-%d %H:%M UTC") if submitted_at else "-"),
    ]
    c.setFont("Helvetica", 10)
    for label, value in meta_rows:
        c.setFillColor(MUTED_COLOR)
        c.drawString(margin, y, label)
        c.setFillColor(colors.black)
        c.drawString(margin + 110, y, str(value))
        y -= 16

    # Score summary box
    y -= 14
    box_top = y
    box_height = 70
    c.setStrokeColor(BORDER_COLOR)
    c.roundRect(margin, box_top - box_height, width - 2 * margin, box_height, 6)

    c.setFillColor(BRAND_COLOR)
    c.setFont("Helvetica-Bold", 26)
    c.drawString(margin + 16, box_top - 32, f"{scored_marks}/{total_marks}")
    c.setFont("Helvetica-Bold", 14)
    c.drawString(margin + 16, box_top - 52, f"{percentage:.2f}%")

    stat_x = margin + 220
    c.setFillColor(colors.black)
    c.setFont("Helvetica", 10)
    for label, value in (("Correct", correct_count), ("Incorrect", incorrect_count), ("Unattempted", unattempted_count)):
        c.setFillColor(MUTED_COLOR)
        c.drawString(stat_x, box_top - 24, label)
        c.setFillColor(colors.black)
        c.setFont("Helvetica-Bold", 12)
        c.drawString(stat_x, box_top - 40, str(value))
        c.setFont("Helvetica", 10)
        stat_x += 110

    y = box_top - box_height - 30

    # Proctoring note
    c.setFillColor(MUTED_COLOR)
    c.setFont("Helvetica", 9)
    if violation_count:
        c.drawString(margin, y, f"AI proctoring logged {violation_count} violation(s) during this attempt. See the exam's violation log for details.")
    else:
        c.drawString(margin, y, "No proctoring violations were logged during this attempt.")

    # Footer
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(MUTED_COLOR)
    c.drawString(margin, margin / 2, f"Generated {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')} - AI Exam Proctor")

    c.showPage()
    c.save()
    buffer.seek(0)
    return buffer.getvalue()
