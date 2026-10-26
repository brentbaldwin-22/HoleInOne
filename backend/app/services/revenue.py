"""Who paid us, at which course, and whose money it is.

Every entry is one `Participant` row. The money attached to it is not
stored anywhere — there is no price column — so revenue is always
`count x settings.registration_price_cents`, computed at read time. That
is deliberate: the price has changed before and will change again, and a
historical price frozen into 600 rows is a migration nobody wants. The
consequence is that this report prices history at TODAY's price. Say so
on the sheet rather than pretending otherwise.

THE SPLIT IS COMPUTED ONCE AND SUBTRACTED, NEVER ROUNDED TWICE. The
course's 25% is the half that gets written on a cheque, so that is the
half that gets rounded; ours is whatever is left. Round both and a
hundred entries will eventually disagree with the gross by a cent, and
the cent will be found by the person we are splitting with.
"""

from __future__ import annotations

import io
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Course, Participant, TeeTime


def _parse_day(value: str | None, field: str) -> date | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"{field} must be YYYY-MM-DD, got {value!r}")


def _status(p: Participant) -> str:
    """Paid / Refunded / Unpaid. Refunded wins over paid: the money came
    in and then went back out, so it is not ours and it is not the
    course's either."""
    if p.refunded_at is not None:
        return "Refunded"
    return "Paid" if p.paid else "Unpaid"


def report(
    db: Session,
    start: str | None = None,
    end: str | None = None,
    course_id: int | None = None,
) -> dict:
    """The whole report as plain data, so the JSON route and the
    spreadsheet route cannot disagree about a number.

    `start`/`end` are inclusive calendar days against `Participant.created_at`
    — when they signed up and paid, not when they played. A round booked in
    March for an April tee time is March's revenue.
    """
    d_start = _parse_day(start, "start")
    d_end = _parse_day(end, "end")
    if d_start and d_end and d_end < d_start:
        raise ValueError("end is before start")

    q = (
        db.query(Participant, Course, TeeTime)
        .join(TeeTime, Participant.tee_time_id == TeeTime.id)
        .join(Course, TeeTime.course_id == Course.id)
    )
    if d_start:
        q = q.filter(Participant.created_at >= datetime.combine(d_start, datetime.min.time()))
    if d_end:
        # Inclusive end: everything strictly before midnight the next day.
        q = q.filter(
            Participant.created_at
            < datetime.combine(d_end + timedelta(days=1), datetime.min.time())
        )
    if course_id:
        q = q.filter(Course.id == course_id)

    rows = q.order_by(Participant.created_at.asc(), Participant.id.asc()).all()

    price_cents = int(settings.registration_price_cents)
    course_pct = int(settings.course_revenue_share_pct)

    def _split(paid_count: int) -> tuple[int, int, int]:
        gross = paid_count * price_cents
        course_cut = round(gross * course_pct / 100.0)
        return gross, gross - course_cut, course_cut

    by_course: dict[int, dict] = {}
    by_month: dict[tuple[str, int], dict] = {}
    signups: list[dict] = []

    for p, c, tt in rows:
        st = _status(p)
        billable = 1 if st == "Paid" else 0

        bucket = by_course.setdefault(
            c.id,
            {
                "course_id": c.id,
                "course": c.name,
                "location": c.location or "",
                "signups": 0,
                "paid": 0,
                "refunded": 0,
                "unpaid": 0,
            },
        )
        bucket["signups"] += 1
        bucket[st.lower()] += 1

        month = p.created_at.strftime("%Y-%m") if p.created_at else "unknown"
        mb = by_month.setdefault(
            (month, c.id),
            {"month": month, "course_id": c.id, "course": c.name, "paid": 0, "signups": 0},
        )
        mb["signups"] += 1
        mb["paid"] += billable

        signups.append(
            {
                "participant_id": p.id,
                "signed_up": p.created_at.isoformat() if p.created_at else "",
                "course": c.name,
                "hole_date": tt.starts_at.isoformat() if tt.starts_at else "",
                "name": p.name or "",
                "email": p.email or "",
                "mobile": p.mobile or "",
                "status": st,
                "billable": billable,
                "stripe_payment_intent_id": p.stripe_payment_intent_id or "",
            }
        )

    course_rows = sorted(by_course.values(), key=lambda r: (-r["paid"], r["course"].lower()))
    for r in course_rows:
        g, ours, theirs = _split(r["paid"])
        r["gross_cents"], r["golfreelz_cents"], r["course_cents"] = g, ours, theirs

    month_rows = sorted(by_month.values(), key=lambda r: (r["month"], r["course"].lower()))
    for r in month_rows:
        g, ours, theirs = _split(r["paid"])
        r["gross_cents"], r["golfreelz_cents"], r["course_cents"] = g, ours, theirs

    tot_paid = sum(r["paid"] for r in course_rows)
    t_gross, t_ours, t_theirs = _split(tot_paid)

    if d_start or d_end:
        period = f"{d_start or 'the beginning'} to {d_end or 'today'}"
    else:
        period = "All time"

    return {
        "period": period,
        "start": start or None,
        "end": end or None,
        "generated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "price_cents": price_cents,
        "course_share_pct": course_pct,
        "golfreelz_share_pct": 100 - course_pct,
        "totals": {
            "signups": sum(r["signups"] for r in course_rows),
            "paid": tot_paid,
            "refunded": sum(r["refunded"] for r in course_rows),
            "unpaid": sum(r["unpaid"] for r in course_rows),
            "gross_cents": t_gross,
            "golfreelz_cents": t_ours,
            "course_cents": t_theirs,
        },
        "by_course": course_rows,
        "by_month": month_rows,
        "signups": signups,
    }


