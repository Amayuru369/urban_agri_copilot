from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from backend.app.services import remedy_service

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
        KeepTogether,
    )

    _HAS_REPORTLAB = True
except Exception:  # pragma: no cover - reportlab optional
    _HAS_REPORTLAB = False


def _crop_name(crop: Any) -> str:
    if isinstance(crop, dict):
        return crop.get("name") or crop.get("scientific_name") or "Unknown crop"
    return getattr(crop, "name", None) or getattr(crop, "scientific_name", None) or "Unknown crop"


def _val(value: Any, fallback: str = "N/A") -> str:
    if value is None:
        return fallback
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _cell(text: str, style: ParagraphStyle, bold: bool = False) -> Paragraph:
    """Wrap text in a Paragraph so it wraps cleanly inside table cells."""
    content = f"<b>{text}</b>" if bold else text
    return Paragraph(content, style)


def _build_remedy_for_diagnosis(diagnosis_data: dict | None) -> dict:
    if diagnosis_data and diagnosis_data.get("issue_type"):
        return remedy_service.synthesize_organic_remedy(diagnosis_data["issue_type"])
    return remedy_service.synthesize_organic_remedy("unknown issue")


def _generate_pdf(
    plan_data: dict,
    diagnosis_data: dict | None,
    savings_data: dict,
    output_path: str,
) -> str:
    doc = SimpleDocTemplate(
        output_path,
        pagesize=A4,
        rightMargin=2 * cm,
        leftMargin=2 * cm,
        topMargin=2 * cm,
        bottomMargin=2 * cm,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "ReportTitle",
        parent=styles["Title"],
        fontSize=20,
        leading=26,
        textColor=colors.HexColor("#1a472a"),
        spaceAfter=6,
    )
    heading_style = ParagraphStyle(
        "ReportHeading",
        parent=styles["Heading2"],
        fontSize=14,
        leading=18,
        textColor=colors.HexColor("#2c5282"),
        spaceAfter=8,
        spaceBefore=12,
    )
    normal_style = styles["Normal"]
    normal_style.fontSize = 10
    normal_style.leading = 14

    story: list[Any] = []
    story.append(Paragraph("UrbanAgri-Copilot", title_style))
    story.append(Paragraph("Garden Health & Action Card Report", heading_style))
    story.append(Paragraph(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", normal_style))
    story.append(Spacer(1, 0.4 * cm))

    # ------------------------------------------------------------------
    # Agro-Climatic Plan summary
    # ------------------------------------------------------------------
    story.append(Paragraph("1. Agro-Climatic Plan Summary", heading_style))
    location = plan_data.get("location") or {}
    plan_rows = [
        [_cell("Location", normal_style, bold=True), _cell(f"Lat {_val(location.get('lat'))}, Lon {_val(location.get('lon'))}", normal_style)],
        [_cell("Container", normal_style, bold=True), _cell(_val(plan_data.get("container_type")), normal_style)],
        [_cell("Space", normal_style, bold=True), _cell(f"{_val(plan_data.get('space_sqm'))} m²", normal_style)],
        [_cell("Target month", normal_style, bold=True), _cell(_val(plan_data.get("target_month")), normal_style)],
    ]
    forecast = plan_data.get("forecast_summary") or {}
    if forecast:
        plan_rows.append([_cell("Avg min temp", normal_style, bold=True), _cell(f"{_val(forecast.get('avg_min_temp_c'))} °C", normal_style)])
        plan_rows.append([_cell("Avg max temp", normal_style, bold=True), _cell(f"{_val(forecast.get('avg_max_temp_c'))} °C", normal_style)])
        plan_rows.append([_cell("Avg daylight", normal_style, bold=True), _cell(f"{_val(forecast.get('avg_daylight_hours'))} h/day", normal_style)])

    plan_table = Table(plan_rows, colWidths=[130, 340])
    plan_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf2f7")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(plan_table)
    story.append(Spacer(1, 0.4 * cm))

    recommendations = plan_data.get("recommendations") or []
    if recommendations:
        story.append(Paragraph("Top crop recommendations", heading_style))
        rec_rows = [
            [
                _cell("Crop", normal_style, bold=True),
                _cell("Score", normal_style, bold=True),
                _cell("Layout", normal_style, bold=True),
                _cell("Notes", normal_style, bold=True),
            ]
        ]
        for rec in recommendations[:5]:
            crop_name = _crop_name(rec.get("crop") if isinstance(rec, dict) else getattr(rec, "crop", None))
            score = _val(rec.get("suitability_score") if isinstance(rec, dict) else getattr(rec, "suitability_score", None))
            layout = _val(rec.get("layout") if isinstance(rec, dict) else getattr(rec, "layout", None))
            notes = _val(rec.get("notes") if isinstance(rec, dict) else getattr(rec, "notes", None), "")
            rec_rows.append([
                _cell(crop_name, normal_style),
                _cell(score, normal_style),
                _cell(layout, normal_style),
                _cell(notes, normal_style),
            ])

        rec_table = Table(rec_rows, colWidths=[80, 45, 140, 200])
        rec_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c5282")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.whitesmoke),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("LEFTPADDING", (0, 0), (-1, -1), 5),
                ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7fafc")]),
            ])
        )
        story.append(rec_table)
        story.append(Spacer(1, 0.4 * cm))

    # ------------------------------------------------------------------
    # Leaf Health Pathology status
    # ------------------------------------------------------------------
    story.append(Paragraph("2. Leaf Health Pathology Status", heading_style))
    if diagnosis_data:
        diag_rows = [
            [_cell("Crop detected", normal_style, bold=True), _cell(_val(diagnosis_data.get("crop_detected")), normal_style)],
            [_cell("Issue", normal_style, bold=True), _cell(_val(diagnosis_data.get("issue_type")), normal_style)],
            [_cell("Confidence", normal_style, bold=True), _cell(_val(diagnosis_data.get("confidence")), normal_style)],
            [_cell("Severity", normal_style, bold=True), _cell(_val(diagnosis_data.get("severity")), normal_style)],
            [_cell("Symptoms", normal_style, bold=True), _cell(", ".join(diagnosis_data.get("symptoms", [])) or "N/A", normal_style)],
            [_cell("Recommended action", normal_style, bold=True), _cell(_val(diagnosis_data.get("recommended_action")), normal_style)],
        ]
        diag_table = Table(diag_rows, colWidths=[130, 340])
        diag_table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf2f7")),
                ("FONTSIZE", (0, 0), (-1, -1), 10),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ])
        )
        story.append(diag_table)
    else:
        story.append(Paragraph("No diagnosis provided.", normal_style))
    story.append(Spacer(1, 0.4 * cm))

    # ------------------------------------------------------------------
    # Step-by-Step Organic Kitchen Remedy
    # ------------------------------------------------------------------
    story.append(Paragraph("3. Step-by-Step Organic Kitchen Remedy", heading_style))
    recipe = _build_remedy_for_diagnosis(diagnosis_data)
    story.append(Paragraph(f"<b>{recipe['remedy_name']}</b>", normal_style))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("<b>Ingredients:</b>", normal_style))
    for ingredient in recipe.get("ingredients", []):
        story.append(Paragraph(f"• {ingredient}", normal_style))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("<b>Preparation:</b>", normal_style))
    for idx, step in enumerate(recipe.get("preparation_steps", []), start=1):
        story.append(Paragraph(f"{idx}. {step}", normal_style))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph(f"<b>Application schedule:</b> {recipe.get('application_schedule', 'N/A')}", normal_style))
    story.append(Spacer(1, 0.2 * cm))
    story.append(Paragraph("<b>Safety notes:</b>", normal_style))
    for note in recipe.get("safety_notes", []):
        story.append(Paragraph(f"• {note}", normal_style))
    story.append(Spacer(1, 0.4 * cm))

    # ------------------------------------------------------------------
    # Estimated Grocery Savings
    # ------------------------------------------------------------------
    story.append(Paragraph("4. Estimated Grocery Savings", heading_style))
    savings_rows = [
        [_cell("Crop", normal_style, bold=True), _cell(_val(savings_data.get("crop_name")), normal_style)],
        [_cell("Expected yield", normal_style, bold=True), _cell(f"{_val(savings_data.get('expected_yield_kg'))} kg", normal_style)],
        [_cell("Retail price", normal_style, bold=True), _cell(f"{_val(savings_data.get('retail_price_per_kg'))} {savings_data.get('currency', 'LKR')}/kg", normal_style)],
        [_cell("Estimated savings", normal_style, bold=True), _cell(f"{_val(savings_data.get('estimated_savings'))} {savings_data.get('currency', 'LKR')}", normal_style)],
    ]
    savings_table = Table(savings_rows, colWidths=[130, 340])
    savings_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#edf2f7")),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    story.append(savings_table)

    doc.build(story)
    return output_path


