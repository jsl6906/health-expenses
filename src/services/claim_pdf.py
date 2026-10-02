"""Render a `ClaimForm` as a PDF that mirrors the MHBP / Aetna medical claim form.

The layout reproduces every section of the paper form (member, patient,
accident, Medicare, other coverage, authorization, assignment of benefits and
the provider statement of services) while allowing any number of service
lines.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from io import BytesIO
from typing import Optional

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.lib.utils import ImageReader
from reportlab.platypus import (
    Image,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

try:
    from src.schemas.claims import ClaimForm, ClaimSignature  # type: ignore
except ImportError:  # pragma: no cover
    from ..schemas.claims import ClaimForm, ClaimSignature  # type: ignore

_styles = getSampleStyleSheet()
BODY = ParagraphStyle("claim_body", parent=_styles["Normal"], fontSize=9, leading=11)
SMALL = ParagraphStyle("claim_small", parent=BODY, fontSize=7.5, leading=9)
LABEL = ParagraphStyle(
    "claim_label", parent=BODY, fontSize=7, leading=8, textColor=colors.HexColor("#444444")
)
VALUE = ParagraphStyle("claim_value", parent=BODY, fontSize=9.5, leading=11)
RIGHT = ParagraphStyle("claim_right", parent=BODY, alignment=TA_RIGHT)
TITLE = ParagraphStyle(
    "claim_title", parent=_styles["Heading1"], fontSize=15, leading=18, spaceAfter=2
)
SECTION = ParagraphStyle(
    "claim_section", parent=_styles["Heading3"], fontSize=10, leading=12, spaceBefore=0
)

GREY = colors.HexColor("#d9d9d9")
BORDER = colors.HexColor("#555555")


def _fmt_date(d: Optional[date]) -> str:
    return d.strftime("%m/%d/%Y") if d else ""


def _fmt_money(v: Optional[Decimal]) -> str:
    if v is None:
        return ""
    return f"${Decimal(v):,.2f}"


def _fmt_phone(p: Optional[str]) -> str:
    if not p:
        return ""
    digits = "".join(ch for ch in str(p) if ch.isdigit())
    if len(digits) == 10:
        return f"({digits[:3]}) {digits[3:6]}-{digits[6:]}"
    return str(p)


def _esc(v) -> str:
    if v is None:
        return ""
    return (
        str(v)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br/>")
    )


def _cell(label: str, value) -> list:
    """A labelled field cell: small grey label above the value."""
    return [Paragraph(_esc(label), LABEL), Paragraph(_esc(value) or "&nbsp;", VALUE)]


def _check(checked: bool, label: str) -> str:
    box = "&#9746;" if checked else "&#9744;"
    return f"{box} {_esc(label)}"


def _section_header(title: str, note: str = "") -> Table:
    text = f"<b>{_esc(title)}</b>"
    if note:
        text += f" &nbsp;<font size=8>{_esc(note)}</font>"
    t = Table([[Paragraph(text, BODY)]], colWidths=[7.0 * inch])
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), GREY),
                ("BOX", (0, 0), (-1, -1), 0.75, BORDER),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    return t


def _grid(rows: list[list], col_widths: list[float]) -> Table:
    t = Table(rows, colWidths=col_widths)
    t.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return t


def _signature_flowable(
    sig: ClaimSignature, image: Optional[bytes], placeholder: str
) -> list:
    if sig.include_signature and image:
        reader = ImageReader(BytesIO(image))
        iw, ih = reader.getSize()
        max_w, max_h = 2.6 * inch, 0.6 * inch
        scale = min(max_w / iw, max_h / ih, 1.0 if iw <= max_w else max_w / iw)
        img = Image(BytesIO(image), width=iw * scale, height=ih * scale)
        img.hAlign = "LEFT"
        return [img, Paragraph(_esc(sig.signer_name), SMALL)]
    if sig.include_signature:
        return [
            Spacer(1, 0.45 * inch),
            Paragraph(
                f"{_esc(sig.signer_name)} <font color='#888888'>(signature not on file)</font>",
                SMALL,
            ),
        ]
    return [Spacer(1, 0.3 * inch), Paragraph(_esc(placeholder), SMALL)]


def _signature_block(
    title: str,
    statement: str,
    sig: ClaimSignature,
    image: Optional[bytes],
    placeholder: str,
) -> list:
    sig_date = _fmt_date(sig.signature_date) if sig.include_signature else ""
    rows = [
        [Paragraph(_esc(statement), SMALL), ""],
        [
            [Paragraph("Patient or authorized person's signature", LABEL)]
            + _signature_flowable(sig, image, placeholder),
            _cell("Date", sig_date),
        ],
    ]
    t = _grid(rows, [5.4 * inch, 1.6 * inch])
    t.setStyle(TableStyle([("SPAN", (0, 0), (1, 0))]))
    return [KeepTogether([_section_header(title), t]), Spacer(1, 6)]


def render_claim_pdf(form: ClaimForm, signature_image: Optional[bytes] = None) -> bytes:
    buf = BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
        title=f"Medical Claim Form - {form.insurance.plan_name or ''}",
        author=form.member.first_name or "",
    )
    story: list = []
    m, p, ps = form.member, form.patient, form.provider_statement

    # ---- Header ----
    header_right = []
    if form.insurance.claim_mailing_address:
        header_right.append(Paragraph(_esc(form.insurance.claim_mailing_address), RIGHT))
    if form.insurance.claim_email:
        header_right.append(Paragraph(_esc(form.insurance.claim_email), RIGHT))
    header = Table(
        [
            [
                [
                    Paragraph(_esc(form.insurance.plan_name or "Insurance Plan"), TITLE),
                    Paragraph("Medical Claim Form", SECTION),
                ],
                header_right or "",
            ]
        ],
        colWidths=[4.5 * inch, 2.5 * inch],
    )
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [header, Spacer(1, 6)]

    # ---- Member Information ----
    story.append(_section_header("Member Information"))
    story.append(
        _grid(
            [
                [
                    _cell("Last Name", m.last_name),
                    _cell("First", m.first_name),
                    _cell("MI", m.middle_initial),
                    _cell("Member ID Number", m.member_id),
                ],
                [
                    _cell("Address", m.address),
                    "",
                    "",
                    _cell("Phone", _fmt_phone(m.phone)),
                ],
            ],
            [2.3 * inch, 2.0 * inch, 0.5 * inch, 2.2 * inch],
        )
    )
    story[-1].setStyle(TableStyle([("SPAN", (0, 1), (2, 1))]))
    if form.insurance.group_number:
        story.append(
            Paragraph(f"Group Number: {_esc(form.insurance.group_number)}", SMALL)
        )
    story.append(Spacer(1, 6))

    # ---- Patient Information ----
    story.append(
        _section_header(
            "Patient Information",
            "Complete this section only if claim is for a qualified dependent.",
        )
    )
    if p.is_dependent:
        story.append(
            _grid(
                [
                    [
                        _cell("Last Name", p.last_name),
                        _cell("First", p.first_name),
                        _cell("MI", p.middle_initial),
                    ],
                    [
                        _cell("Patient ID", p.patient_id),
                        _cell("Date of Birth", _fmt_date(p.dob)),
                        _cell("Relationship", p.relationship),
                    ],
                ],
                [3.0 * inch, 2.6 * inch, 1.4 * inch],
            )
        )
    else:
        story.append(
            _grid(
                [[Paragraph(_check(True, "Patient is the member (Self)"), BODY)]],
                [7.0 * inch],
            )
        )
    story.append(Spacer(1, 6))

    # ---- Accident Information ----
    a = form.accident
    story.append(
        _section_header(
            "Accident Information",
            "Complete this section only if claim is result of accident or work-related illness or injury.",
        )
    )
    if a.is_accident:
        emp = (
            "Yes"
            if a.employment_related
            else ("No" if a.employment_related is False else "")
        )
        story.append(
            _grid(
                [
                    [
                        _cell(
                            "Date of accident or first symptoms of illness?",
                            _fmt_date(a.accident_date),
                        ),
                        _cell("Where did the accident occur? (City/State)", a.location),
                    ],
                    [
                        [
                            Paragraph("Is accident/illness related to employment?", LABEL),
                            Paragraph(
                                f"{_esc(emp)} &nbsp; If no: {_check(a.auto, 'Auto')} &nbsp; {_check(a.other, 'Other')}",
                                VALUE,
                            ),
                        ],
                        _cell("Describe the accident or illness.", a.description),
                    ],
                    [
                        _cell(
                            "Give date patient first consulted physician.",
                            _fmt_date(a.first_consulted_date),
                        ),
                        [
                            Paragraph(
                                "Has patient ever had same or similar symptoms?", LABEL
                            ),
                            Paragraph(
                                f"{_check(a.similar_symptoms is True, 'Yes')} &nbsp; {_check(a.similar_symptoms is False, 'No')}",
                                VALUE,
                            ),
                        ],
                    ],
                ],
                [3.5 * inch, 3.5 * inch],
            )
        )
    else:
        story.append(
            _grid(
                [
                    [
                        Paragraph(
                            _check(
                                True,
                                "Not applicable - claim is not the result of an accident or work-related illness or injury.",
                            ),
                            BODY,
                        )
                    ]
                ],
                [7.0 * inch],
            )
        )
    story.append(Spacer(1, 6))

    # ---- Medicare Information ----
    md = form.medicare
    story.append(
        _section_header(
            "Medicare Information",
            "Complete this section only if patient is eligible for Medicare.",
        )
    )
    if md.eligible:
        story.append(
            _grid(
                [
                    [
                        _cell(
                            "Medicare Number (include any alpha characters)",
                            md.medicare_number,
                        ),
                        _cell("Effective Date (Part A)", _fmt_date(md.part_a_date)),
                        _cell("Effective Date (Part B)", _fmt_date(md.part_b_date)),
                    ]
                ],
                [3.4 * inch, 1.8 * inch, 1.8 * inch],
            )
        )
        story.append(
            Paragraph(
                "Copy of the Medicare Explanation of Benefits statement attached.", SMALL
            )
        )
    else:
        story.append(
            _grid(
                [
                    [
                        Paragraph(
                            _check(True, "Not applicable - patient is not eligible for Medicare."),
                            BODY,
                        )
                    ]
                ],
                [7.0 * inch],
            )
        )
    story.append(Spacer(1, 6))

    # ---- Other Health Insurance ----
    o = form.other_insurance
    story.append(
        _section_header(
            "Other Health Insurance",
            "If Yes, complete section below or claim cannot be processed.",
        )
    )
    story.append(
        _grid([[Paragraph(_check(o.no_other_coverage, "No other coverage"), BODY)]], [7.0 * inch])
    )
    if not o.no_other_coverage:
        story.append(
            _grid(
                [
                    [
                        _cell("Name of Policyholder", o.policyholder_name),
                        _cell("Policy Number", o.policy_number),
                        "",
                        "",
                    ],
                    [
                        _cell("Name of Insurance Company", o.company),
                        _cell("Insurance Company Phone Number", _fmt_phone(o.phone)),
                        "",
                        "",
                    ],
                    [
                        _cell("Street Address", o.address),
                        _cell("City", o.city),
                        _cell("State", o.state),
                        _cell("ZIP", o.zip),
                    ],
                ],
                [3.5 * inch, 1.8 * inch, 0.7 * inch, 1.0 * inch],
            )
        )
        story[-1].setStyle(
            TableStyle([("SPAN", (1, 0), (3, 0)), ("SPAN", (1, 1), (3, 1))])
        )
    story.append(Spacer(1, 6))

    # ---- Authorization / Release ----
    story += _signature_block(
        "Authorization/Release of Information",
        "I authorize any insurance company, organization, employer, hospital physician, pharmacist or other "
        "health care provider to release any information requested with regard to this claim and the expenses "
        "reported. I certify that the information furnished in conjunction with this claim is true and correct. "
        "I know it is a crime to fill out this form with facts I know are false or to omit facts I know are important.",
        form.authorization,
        signature_image,
        "",
    )

    # ---- Assignment of Benefits ----
    story += _signature_block(
        "Assignment of Benefits",
        "I agree to assign benefits directly to the provider of services.",
        form.assignment_of_benefits,
        signature_image,
        "Not signed - benefits are NOT assigned to the provider; reimburse the member.",
    )

    # ---- Provider Statement (page 2) ----
    story.append(PageBreak())
    story.append(
        Paragraph(
            "<b>THIS SECTION FOR PHYSICIAN OR SUPPLIER ONLY.</b><br/>"
            "<font size=8>If a detailed statement is available, please attach.</font>",
            ParagraphStyle("c", parent=BODY, alignment=1),
        )
    )
    story.append(Spacer(1, 4))
    story.append(_section_header("Provider Statement of Services Rendered"))
    story.append(
        _grid(
            [
                [
                    _cell(
                        "Name and address of facility where services were rendered (if other than home or office)",
                        ps.facility_name_address,
                    ),
                    _cell("Date Admitted", _fmt_date(ps.date_admitted)),
                ],
                ["", _cell("Date Discharged", _fmt_date(ps.date_discharged))],
            ],
            [5.4 * inch, 1.6 * inch],
        )
    )
    story[-1].setStyle(TableStyle([("SPAN", (0, 0), (0, 1))]))

    codes = ps.diagnosis_codes or [""]
    half = (len(codes) + 1) // 2
    left, right = codes[:half], codes[half:]
    dx_rows = []
    for i in range(half):
        l = f"{i + 1}. {_esc(left[i])}" if i < len(left) else ""
        r = f"{half + i + 1}. {_esc(right[i])}" if i < len(right) else ""
        dx_rows.append([Paragraph(l, VALUE), Paragraph(r, VALUE)])
    dx = Table(
        [[Paragraph("Diagnosis Code and Description", LABEL), ""]] + dx_rows,
        colWidths=[3.5 * inch, 3.5 * inch],
    )
    dx.setStyle(
        TableStyle(
            [
                ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
                ("SPAN", (0, 0), (1, 0)),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 1),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )
    story.append(dx)

    head = [
        Paragraph("Date of Service (from/to)", LABEL),
        Paragraph("Place of Service", LABEL),
        Paragraph("CPT-4 Procedure Code", LABEL),
        Paragraph("Description of Service", LABEL),
        Paragraph("Charges", LABEL),
        Paragraph("Days or Units", LABEL),
    ]
    rows = [head]
    for line in ps.service_lines:
        if line.date_to and line.date_to != line.date_from:
            dates = f"{_fmt_date(line.date_from)} -<br/>{_fmt_date(line.date_to)}"
        else:
            dates = _fmt_date(line.date_from)
        units = ""
        if line.units is not None:
            units = str(int(line.units)) if line.units == int(line.units) else str(line.units)
        rows.append(
            [
                Paragraph(dates, BODY),
                Paragraph(_esc(line.place_of_service), BODY),
                Paragraph(_esc(line.cpt_code), BODY),
                Paragraph(_esc(line.description), BODY),
                Paragraph(_fmt_money(line.charges), RIGHT),
                Paragraph(_esc(units), BODY),
            ]
        )
    lines_tbl = Table(
        rows,
        colWidths=[1.05 * inch, 0.7 * inch, 0.95 * inch, 2.7 * inch, 0.9 * inch, 0.7 * inch],
        repeatRows=1,
    )
    lines_tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    story.append(lines_tbl)

    totals = _grid(
        [
            [
                [
                    Paragraph("Signature of Provider", LABEL),
                    Paragraph("See attached itemized statement / receipt.", SMALL),
                ],
                _cell("Total Charge", _fmt_money(ps.total_charge)),
                _cell("Amount Paid", _fmt_money(ps.amount_paid)),
                _cell("Balance Due", _fmt_money(ps.balance_due)),
            ],
            [
                _cell("Provider Name", ps.provider_name),
                _cell("Tax ID Number", ps.tax_id),
                "",
                _cell("NPI", ps.npi_number),
            ],
            [
                _cell("Provider Address", ps.provider_address),
                _cell("Telephone Number", _fmt_phone(ps.provider_phone)),
                "",
                "",
            ],
        ],
        [3.9 * inch, 1.05 * inch, 1.0 * inch, 1.05 * inch],
    )
    totals.setStyle(
        TableStyle([("SPAN", (1, 1), (2, 1)), ("SPAN", (1, 2), (3, 2))])
    )
    story.append(totals)

    if form.notes:
        story += [Spacer(1, 8), Paragraph(f"<b>Notes:</b> {_esc(form.notes)}", SMALL)]

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        label = f"{form.member.last_name or ''}, {form.member.first_name or ''} - Member ID {form.member.member_id or ''}"
        canvas.drawString(0.75 * inch, 0.4 * inch, label.strip(" ,-"))
        canvas.drawRightString(
            letter[0] - 0.75 * inch, 0.4 * inch, f"Page {doc_.page}"
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def render_claims_pdf(
    forms: list[ClaimForm], signatures: dict[int | None, Optional[bytes]]
) -> bytes:
    """Render several claim forms into one PDF, each form starting on a new page."""
    from pypdf import PdfReader, PdfWriter

    if len(forms) == 1:
        f = forms[0]
        return render_claim_pdf(f, signatures.get(f.authorization.signer_family_member_id))
    writer = PdfWriter()
    for f in forms:
        single = render_claim_pdf(f, signatures.get(f.authorization.signer_family_member_id))
        for page in PdfReader(BytesIO(single)).pages:
            writer.add_page(page)
    out = BytesIO()
    writer.write(out)
    return out.getvalue()


def claims_pdf_filename(forms: list[ClaimForm]) -> str:
    if len(forms) == 1:
        return claim_pdf_filename(forms[0])
    first = forms[0]
    parts = [
        "claims",
        (first.insurance.plan_name or "insurance").replace(" ", "_"),
        (first.member.last_name or "").replace(" ", "_"),
        f"{len(forms)}forms",
    ]
    dates = [
        l.date_from for f in forms for l in f.provider_statement.service_lines if l.date_from
    ]
    if dates:
        parts.append(min(dates).strftime("%Y%m%d"))
        if max(dates) != min(dates):
            parts.append(max(dates).strftime("%Y%m%d"))
    safe = "_".join(p for p in parts if p)
    return "".join(ch for ch in safe if ch.isalnum() or ch in "_-") + ".pdf"


def claim_pdf_filename(form: ClaimForm) -> str:
    parts = [
        "claim",
        (form.insurance.plan_name or "insurance").replace(" ", "_"),
        (form.patient.last_name or form.member.last_name or "").replace(" ", "_"),
        (form.patient.first_name or form.member.first_name or "").replace(" ", "_"),
    ]
    dates = [l.date_from for l in form.provider_statement.service_lines if l.date_from]
    if dates:
        parts.append(min(dates).strftime("%Y%m%d"))
        if len(dates) > 1 and max(dates) != min(dates):
            parts.append(max(dates).strftime("%Y%m%d"))
    safe = "_".join(p for p in parts if p)
    return "".join(ch for ch in safe if ch.isalnum() or ch in "_-") + ".pdf"
