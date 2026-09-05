from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.lib.units import cm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
    )

    _HAS_REPORTLAB = True
except Exception:  # pragma: no cover
    _HAS_REPORTLAB = False


def _clean_crop_name(name: str) -> str:
    """Normalize any variations of Chilli / Green Chilli to clean display name."""
    clean = (name or "").strip()
    if clean.lower() in ["green chilli", "chilli", "chili", "green chili"]:
        return "Chillie"
    return clean


def _val(value: Any, fallback: str = "N/A") -> str:
    if value is None:
        return fallback
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _cell(text: str, style: ParagraphStyle, bold: bool = False) -> Paragraph:
    content = f"<b>{text}</b>" if bold else text
    return Paragraph(content, style)


def _generate_pdf(
    plan_data: dict,
    diagnosis_data: dict | None,
    savings_data: dict,
    output_path: str,
) -> str:
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=1.6 * cm,
        leftMargin=1.6 * cm,
        topMargin=1.4 * cm,
        bottomMargin=1.6 * cm,
    )

    styles = getSampleStyleSheet()

    # --- Color Palette ---
    C_PRIMARY = colors.HexColor("#15803d")       # Emerald 700
    C_PRIMARY_DARK = colors.HexColor("#14532d")  # Emerald 900
    C_PRIMARY_LIGHT = colors.HexColor("#f0fdf4") # Emerald 50
    C_TEXT_MAIN = colors.HexColor("#1f2937")     # Gray 800
    C_BORDER = colors.HexColor("#e5e7eb")        # Gray 200
    C_BG_ALT = colors.HexColor("#f9fafb")        # Gray 50

    # --- Typography Styles ---
    normal_style = ParagraphStyle(
        "ModernNormal",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=13,
        textColor=C_TEXT_MAIN,
    )

    bold_label = ParagraphStyle(
        "ModernBoldLabel",
        parent=normal_style,
        fontName="Helvetica-Bold",
        textColor=C_TEXT_MAIN,
    )

    table_header_style = ParagraphStyle(
        "TableHeader",
        parent=normal_style,
        fontName="Helvetica-Bold",
        fontSize=8.5,
        leading=11,
        textColor=colors.white,
    )

    table_header_right = ParagraphStyle(
        "TableHeaderRight",
        parent=table_header_style,
        alignment=2,  # Right align with white text
    )

    cell_right = ParagraphStyle(
        "CellRight",
        parent=normal_style,
        alignment=2,
    )

    cell_right_bold = ParagraphStyle(
        "CellRightBold",
        parent=normal_style,
        fontName="Helvetica-Bold",
        alignment=2,
    )

    section_heading = ParagraphStyle(
        "SectionHeading",
        fontName="Helvetica-Bold",
        fontSize=11,
        leading=15,
        textColor=C_PRIMARY_DARK,
        spaceBefore=10,
        spaceAfter=5,
    )

    story: list[Any] = []

    # ------------------------------------------------------------------
    # 1. Executive Header Banner
    # ------------------------------------------------------------------
    header_content = [
        [
            Paragraph(
                "<font size='18'><b>UrbanAgri-Copilot</b></font><br/>"
                "<font size='9' color='#bbf7d0'>Smart Agro-Climatic Planning &amp; Harvest Economics</font>",
                ParagraphStyle("BrandHead", fontName="Helvetica", textColor=colors.white, leading=16),
            ),
            Paragraph(
                f"<font size='8' color='#dcfce7'>GARDEN ACTION CARD REPORT</font><br/>"
                f"<font size='8' color='#ffffff'>Generated: <b>{datetime.now().strftime('%Y-%m-%d %H:%M')}</b></font>",
                ParagraphStyle("BrandMeta", fontName="Helvetica", textColor=colors.white, alignment=2, leading=13),
            ),
        ]
    ]
    header_table = Table(header_content, colWidths=[310, 200])
    header_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), C_PRIMARY_DARK),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 12),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
        ])
    )
    story.append(header_table)
    story.append(Spacer(1, 0.35 * cm))

    # ------------------------------------------------------------------
    # 2. Estimated Grocery Savings (Executive Financial Table)
    # ------------------------------------------------------------------
    story.append(Paragraph("Projected Harvest Economics &amp; Savings", section_heading))

    savings_items = savings_data.get("items") or []
    currency = savings_data.get("currency", "LKR")

    if savings_items:
        savings_rows = [
            [
                Paragraph("CROP", table_header_style),
                Paragraph("PROJECTED YIELD", table_header_right),
                Paragraph("MARKET RETAIL", table_header_right),
                Paragraph("GROCERY SAVINGS", table_header_right),
            ]
        ]

        total_savings = 0.0
        for item in savings_items:
            crop_name = _clean_crop_name(_val(item.get("crop_name")))
            yield_val = f"{float(item.get('expected_yield_kg', 0)):.2f} kg"
            price_val = f"{float(item.get('retail_price_per_kg', 0)):,.2f} {currency}/kg"
            sav_num = float(item.get("estimated_savings", 0))
            total_savings += sav_num
            sav_val = f"{sav_num:,.2f} {currency}"

            savings_rows.append([
                _cell(crop_name, normal_style, bold=True),
                _cell(yield_val, cell_right),
                _cell(price_val, cell_right),
                _cell(sav_val, cell_right_bold),
            ])

        # Grand Total Highlight Ribbon
        savings_rows.append([
            _cell("<b>TOTAL PROJECTED SAVINGS</b>", bold_label),
            _cell("", normal_style),
            _cell("", normal_style),
            _cell(f"<b>{total_savings:,.2f} {currency}</b>", ParagraphStyle(
                "BigTotal", parent=cell_right_bold, fontSize=10, textColor=C_PRIMARY_DARK
            )),
        ])

        savings_table = Table(savings_rows, colWidths=[150, 110, 125, 125])
        savings_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), C_PRIMARY_DARK),
                ("BOX", (0, 0), (-1, -1), 0.5, C_BORDER),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("ROWBACKGROUNDS", (0, 1), (-2, -1), [colors.white, C_BG_ALT]),
                ("LINEBELOW", (0, 1), (-1, -2), 0.5, colors.HexColor("#f3f4f6")),
                ("BACKGROUND", (0, -1), (-1, -1), C_PRIMARY_LIGHT),
                ("LINEABOVE", (0, -1), (-1, -1), 1.2, C_PRIMARY),
            ])
        )
        story.append(savings_table)

    story.append(Spacer(1, 0.4 * cm))
    story.append(
        Paragraph(
            "<font size='7.5' color='#9ca3af'>* Estimated grocery savings calculated based on official weekly open market indices published in Sri Lanka. Actual container yields may vary depending on local microclimates and pest control adherence.</font>",
            normal_style
        )
    )

    doc.build(story)
    return output_path


