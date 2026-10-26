"""Prepared places for the demo. Each one is a real event with clear Sentinel-2 images on both sides."""

DEMOS = [
    {
        "id": "bandarban-burns",
        "title": "Hill burning before the monsoon, Bandarban, Bangladesh",
        "story": "Hillsides are burned for jhum farming from February to May, just before the rains, on the same "
                 "slopes that slide in the monsoon. The burned patches come from Burn Before the Rain's hill list.",
        "bbox": [92.44, 21.60, 92.54, 21.70],
        "before": "2026-01-20", "after": "2026-05-15", "window_days": 25,
    },
    {
        "id": "evros-fire",
        "title": "Dadia forest fire, Evros, Greece (August 2023)",
        "story": "One of the largest wildfires recorded in the EU burned through Dadia National Park.",
        "bbox": [26.05, 41.00, 26.20, 41.12],
        "before": "2023-08-10", "after": "2023-09-15", "window_days": 12,
    },
    {
        "id": "sindh-flood",
        "title": "Monsoon flood near Johi, Sindh, Pakistan (2022)",
        "story": "The 2022 floods put a third of Pakistan under water; fields west of the Indus stayed flooded for weeks.",
        "bbox": [67.60, 26.65, 67.75, 26.80],
        "before": "2022-06-20", "after": "2022-09-05", "window_days": 15,
    },
    {
        "id": "para-clearing",
        "title": "Forest clearing near Novo Progresso, Para, Brazil",
        "story": "Fresh clearing along the BR-163 highway in the dry season.",
        "bbox": [-55.50, -7.20, -55.35, -7.05],
        "before": "2023-07-15", "after": "2024-08-15", "window_days": 30,
    },
]
