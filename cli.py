"""UrbanAgri-Copilot command-line runner.

Usage:
    python cli.py plan --lat 6.9271 --lon 79.8612 --space 2.0 --container pot
    python cli.py remedy --issue "Nitrogen Deficiency" --scraps "banana peel,eggshells"
    python cli.py report --output garden_report.pdf
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime
from pathlib import Path

# Ensure the project root is on sys.path when running the CLI directly.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.app.core.database import SessionLocal
from backend.app.models.schemas import CropOut
from backend.app.services import planner_service, remedy_service, report_service
from backend.seed_data import seed_all


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser (exposed for tests)."""
    parser = argparse.ArgumentParser(
        prog="urban-agri-copilot",
        description="UrbanAgri-Copilot autonomous gardening assistant.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    plan_parser = subparsers.add_parser("plan", help="Generate a ranked crop plan")
    plan_parser.add_argument("--lat", type=float, required=True, help="Latitude")
    plan_parser.add_argument("--lon", type=float, required=True, help="Longitude")
    plan_parser.add_argument("--space", type=float, required=True, help="Available space in m²")
    plan_parser.add_argument(
        "--container",
        type=str,
        default="grow_bag",
        choices=["pot", "grow_bag", "ground"],
        help="Container type (default: grow_bag)",
    )
    plan_parser.add_argument(
        "--month",
        type=int,
        default=datetime.now().month,
        help="Target month as integer 1-12 (default: current month)",
    )
    plan_parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Optional path to write the JSON plan",
    )

    remedy_parser = subparsers.add_parser("remedy", help="Synthesize an organic remedy")
    remedy_parser.add_argument("--issue", type=str, required=True, help="Plant issue to treat")
    remedy_parser.add_argument(
        "--scraps",
        type=str,
        default=None,
        help="Comma-separated list of available kitchen scraps",
    )

    report_parser = subparsers.add_parser("report", help="Generate a sample garden report")
    report_parser.add_argument(
        "--output",
        type=str,
        default="report.pdf",
        help="Output report path (default: report.pdf)",
    )

    return parser


def _serialize_crop(crop: object) -> dict:
    """Serialize a Crop ORM instance to a plain dict."""
    return CropOut.model_validate(crop).model_dump()


def _serialize_plan(plan: dict) -> dict:
    """Convert a planner result into JSON-serializable dicts."""
    result = dict(plan)
    serialized_recs = []
    for rec in plan.get("recommendations", []):
        if isinstance(rec, dict):
            serialized = dict(rec)
            crop = serialized.get("crop")
            if crop is not None and not isinstance(crop, dict):
                serialized["crop"] = _serialize_crop(crop)
        else:
            serialized = {
                "crop": _serialize_crop(rec.crop),
                "suitability_score": rec.suitability_score,
                "recommended_pot_count": rec.recommended_pot_count,
                "layout": rec.layout,
                "companion_synergy": rec.companion_synergy,
                "notes": rec.notes,
            }
        serialized_recs.append(serialized)
    result["recommendations"] = serialized_recs
    return result


async def _cmd_plan(args: argparse.Namespace) -> None:
    """Run the plan command."""
    seed_all()
    db = SessionLocal()
    try:
        plan = await planner_service.generate_crop_plan(
            lat=args.lat,
            lon=args.lon,
            container_type=args.container,
            space_sqm=args.space,
            target_month=args.month,
            db=db,
        )
        serializable = _serialize_plan(plan)
        print(json.dumps(serializable, indent=2))
        if args.output:
            Path(args.output).write_text(
                json.dumps(serializable, indent=2), encoding="utf-8"
            )
            print(f"Plan saved to {args.output}", file=sys.stderr)
    finally:
        db.close()


def _cmd_remedy(args: argparse.Namespace) -> None:
    """Run the remedy command."""
    scraps = (
        [s.strip() for s in args.scraps.split(",") if s.strip()]
        if args.scraps
        else None
    )
    recipe = remedy_service.synthesize_organic_remedy(args.issue, scraps)
    print(json.dumps(recipe, indent=2))


def _cmd_report(args: argparse.Namespace) -> None:
    """Run the report command with sample data."""
    sample_plan = {
        "location": {"lat": 6.9271, "lon": 79.8612},
        "container_type": "grow_bag",
        "space_sqm": 2.0,
        "target_month": 9,
        "forecast_summary": {
            "avg_min_temp_c": 24.0,
            "avg_max_temp_c": 30.0,
            "avg_daylight_hours": 12.0,
        },
        "recommendations": [
            {
                "crop": {"name": "Gotukola", "category": "leafy"},
                "suitability_score": 95,
                "recommended_pot_count": 200,
                "layout": "20 rows × 10 cols (200 plants)",
                "companion_synergy": ["Grows well with Kang-kung"],
                "notes": "Harvestable in ~45 days; Water roughly every 1 day(s)",
            },
            {
                "crop": {"name": "Tomato", "category": "fruiting"},
                "suitability_score": 88,
                "recommended_pot_count": 9,
                "layout": "3 rows × 3 cols (9 plants)",
                "companion_synergy": ["Grows well with Green Chilli"],
                "notes": "Harvestable in ~75 days; Water roughly every 2 day(s)",
            },
        ],
    }

    sample_diagnosis = {
        "crop_detected": "Tomato",
        "issue_type": "Early Nitrogen Deficiency & Leaf Curl",
        "confidence": 0.92,
        "symptoms": [
            "Older leaves turning pale green to yellow",
            "Upward curling of young leaves",
            "Stunted new growth",
        ],
        "severity": "moderate",
        "recommended_action": "Apply a nitrogen-rich organic feed such as compost tea or banana-peel fertiliser; inspect undersides of leaves for pests.",
    }

    sample_savings = {
        "crop_name": "Tomato",
        "expected_yield_kg": 2.5,
        "retail_price_per_kg": 300.0,
        "currency": "LKR",
        "estimated_savings": 750.0,
    }

    output_path = report_service.generate_garden_report(
        plan_data=sample_plan,
        diagnosis_data=sample_diagnosis,
        savings_data=sample_savings,
        output_path=args.output,
    )
    print(f"Report generated: {output_path}")


async def main_async() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "plan":
        await _cmd_plan(args)
    elif args.command == "remedy":
        _cmd_remedy(args)
    elif args.command == "report":
        _cmd_report(args)


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