def _generate_markdown(
    plan_data: dict,
    diagnosis_data: dict | None,
    savings_data: dict,
    output_path: str,
) -> str:
    lines = [
        "# UrbanAgri-Copilot Garden Health & Action Card Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
    ]
    savings_items = savings_data.get("items")
    if savings_items:
        currency = savings_data.get("currency", "LKR")
        lines.extend([
            "## Estimated Grocery Savings",
            "",
        ])
        for item in savings_items:
            item_currency = item.get("currency", currency)
            lines.append(
                f"- **{_clean_crop_name(_val(item.get('crop_name')))}:** {_val(item.get('expected_yield_kg'))} kg "
                f"@ {_val(item.get('retail_price_per_kg'))} {item_currency}/kg → "
                f"{_val(item.get('estimated_savings'))} {item_currency}"
            )
        total_savings = savings_data.get("total_estimated_savings")
        if total_savings is None:
            total_savings = sum((item.get("estimated_savings") or 0) for item in savings_items)
        lines.append(f"- **Grand total estimated savings:** {_val(total_savings)} {currency}")
        lines.append("")

    path = Path(output_path)
    if path.suffix.lower() == ".pdf":
        path = path.with_suffix(".md")

    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def generate_garden_report(
    plan_data: dict,
    diagnosis_data: dict | None,
    savings_data: dict,
    output_path: str = "garden_report.pdf",
) -> str:
    if _HAS_REPORTLAB:
        return _generate_pdf(plan_data, diagnosis_data, savings_data, output_path)
    return _generate_markdown(plan_data, diagnosis_data, savings_data, output_path)