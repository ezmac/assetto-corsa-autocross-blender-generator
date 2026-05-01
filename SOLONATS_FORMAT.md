# Solo Nationals Autocross Course Format

Reference for understanding and processing Solo Nationals (SCCA) course PDFs from the Lincoln, NE venue.

---

## PDF Structure

Each course is one page of a multi-page PDF. Pages are typically ~8.5×11" (letter) at 72 pt/in, so **1 pt = 1 ft = 0.3048 m** in course space. This scale is consistent across years.

The page contains:
- Vector path drawings (cones, pointer arrows, timing gate bars)
- Numeric text labels (cone numbers, section identifiers)
- Colored text labels ("Start", "Finish")
- Other annotations (page numbers, headings, legend text) — these can match the digit patterns used for cone labels and must be filtered out

---

## Cone Symbols

### Standing cones
Filled circles or small squares drawn with colored fill (typically orange/yellow). Represent physical traffic cones placed upright on course.

### Pointer cones (tipped cones / arrows)
Triangular arrow shapes. Represent a cone tipped on its side pointing in a direction. The tip of the arrow indicates which way the cone points. Used to mark turns and course direction.

### Timing cones (start/finish markers)
Standing cones at the start and finish gates, often drawn in green (start) or red (finish). May be co-located with or adjacent to the timing gate bar.

---

## Timing Gates

### Detection methods (in priority order)

1. **Colored text labels** — "Start" in green, "Finish" in red. Most authoritative when present.
2. **Stroke bars** — Colored line segments (fill=None) drawn across the course. Green = start, red = finish. Extracted via `page.get_drawings()`. The bar endpoints define the gate width (LEFT and RIGHT marker positions).
3. **Fill bars** — Colored filled rectangles spanning the course. Less common, less reliable.
4. **Cone number inference** — Last section's highest-numbered cones near section-1 cones indicate finish position (fallback only).

### Gate representation in JSON
```json
"timing_start_gate": {"a": [bx, by], "b": [bx, by]},
"timing_end_gate":   {"a": [bx, by], "b": [bx, by]}
```
`a` and `b` are the two bar endpoints in Blender world space (metres). These become `AC_AB_START_L/R` and `AC_AB_FINISH_L/R` markers in the game.

---

## Cone Numbering

Cones are numbered by **section**: 100s = section 1, 200s = section 2, ..., 600s = section 6 (max observed). Section 1 begins at the start; the highest-numbered section ends at the finish.

### Staging area (cone 101 region)

The **staging area** is where the car waits before the start run. It sits just behind (before) the start gate. Cone 100/101 marks this area. Cone 101 is typically within ~20m (~60 ft) of the start gate.

**Important:** The PDF may contain multiple text spans with the same digit string (e.g., "101" appearing as both a cone label and as a page annotation or diagram note elsewhere on the page). Non-cone occurrences of numbers can be far from the actual course elements. Always filter candidate cone labels by proximity to the expected gate position (< 300 pt / ~90 m) before trusting them as cone references.

**Correct detection:** Find all section-1 cone labels (100-199) within ~300 pt of the start gate in PDF space. Take the 5 lowest-numbered from that set. Their centroid = staging position.

### Start direction rule

> **The car always crosses the start gate going AWAY from the staging area (away from cone 101).**

Implementation: `entry = normalize(gate_midpoint - stage_centroid)`.

Do not use:
- Cone-count majority (fails when far-side cones bias the count)
- Course centroid (fails when start gate is offset from the geometric center of all cones)

### Finish direction

The car exits the finish gate going **away from the course interior**. Use the course centroid (centroid of all cone positions) to determine which side is interior. `finish_exit = -interior_perp(finish_gate, toward_centroid)`.

---

## Coordinate System

### PDF space
- Origin: top-left of page
- X: right (positive)
- Y: down (positive)
- Units: points (1 pt = 1/72 inch = 1 ft at course scale)

### Blender world space
- Origin: centroid of all detected cones
- X: right
- Y: up (PDF Y is negated: `by = -(pdf_y - cy_centroid) * m_per_pt`)
- Units: metres

### Transform stored in JSON
```json
"transform": {
  "type": "scale",
  "scale": 0.3048,    // m/pt
  "ox": ...,          // = -cx_centroid * scale  (world X at PDF x=0)
  "oy": ...,          // =  cy_centroid * scale  (world Y at PDF y=0)
  "page_w_pt": ...,
  "page_h_pt": ...
}
```
Reconstruction:
```python
cx_centroid = -ox / scale   # PDF x of cone centroid
cy_centroid =  oy / scale   # PDF y of cone centroid
# world coords from PDF coords:
bx = (pdf_x - cx_centroid) * scale
by = -(pdf_y - cy_centroid) * scale
# pixel coords in preview (150 DPI):
px = pdf_x * (150/72)
py = pdf_y * (150/72)
```

---

## Course Layout Patterns (Lincoln, NE)

- Two courses per day: **East** and **West**, on opposite ends of the lot.
- Courses typically span 200-300 m in the longer axis.
- The start gate is at one end; finish gate at the other (or same end with a turnaround).
- Staging lane runs parallel to the start gate on one side. Cars queue in staging, then pull forward through the gate.
- Finish chute runs adjacent to the staging lane — the car finishes then coasts into the return lane.

---

## Common Detection Pitfalls

### Parking space numbers
Some course maps (e.g. 2013 west) include numbered parking spaces along the edge of the page. These are printed with the same font and size as cone labels and fall in the same numeric range (100s). They are not cone positions. Filter by proximity to the start gate to avoid treating parking space numbers as staging cone references.

### Gate direction via course centroid
The course centroid can be on the wrong side of the gate when the gate is near the geometric center of the bounding box. Always prefer staging position over centroid for start direction.

### Bar endpoints vs bar centroid
The gate bar's **endpoints** are the LEFT/RIGHT timing marker positions. The bar **centroid** is only useful for approximate gate location detection. Always store and use the endpoints for `AC_AB_START_L/R`.

---

## JSON Output Fields

Key fields written by `detect_cones_pdf.run()`:

| Field | Description |
|---|---|
| `standing` | List of `{bx, by, type, size}` — upright cones |
| `pointers` | List of `{bx, by, type, facing}` — tipped/arrow cones |
| `timing_start` | Green timing cones near start gate |
| `timing_end` | Red timing cones near finish gate |
| `timing_start_gate` | `{a:[x,y], b:[x,y]}` bar endpoints, start |
| `timing_end_gate` | `{a:[x,y], b:[x,y]}` bar endpoints, finish |
| `stage_cone_pos` | `[bx, by]` centroid of lowest-numbered nearby section-1 cones |
| `bounds` | `{xmin,xmax,ymin,ymax}` of all cones |
| `transform` | Scale/offset to reconstruct PDF↔world mapping |
