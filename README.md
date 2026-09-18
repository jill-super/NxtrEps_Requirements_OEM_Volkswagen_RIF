# VW EPS Requirements (RIF) — Bilingual EN/DE Documentation

DOORS Requirements Interchange Format (RIF) excerpts for an Electric Power
Steering (EPS) programme, converted into a browsable, searchable,
**bilingual documentation site (English default, German original)** with
**every embedded figure extracted**.

- Source: 16 × `*_EXERPT_20160509.xml` (DOORS 9.6.0.1, exported 2016-05-17),
  original language **German**
- Converted: **3,022 objects** (2,106 requirements) in document order,
  **89 embedded OLE figures** as PNG + searchable transcription + downloadable original
- Site: static build (Astro + Starlight), EN at `/en/`, DE at `/de/`,
  root `/` redirects to EN; full-text search, per-page ID/text/type filter,
  deep links per requirement (`#<Object-ID>`), print CSS for Word-like output
- Mirrors: plain-Markdown `converted/` copies of every page for reading
  directly on the code hosting platform, no build needed

## Content — 16 modules

| Module (EN) | Objects | Requir. | Figs | Official EN |
|---|---|---|---|---|
| General Functional-Safety Requirements | 31 | 22 | 0 | 58% |
| General Software Requirements | 381 | 268 | 8 | 98% |
| General Electrical Requirements | 148 | 108 | 7 | 97% |
| Component Spec — EMC Power Steering 2.5 | 141 | 93 | 15 | 100% |
| Base Module — Steering Specification | 1034 | 723 | 20 | 11% |
| EE Quality Assurance | 237 | 147 | 1 | 0% |
| Testing — Electrics & Electronics | 65 | 39 | 6 | 97% |
| Testing — Mechanics | 44 | 14 | 6 | 93% |
| Testing — SiL | 28 | 16 | 0 | 100% |
| Testing — Software Functions | 175 | 143 | 4 | 100% |
| Component Spec — iLWS | 88 | 62 | 1 | 100% |
| Steering-Torque Sensing | 56 | 40 | 4 | 100% |
| Mechanics — Steering Gear | 171 | 104 | 14 | 94% |
| Applicable Documents | 178 | 177 | 0 | 96% |
| ECU and Motor | 90 | 46 | 3 | 83% |
| Material Requirements | 155 | 104 | 0 | 0% |
| **Total** | **3,022** | **2,106** | **89** | — |

Remaining objects without official DOORS English are machine-translated
(Helsinki-NLP/opus-mt-de-en + automotive glossary) and carry a yellow
**MT** badge on EN pages — type `MT` in the page filter to list them.
The three (almost) fully-German modules are the Base Module, EE Quality
Assurance and Material Requirements.

## Repository layout

```text
*.xml                        # 16 DOORS RIF excerpts (source of truth, DE)
<Module>_EXERPT_20160509/    # 12 dirs with embedded *.ole objects (89 total)
tools/
  ole_to_png.py              # OLE (RTF/WMF) -> PNG + .txt transcription + original download
  rif_to_mdx.py              # RIF XML -> bilingual MDX (EN default + DE) + converted/ mirrors
  translate_en.py            # bulk DE->EN translation, persistent cache
converted/
  mt_cache_de_en.json        # translation memory {german_line: english_line}
  manifest.json              # per-module stats (objects/requirements/figures/EN share)
  <Module EN>/README-EN.md   # English plain-Markdown mirror per module
  <Module DE>/README-DE.md   # German plain-Markdown mirror per module
docs/                        # static site (Astro + Starlight, EN default + DE)
  src/content/docs/en/       # 16 module pages + index (English)
  src/content/docs/de/       # 16 module pages + index (German, original)
  public/assets/<Module>/    # 89 PNG figures + transcriptions + original OLE payloads
```

## Use

### Read without building

Open any `converted/<Module>/README-EN.md` (or `-DE.md` for German) —
same content as the site, figures linked relatively.

### Run the site locally

```bash
cd docs
npm install
npm run dev
```

Open the printed URL: `/` redirects to `/en/`, `/de/` holds the German original.

### Static build (hosting-ready)

```bash
cd docs
npm run build     # -> dist/
npm run preview   # serve dist/ locally
```

For a project-subpath deployment pass the subpath as base:

```bash
BASE=/<repo>/ npm run build
```

No CI workflow is shipped — wire your own (build `docs/`, publish `docs/dist/`).

## Regenerate everything

```bash
python3 tools/ole_to_png.py --all   # OLE -> docs/public/assets (PNG + .txt + originals)
python3 tools/rif_to_mdx.py         # RIF -> docs/src/content/docs/{en,de} + converted/ + manifests
```

Translation cache first (only needed if German source lines changed):

```bash
python3 tools/translate_en.py --stats   # show missing segments
python3 tools/translate_en.py           # translate missing, update cache + figure .en.txt
```

Requirements: Python 3 with Pillow (+ `transformers` + `torch` only for
re-translation), Node 18+.

## What is extracted (fidelity notes)

- **Order = DOORS spec hierarchy**, i.e. the same sequence as a DOORS
  Word export — not alphabetical, not file order.
- **All attributes**: Object ID / Heading / Text (+ official English
  variants where present), Typ, Validity, Status VW / Supplier, both
  comments, Version (where the module defines it).
- **`Überschrift` → real Markdown headings** (table of contents + search);
  everything else becomes a requirement card with an `id="<Object-ID>"`
  anchor, badges (type / validity / VW / supplier / MT), metadata table
  and collapsible comments.
- **All rich text**: bold, italic, underline, sub/superscript, lists,
  line breaks — converted to Markdown/HTML that survives the MDX compiler
  (stray `<`, `{`, `}` in prose such as `<75%` are escaped).
- **All 89 OLE figures**: bitmap DIBs pixel-exact; vector WMF
  (Word/Excel tables and drawings) re-rendered readably as PNG **plus**
  a searchable transcription (`<details>` block, EN machine-translated on
  EN pages) **plus** the original embedded payload (`.doc`/`.xls`/`.bin`)
  as a per-figure download link.
- **Interactive structure**: site-wide full-text search, per-page
  ID/text/type filter with live count, language switcher, sidebar,
  `Überschrift` table of contents, per-requirement deep links,
  print stylesheet (filter/sidebar hidden, cards and figures kept together —
  use browser print-to-PDF for a Word-like document).

## Provenance

Source exports: `*_EXERPT_20160509.xml`, `DOORS 9.6.0.1`,
creation timestamps 2016-05-17 (see per-page header and
`converted/manifest.json`). German is the original; English pages prefer
the official DOORS English text wherever the export contains it and mark
every machine-translated requirement with an **MT** badge.