def _generate_markdown(
    plan_data: dict,
    diagnosis_data: dict | None,
    savings_data: dict,
    output_path: str,
) -> str:
    recipe = _build_remedy_for_diagnosis(diagnosis_data)
    location = plan_data.get("location") or {}
    forecast = plan_data.get("forecast_summary") or {}
    recommendations = plan_data.get("recommendations") or []

    lines = [
        "# UrbanAgri-Copilot Garden Health & Action Card Report",
        "",
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "",
        "## 1. Agro-Climatic Plan Summary",
        "",
        f"- **Location:** Lat {_val(location.get('lat'))}, Lon {_val(location.get('lon'))}",
        f"- **Container:** {_val(plan_data.get('container_type'))}",
        f"- **Space:** {_val(plan_data.get('space_sqm'))} m²",
        f"- **Target month:** {_val(plan_data.get('target_month'))}",
        f"- **Avg min temp:** {_val(forecast.get('avg_min_temp_c'))} °C",
        f"- **Avg max temp:** {_val(forecast.get('avg_max_temp_c'))} °C",
        f"- **Avg daylight:** {_val(forecast.get('avg_daylight_hours'))} h/day",
        "",
    ]

    if recommendations:
        lines.append("### Top crop recommendations")
        lines.append("")
        for rec in recommendations[:5]:
            crop_obj = rec.get("crop") if isinstance(rec, dict) else getattr(rec, "crop", None)
            crop_name = _crop_name(crop_obj)
            score = _val(rec.get("suitability_score") if isinstance(rec, dict) else getattr(rec, "suitability_score", None))
            layout = _val(rec.get("layout") if isinstance(rec, dict) else getattr(rec, "layout", None))
            notes = _val(rec.get("notes") if isinstance(rec, dict) else getattr(rec, "notes", None), "")
            lines.append(f"- **{crop_name}** (score {score}) — {layout}. {notes}")
        lines.append("")

    lines.extend([
        "## 2. Leaf Health Pathology Status",
        "",
    ])
    if diagnosis_data:
        lines.extend([
            f"- **Crop detected:** {_val(diagnosis_data.get('crop_detected'))}",
            f"- **Issue:** {_val(diagnosis_data.get('issue_type'))}",
            f"- **Confidence:** {_val(diagnosis_data.get('confidence'))}",
            f"- **Severity:** {_val(diagnosis_data.get('severity'))}",
            f"- **Symptoms:** {', '.join(diagnosis_data.get('symptoms', [])) or 'N/A'}",
            f"- **Recommended action:** {_val(diagnosis_data.get('recommended_action'))}",
            "",
        ])
    else:
        lines.extend(["No diagnosis provided.", ""])

    lines.extend([
        "## 3. Step-by-Step Organic Kitchen Remedy",
        "",
        f"### {recipe['remedy_name']}",
        "",
        "**Ingredients:**",
        "",
    ])
    for ingredient in recipe.get("ingredients", []):
        lines.append(f"- {ingredient}")
    lines.extend(["", "**Preparation:**", ""])
    for idx, step in enumerate(recipe.get("preparation_steps", []), start=1):
        lines.append(f"{idx}. {step}")
    lines.extend([
        "",
        f"**Application schedule:** {recipe.get('application_schedule', 'N/A')}",
        "",
        "**Safety notes:**",
        "",
    ])
    for note in recipe.get("safety_notes", []):
        lines.append(f"- {note}")
    lines.extend([
        "",
        "## 4. Estimated Grocery Savings",
        "",
        f"- **Crop:** {_val(savings_data.get('crop_name'))}",
        f"- **Expected yield:** {_val(savings_data.get('expected_yield_kg'))} kg",
        f"- **Retail price:** {_val(savings_data.get('retail_price_per_kg'))} {savings_data.get('currency', 'LKR')}/kg",
        f"- **Estimated savings:** {_val(savings_data.get('estimated_savings'))} {savings_data.get('currency', 'LKR')}",
        "",
    ])

    # Ensure the fallback file is saved as markdown for clarity.
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
    """Generate a structured Garden Health & Action Card report.

    If `reportlab` is available, a PDF is produced. Otherwise a clean markdown
    Action Card is written and its path is returned.
    """
    if _HAS_REPORTLAB:
        return _generate_pdf(plan_data, diagnosis_data, savings_data, output_path)
    return _generate_markdown(plan_data, diagnosis_data, savings_data, output_path)