# --------------------------------------------------------------------------
# Spreadsheet
# --------------------------------------------------------------------------
# The money columns are Excel FORMULAS, not baked numbers. The price and
# the split live in two cells on the Summary sheet and everything else
# points at them, so changing the entry price to $25 re-totals the whole
# workbook instead of needing a new download. The counts are the only
# hard numbers in it, which is the right place for the database to have
# the last word.

_MONEY = '"$"#,##0.00'
_HEADER_FILL = "FF133D28"


def workbook_bytes(rep: dict) -> bytes:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError as exc:  # pragma: no cover - deploy-order guard
        raise RuntimeError(
            "openpyxl is not installed on this server — add it to "
            "backend/requirements.txt and redeploy"
        ) from exc

    wb = Workbook()
    bold = Font(bold=True)
    head_font = Font(bold=True, color="FFFFFFFF")
    head_fill = PatternFill("solid", fgColor=_HEADER_FILL)

    def _head(ws, row: int, labels: list[str]) -> None:
        for i, label in enumerate(labels, start=1):
            cell = ws.cell(row=row, column=i, value=label)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.freeze_panes = ws.cell(row=row + 1, column=1)

    def _widths(ws, widths: list[int]) -> None:
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(i)].width = w

    # ---------------- Summary ----------------
    ws = wb.active
    ws.title = "Summary"

    ws["A1"] = "Golf Reelz — revenue by course"
    ws["A1"].font = Font(bold=True, size=15, color=_HEADER_FILL[2:])
    ws["A2"], ws["B2"] = "Period", rep["period"]
    ws["A3"], ws["B3"] = "Entry price", rep["price_cents"] / 100.0
    ws["B3"].number_format = _MONEY
    ws["A4"], ws["B4"] = "Course share", rep["course_share_pct"] / 100.0
    ws["B4"].number_format = "0%"
    ws["A5"], ws["B5"] = "Golf Reelz share", "=1-B4"
    ws["B5"].number_format = "0%"
    ws["A6"], ws["B6"] = "Generated (UTC)", rep["generated_at"]
    ws["C3"] = "<- change this and the whole workbook re-totals"
    ws["C3"].font = Font(italic=True, size=9, color="FF8C8878")
    for r in range(2, 7):
        ws[f"A{r}"].font = bold

    hdr = 8
    _head(ws, hdr, [
        "Course", "Location", "Signups", "Paid", "Refunded", "Unpaid",
        "Gross", f"Golf Reelz ({rep['golfreelz_share_pct']}%)",
        f"Course ({rep['course_share_pct']}%)",
    ])
    r = hdr + 1
    first = r
    for row in rep["by_course"]:
        ws.cell(row=r, column=1, value=row["course"])
        ws.cell(row=r, column=2, value=row["location"])
        ws.cell(row=r, column=3, value=row["signups"])
        ws.cell(row=r, column=4, value=row["paid"])
        ws.cell(row=r, column=5, value=row["refunded"])
        ws.cell(row=r, column=6, value=row["unpaid"])
        ws.cell(row=r, column=7, value=f"=D{r}*$B$3").number_format = _MONEY
        # Course cut rounded, ours the remainder — see module docstring.
        ws.cell(row=r, column=9, value=f"=ROUND(G{r}*$B$4,2)").number_format = _MONEY
        ws.cell(row=r, column=8, value=f"=G{r}-I{r}").number_format = _MONEY
        r += 1
    last = r - 1

    if rep["by_course"]:
        ws.cell(row=r, column=1, value="TOTAL").font = bold
        for col in range(3, 10):
            L = get_column_letter(col)
            c = ws.cell(row=r, column=col, value=f"=SUM({L}{first}:{L}{last})")
            c.font = bold
            if col >= 7:
                c.number_format = _MONEY
    else:
        ws.cell(row=r, column=1, value="No signups in this period.").font = Font(italic=True)
    _widths(ws, [30, 24, 10, 9, 11, 10, 14, 16, 16])

    # ---------------- By month ----------------
    ws = wb.create_sheet("By month")
    _head(ws, 1, [
        "Month", "Course", "Signups", "Paid", "Gross",
        f"Golf Reelz ({rep['golfreelz_share_pct']}%)",
        f"Course ({rep['course_share_pct']}%)",
    ])
    r = 2
    for row in rep["by_month"]:
        ws.cell(row=r, column=1, value=row["month"])
        ws.cell(row=r, column=2, value=row["course"])
        ws.cell(row=r, column=3, value=row["signups"])
        ws.cell(row=r, column=4, value=row["paid"])
        ws.cell(row=r, column=5, value=f"=D{r}*Summary!$B$3").number_format = _MONEY
        ws.cell(row=r, column=7, value=f"=ROUND(E{r}*Summary!$B$4,2)").number_format = _MONEY
        ws.cell(row=r, column=6, value=f"=E{r}-G{r}").number_format = _MONEY
        r += 1
    if not rep["by_month"]:
        ws.cell(row=2, column=1, value="No signups in this period.").font = Font(italic=True)
    _widths(ws, [12, 30, 10, 9, 14, 16, 16])

    # ---------------- Signups ----------------
    # The evidence behind the Summary. One row per entry, so any number
    # above can be traced to the people it came from.
    ws = wb.create_sheet("Signups")
    _head(ws, 1, [
        "Signed up", "Course", "Tee time", "Name", "Email", "Mobile",
        "Status", "Billable", "Entry",
        f"Golf Reelz ({rep['golfreelz_share_pct']}%)",
        f"Course ({rep['course_share_pct']}%)", "Stripe payment intent",
    ])
    r = 2
    for row in rep["signups"]:
        ws.cell(row=r, column=1, value=row["signed_up"][:19].replace("T", " "))
        ws.cell(row=r, column=2, value=row["course"])
        ws.cell(row=r, column=3, value=row["hole_date"][:16].replace("T", " "))
        ws.cell(row=r, column=4, value=row["name"])
        ws.cell(row=r, column=5, value=row["email"])
        ws.cell(row=r, column=6, value=row["mobile"])
        ws.cell(row=r, column=7, value=row["status"])
        ws.cell(row=r, column=8, value=row["billable"])
        ws.cell(row=r, column=9, value=f"=H{r}*Summary!$B$3").number_format = _MONEY
        ws.cell(row=r, column=11, value=f"=ROUND(I{r}*Summary!$B$4,2)").number_format = _MONEY
        ws.cell(row=r, column=10, value=f"=I{r}-K{r}").number_format = _MONEY
        ws.cell(row=r, column=12, value=row["stripe_payment_intent_id"])
        r += 1
    if not rep["signups"]:
        ws.cell(row=2, column=1, value="No signups in this period.").font = Font(italic=True)
    _widths(ws, [19, 26, 18, 22, 28, 15, 10, 9, 12, 16, 16, 30])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def filename(rep: dict) -> str:
    if rep["start"] or rep["end"]:
        span = f"{rep['start'] or 'start'}_to_{rep['end'] or 'today'}"
    else:
        span = "all-time"
    return f"golfreelz-revenue_{span}.xlsx"
