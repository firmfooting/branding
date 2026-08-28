# firmfooting brand

The mark is a **footing**: a trapezoid that is wider at the base, sitting on a ground line. It reads as the stable base you pour first — the same promise the tools make: firm ground under you, so a wrong step is never a fall.

All assets are generated deterministically by [`generate.py`](generate.py). Edit the geometry there, never the PNGs.

## Colour

| Token | Value | Use |
|---|---|---|
| Primary | `#2A6B64` | Tile background, universal wordmark text |
| Glyph | `#FFFFFF` | The mark |
| Text dark | `#1F2937` | Wordmark on light backgrounds |
| Text light | `#F9FAFB` | Wordmark on dark backgrounds |
| Font | DejaVu Sans Bold | Wordmark |

## Assets

| Path | What it is |
|---|---|
| `assets/icon.png` / `@2x` | Square app/repo icon (256 / 512) |
| `assets/avatar.png` / `@2x` | Org avatar (512 / 1024) |
| `assets/logo.png` / `@2x` | Wordmark, dark text (light backgrounds) |
| `assets/dark_logo.png` / `@2x` | Wordmark, light text (dark backgrounds) |
| `assets/logo_universal.png` / `@2x` | Wordmark, teal text (any theme) |
| `repos/dbml-sharepoint.png` | Canonical mark |
| `repos/dbml-sharepoint-probes.png` | Mark + probe beacon (the validation agent) |
| `repos/dbml-sharepoint-family.png` | Mark + cover bar (the private family copy) |

## Regenerate

```sh
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
python generate.py
```

`generate.py` needs Pillow only. Output is deterministic — re-running produces byte-identical files.
