# Docs (Astro 7 + Starlight, EN default + DE)

Bilingual site generated from the DOORS RIF excerpts. Content lives in
`src/content/docs/{en,de}/` (same filenames per language), figures in
`public/assets/<Original-Modulname>/`.

## Develop

```bash
cd docs
npm install
npm run dev
```

## Build (static, GitHub Pages–ready)

```bash
cd docs
npm run build   # -> dist/
npm run preview # serve dist/ locally
```

GitHub Pages project sites need `base: '/<repo>/'`. Pass it via env —
no workflow file is shipped (owner wires their own):

```bash
BASE=/<repo>/ npm run build
```

## Regenerate content

```bash
python3 tools/ole_to_png.py --all   # RTF/WMF OLE -> PNG + .txt + originals
python3 tools/rif_to_mdx.py         # RIF XML -> en/de MDX + converted/ mirror
```

- Default language is **English** (`defaultLocale: 'en'`); German original
  is under `/de/`. Objects without English text fall back to German with a badge.
- Search (Pagefind), sidebar, language picker and last-updated are built in.
