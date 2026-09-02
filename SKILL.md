---
name: urban_agri_skills
description: Interactive agent skills for UrbanAgri-Copilot — plan urban gardens, diagnose plant issues, synthesize organic remedies, and estimate grocery savings.
version: 0.1.0
---

# UrbanAgri-Copilot Agent Skills

This skill package exposes four interactive tools that let QoderWork act as an urban gardening assistant for Sri Lankan home growers.

## Tools

### `skill_plan_garden`

Generate a ranked crop plan for a given location, container type, and growing space.

Parameters:

- `lat` (number): Latitude of the garden.
- `lon` (number): Longitude of the garden.
- `container_type` (string): One of `pot`, `grow_bag`, or `ground`.
- `space_sqm` (number): Available growing area in square metres.
- `target_month` (integer): Target calendar month, 1-12.

Example:

```json
{
  "lat": 6.9271,
  "lon": 79.8612,
  "container_type": "grow_bag",
  "space_sqm": 2.0,
  "target_month": 9
}
```

The planner fetches a 7-day Open-Meteo forecast, scores each crop on temperature fit, sunlight fit, and container suitability, and returns a ranked list with recommended layouts and companion-synergy notes.

### `skill_diagnose_plant`

Diagnose a plant health issue from an image.

Parameters:

- `image_path` (string): Filesystem path to the plant photo.
- `crop_name` (string, optional): Crop name to guide the diagnosis.

When no real vision endpoint is configured, the skill returns a deterministic mock diagnosis based on the crop name (e.g. nitrogen deficiency for tomato/chilli/brinjal, iron chlorosis for leafy greens).

### `skill_synthesize_remedy`

Generate a zero-chemical organic remedy recipe from kitchen scraps.

Parameters:

- `issue_type` (string): The diagnosed issue, e.g. `Nitrogen Deficiency`, `Leaf Curl`, `Powdery Mildew`.
- `available_scraps` (list[string], optional): Scraps on hand, e.g. `["banana peels", "eggshells"]`.

The recipe includes ingredients, preparation steps, an application schedule, safety notes, and a list of matched scraps.

### `skill_calculate_savings`

Estimate household grocery savings for growing a crop.

Parameters:

- `crop_name` (string): Name of the crop.
- `expected_yield_kg` (number): Expected harvest yield in kilograms.

The skill looks up the latest retail market price and computes `retail_price_per_kg * expected_yield_kg` in LKR.

## Integration notes

- Service handlers live under `backend/app/services/`.
- `skill_diagnose_plant` expects the agent to read `image_path` into bytes before invoking `diagnose_service.diagnose_plant_image`.
- `skill_calculate_savings` requires a SQLAlchemy `Session` and queries the `market_prices` table seeded by `backend/seed_data.py`.

## CLI usage

A standalone CLI is provided in `cli.py`:

```bash
python cli.py plan --lat 6.9271 --lon 79.8612 --space 2.0 --container pot
python cli.py remedy --issue "Nitrogen Deficiency" --scraps "banana peel,eggshells"
python cli.py report --output garden_report.pdf
```
