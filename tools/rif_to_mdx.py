#!/usr/bin/env python3
"""RIF (DOORS) -> bilingual MDX converter.

Reads every *_EXERPT_20160509.xml in the repo root, preserves the
SPEC-HIERARCHY order (Word/DOORS document order), converts rif-xhtml to
Markdown, wires in OLE figures already rendered by tools/ole_to_png.py
(docs/public/assets/<module>/*.png + *.txt), and emits Starlight-ready MDX:

  docs/src/content/docs/en/<slug>.mdx   (English; fallback to German)
  docs/src/content/docs/de/<slug>.mdx   (German original)

Also emits:
  docs/src/content/docs/en/index.mdx, de/index.mdx (overview + stats)
  converted/<Module>/README-DE.md, README-EN.md (plain-MD mirror for GitHub)
  docs/src/content/docs/i18n-manifest.json + converted/manifest.json
"""
from __future__ import annotations
import glob
import html
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter

NS = {"r": "http://automotive-his.de/200706/rif",
      "x": "http://automotive-his.de/200706/rif-xhtml"}
XR = "{http://automotive-his.de/200706/rif-xhtml}"

MODULES = [
    ("Allgemeine Anforderungen zur Funktionalen Sicherheit_EXERPT_20160509", "funktionale-sicherheit", "General Functional-Safety Requirements", "Allgemeine Anforderungen zur Funktionalen Sicherheit"),
    ("Allgemeine Software-Anforderungen_EXERPT_20160509", "software-anforderungen", "General Software Requirements", "Allgemeine Software-Anforderungen"),
    ("Allgemeine elektrische Anforderungen_EXERPT_20160509", "elektrische-anforderungen", "General Electrical Requirements", "Allgemeine elektrische Anforderungen"),
    ("BT-LAH-Modul_EMV_Lenkhilfe_2.5_EXERPT_20160509", "emv-lenkhilfe", "Component Spec — EMC Power Steering 2.5", "BT-LAH-Modul EMV Lenkhilfe 2.5"),
    ("Basismodul Lenkungslastenheft_EXERPT_20160509", "basismodul-lenkung", "Base Module — Steering Specification", "Basismodul Lenkungslastenheft"),
    ("EE Qualitaetssicherung_EXERPT_20160509", "ee-qualitaetssicherung", "EE Quality Assurance", "EE Qualitätssicherung"),
    ("Erprobung Elektrik und Elektronik_EXERPT_20160509", "erprobung-elektrik-elektronik", "Testing — Electrics & Electronics", "Erprobung Elektrik und Elektronik"),
    ("Erprobung Mechanik_EXERPT_20160509", "erprobung-mechanik", "Testing — Mechanics", "Erprobung Mechanik"),
    ("Erprobung SiL_EXERPT_20160509", "erprobung-sil", "Testing — SiL", "Erprobung SiL"),
    ("Erprobung Software-Funktion_EXERPT_20160509", "erprobung-software-funktion", "Testing — Software Functions", "Erprobung Software-Funktion"),
    ("LAH-iLWS_EXERPT_20160509", "lah-ilws", "Component Spec — iLWS", "LAH-iLWS"),
    ("Lenkmomentenerfassung_EXERPT_20160509", "lenkmomentenerfassung", "Steering-Torque Sensing", "Lenkmomentenerfassung"),
    ("Mechanik Lenkgetriebe_EXERPT_20160509", "mechanik-lenkgetriebe", "Mechanics — Steering Gear", "Mechanik Lenkgetriebe"),
    ("Mitgeltende Unterlagen_EXERPT_20160509", "mitgeltende-unterlagen", "Applicable Documents", "Mitgeltende Unterlagen"),
    ("Steuergeraet und Motor_EXERPT_20160509", "steuergeraet-motor", "ECU and Motor", "Steuergerät und Motor"),
    ("Werkstoffanforderungen_EXERPT_20160509", "werkstoffanforderungen", "Material Requirements", "Werkstoffanforderungen"),
]
SLUG_OF = {m[0]: m[1] for m in MODULES}
EN_TITLE = {m[0]: m[2] for m in MODULES}
DE_TITLE = {m[0]: m[3] for m in MODULES}

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) if "__file__" in dir() else os.getcwd()

# ---- machine translation (DE->EN) -------------------------------------------
# Built by tools/translate_en.py (Helsinki-NLP/opus-mt-de-en + automotive
# glossary). Line-granular: {german_line: english_line}.
MT_CACHE: dict[str, str] = {}
try:
    with open(os.path.join(REPO, "converted", "mt_cache_de_en.json"), encoding="utf-8") as _fh:
        MT_CACHE = json.load(_fh)
except (OSError, ValueError):
    MT_CACHE = {}


def mt_translate(de_text: str) -> tuple[str, bool]:
    """Translate German markdown line-by-line via MT cache.

    Returns (text, fully_translated). Lines missing from the cache are kept
    in German (caller flags them). Placeholders ({{FIGURE:…}}) and HTML tags
    (<sub>/<sup>/<u>) survive because the model was verified to preserve them.
    """
    if not (de_text or "").strip():
        return "", True
    out, full = [], True
    for ln in de_text.split("\n"):
        s = ln.strip()
        if not s:
            out.append("")
            continue
        t = MT_CACHE.get(s)
        if t is None:
            out.append(ln)
            full = False
        else:
            # preserve leading list/quote markers spacing of the original line
            prefix = ln[:len(ln) - len(ln.lstrip())]
            out.append(prefix + t.strip())
    return "\n".join(out).strip(), full


TYP_EN = {"Anforderung": "requirement", "Information": "information",
          "TBD": "TBD", "Überschrift": "heading"}
VALIDITY_EN = {"gültig": "valid", "ungültig": "invalid", "entfallen": "dropped",
               "informativ": "informative", "tbd": "TBD", "-": "-"}

# ---------------------------------------------------------------- XHTML

def _text_of(el) -> str:
    return "".join(el.itertext())


def xhtml_to_md(xc) -> tuple[str, list[str]]:
    """Convert <XHTML-CONTENT> to markdown. Returns (md, [ole_basenames])."""
    if xc is None:
        return "", []
    oles: list[str] = []

    def conv(el) -> str:
        tag = el.tag
        if tag.startswith(XR):
            tag = tag[len(XR):]
        elif "}" in tag:
            tag = tag.split("}", 1)[1]
        if tag in ("XHTML-CONTENT", "div", "p"):
            s = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return s.strip()
        if tag == "br":
            return "\n"
        if tag == "b":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return f"**{inner.strip()}**" if inner.strip() else ""
        if tag == "i":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return f"*{inner.strip()}*" if inner.strip() else ""
        if tag == "u":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return f"<u>{html.escape(inner.strip())}</u>" if inner.strip() else ""
        if tag == "sub":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return f"<sub>{html.escape(inner)}</sub>"
        if tag == "sup":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return f"<sup>{html.escape(inner)}</sup>"
        if tag == "ul":
            items = []
            for c in el:
                ctag = c.tag[len(XR):] if c.tag.startswith(XR) else c.tag
                if ctag == "li":
                    t = (c.text or "") + "".join(conv(g) + (g.tail or "") for g in c)
                    t = re.sub(r"\s*\n\s*", " ", t).strip()
                    items.append(f"- {t}")
            return "\n" + "\n".join(items) + "\n"
        if tag == "li":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return inner
        if tag == "a":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            href = el.get("href", "")
            return f"[{inner}]({href})" if href else inner
        if tag == "object":
            data = el.get("data", "")
            name = el.get("name", "")
            base = os.path.basename(data) if data else name
            if base:
                oles.append(base)
                return f"\n\n{{{{FIGURE:{base}}}}}\n\n"
            return ""
        if tag == "img":
            return ""
        # fallback: recurse
        return (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)

    parts = []
    for child in list(xc):
        parts.append(conv(child) + (child.tail or ""))
    if xc.text and xc.text.strip():
        parts.insert(0, xc.text)
    md = "".join(parts)
    md = html.unescape(md)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    # escape nothing else; keep markdown chars from source as-is (DOORS text is plain)
    return md, oles


def md_escape_table(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", "<br/>")


def sanitize(s: str) -> str:
    """Drop NUL/control chars that occasionally leak from WMF dx-arrays or
    legacy DOORS exports; collapse stray whitespace."""
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", s or "")
    return re.sub(r"[ \t]+\n", "\n", s)

# ---------------------------------------------------------------- RIF parse

def parse_module(xml_path: str):
    tree = ET.parse(xml_path)
    root = tree.getroot()
    creation = (root.find("r:CREATION-TIME", NS).text
                if root.find("r:CREATION-TIME", NS) is not None else "")
    author = (root.find("r:AUTHOR", NS).text
              if root.find("r:AUTHOR", NS) is not None else "")
    tool = (root.find("r:SOURCE-TOOL-ID", NS).text
            if root.find("r:SOURCE-TOOL-ID", NS) is not None else "")

    defmap: dict[str, str] = {}
    for at in root.findall(".//r:SPEC-TYPE/r:SPEC-ATTRIBUTES/*", NS):
        aid = at.find("r:IDENTIFIER", NS).text
        ln = at.find("r:LONG-NAME", NS).text
        defmap[aid] = ln
    enum_map: dict[str, str] = {}
    for ev in root.findall(".//r:ENUM-VALUE", NS):
        enum_map[ev.find("r:IDENTIFIER", NS).text] = ev.find("r:LONG-NAME", NS).text

    objects: dict[str, dict] = {}
    for o in root.findall(".//r:SPEC-OBJECT", NS):
        oid = o.find("r:IDENTIFIER", NS).text
        longn = o.find("r:LONG-NAME", NS).text if o.find("r:LONG-NAME", NS) is not None else ""
        vals: dict[str, tuple[str, list[str]]] = {}
        enums: dict[str, str] = {}
        for v in o.findall("r:VALUES/*", NS):
            dnode = v.find("r:DEFINITION/*", NS)
            if dnode is None:
                continue
            name = defmap.get(dnode.text, dnode.text)
            xc = v.find("r:XHTML-CONTENT", NS)
            if xc is not None:
                md, oles = xhtml_to_md(xc)
                vals[name] = (md, oles)
            else:
                refs = v.findall("r:VALUES/r:ENUM-VALUE-REF", NS)
                if refs:
                    enums[name] = enum_map.get(refs[0].text, refs[0].text)
                else:
                    vals[name] = ("", [])
        objects[oid] = {"long": longn, "vals": vals, "enums": enums}

    # hierarchy DFS order with depth
    order: list[tuple[str, int]] = []
    def walk(h, depth):
        obj = h.find("r:OBJECT/r:SPEC-OBJECT-REF", NS)
        if obj is not None:
            order.append((obj.text, depth))
        for ch in h.findall("r:CHILDREN/r:SPEC-HIERARCHY", NS):
            walk(ch, depth + 1)
    for hr in root.findall(".//r:SPEC-HIERARCHY-ROOT", NS):
        for ch in hr.findall("r:CHILDREN/r:SPEC-HIERARCHY", NS):
            walk(ch, 1)
        # some modules nest directly under root object?
        o = hr.find("r:OBJECT/r:SPEC-OBJECT-REF", NS)
        if o is not None:
            order.append((o.text, 0))
    # fallback: any objects missing from hierarchy get appended
    seen = {o for o, _ in order}
    for oid in objects:
        if oid not in seen:
            order.append((oid, 1))
    return {"creation": creation, "author": author, "tool": tool,
            "objects": objects, "order": order, "defmap": defmap}


def g(vals, key):
    v = vals.get(key)
    return sanitize(v[0].strip()) if v else ""


def oles_of(vals, *keys):
    out = []
    for k in keys:
        v = vals.get(k)
        if v:
            out.extend(v[1])
    # dedupe, keep order
    seen, res = set(), []
    for o in out:
        if o not in seen:
            seen.add(o)
            res.append(o)
    return res

# ---------------------------------------------------------------- figure block

def figure_block(ole_base: str, module_dir: str, req_id: str, lang: str) -> str:
    from urllib.parse import quote
    png = re.sub(r"\.ole$", ".png", ole_base, flags=re.I)
    # NOTE: RELATIVE asset URL (../../assets/…). All module pages live two
    # levels deep (/<lang>/<slug>/), so this resolves correctly both with the
    # default base '/' and with a GitHub-Pages project base '/<repo>/' —
    # absolute '/assets/…' URLs would break under a project base.
    asset_url = f"../../assets/{quote(module_dir)}/{quote(png)}"
    txt_name = re.sub(r"\.ole$", ".txt", ole_base, flags=re.I)
    txt_path = os.path.join(REPO, "docs", "public", "assets", module_dir, txt_name)
    en_path = os.path.join(REPO, "docs", "public", "assets", module_dir,
                           re.sub(r"\.ole$", ".en.txt", ole_base, flags=re.I))
    de_trans = ""
    if os.path.exists(txt_path):
        with open(txt_path, encoding="utf-8", errors="replace") as fh:
            de_trans = fh.read().strip()
    en_trans = ""
    if os.path.exists(en_path):
        with open(en_path, encoding="utf-8", errors="replace") as fh:
            en_trans = fh.read().strip()
    # quality gate on the GERMAN source (same decision in both languages):
    # show the transcription only when it reads like real text (table
    # contents), not scattered drawing labels / dimension fragments.
    gtlines = [t for t in de_trans.splitlines() if t.strip()]
    gwordy = sum(1 for t in gtlines if re.search(r"[A-Za-zÄÖÜäöüß]{3,}", t))
    avg_len = (sum(len(t.strip()) for t in gtlines) / max(1, len(gtlines))) if gtlines else 0
    show_trans = bool(gtlines) and (
        (gwordy / max(1, len(gtlines)) >= 0.4 and avg_len >= 10) or gwordy >= 12)
    if lang == "en" and en_trans:
        transcription, trans_origin = en_trans, "en-mt"
    else:
        transcription, trans_origin = de_trans, "de"
    # download candidates produced by ole_to_png (original .doc/.xls/.bin/.ole)
    dl_links = []
    adir = os.path.join(REPO, "docs", "public", "assets", module_dir)
    stem = re.sub(r"\.ole$", "", ole_base, flags=re.I)
    if os.path.isdir(adir):
        for f in sorted(os.listdir(adir)):
            if f.startswith(stem + ".") and not f.endswith((".png", ".txt")):
                dl_links.append(f)
    # NOTE: figcaption is a SINGLE line — MDX fails on mixed text content
    # spanning multiple lines inside inline-level JSX ("expected a closing
    # tag ... before the end of paragraph").
    cap = "Figure" if lang == "en" else "Abbildung"
    cap_line = (f"  <figcaption>{cap} <code>{html.escape(stem)}</code> · {html.escape(req_id)} — "
                f"<a href=\"{asset_url}\" target=\"_blank\" rel=\"noopener\">open full size</a>")
    if dl_links:
        links = " · ".join(
            f"<a href=\"../../assets/{quote(module_dir)}/{quote(f)}\" download>source <code>{html.escape(f)}</code></a>"
            for f in dl_links)
        cap_line += f" <br/><span class=\"fig-src\">Embedded source: {links}</span>"
    cap_line += "</figcaption>"
    lines = [
        f"<figure class=\"req-figure\" id=\"fig-{html.escape(stem)}\">",
        f"  <a href=\"{asset_url}\" target=\"_blank\" rel=\"noopener\">",
        f"    <img src=\"{asset_url}\" alt=\"{cap} {html.escape(stem)} ({html.escape(req_id)})\" loading=\"lazy\" />",
        f"  </a>",
        cap_line,
        "</figure>",
    ]
    if transcription and show_trans:
        # NOTE: the transcript <details> is a SIBLING of <figure> (not nested
        # inside it) and its body is pure HTML (<br/> / <table>). Raw Markdown
        # (blockquotes `>`, `|` tables) inside JSX is either an MDX parse
        # error or renders unprocessed — pure HTML always works and keeps the
        # text searchable for Pagefind.
        if lang == "en":
            det_label = ("Transcription (English, machine-translated — searchable text)"
                         if trans_origin == "en-mt"
                         else "Transcription (searchable text)")
        else:
            det_label = "Transkription (durchsuchbarer Text)"
        tlines = [t for t in transcription.splitlines()]

        def _html_cell(s: str) -> str:
            # pure-HTML escaping for content nested inside JSX: escape
            # &<>" plus MDX-hostile { }, then restore **bold**.
            t = html.escape(s, quote=False).replace("{", "&#123;").replace("}", "&#125;")
            return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)

        # NOTE: transcript body must stay INLINE-level markup (<br/>, <span>,
        # <code>) — block-level children (<blockquote>, <table>) inside
        # <details> in JSX flow break the MDX parser. Cells of table-like
        # transcriptions are joined with " | " so no information is lost.
        # NOTE 2: blank line between </figure> and <details> is REQUIRED —
        # without it the MDX parser treats <details> as part of the <figure>
        # JSX element and fails ("expected closing tag before end of paragraph").
        lines.append("")
        # NOTE: <summary> MUST be on its own line (not trailing the
        # <details ATTRS> opener). `<details class="…"><summary>…</summary>`
        # on one line followed by block content is a fatal MDX parse error
        # in this toolchain ("expected closing tag before end of paragraph"),
        # while the split form parses fine (verified with the project compiler).
        lines.append(f"<details class=\"fig-transcript\">")
        lines.append(f"<summary>{det_label}</summary>")
        body = "<br/>".join(_html_cell(t) for t in tlines)
        lines.append(f"<span class=\"fig-transcript-body\">{body}</span>")
        lines.append("</details>")
    return "\n".join(lines)


# inline tags whose balance we enforce (the MT model occasionally drops or
# duplicates one side, e.g. `I<sub>a </sub>` -> `I </sub>a </sub>`; a stray
# `</sub>` or `<u>` is a fatal MDX build error, so we repair mechanically).
BALANCED_INLINE_TAGS = frozenset({"a", "code", "em", "span", "strong", "sub", "sup", "u"})


def balance_inline_tags(s: str) -> str:
    """Drop stray closers and unclosed openers for BALANCED_INLINE_TAGS.

    Only exact `<name>` / `</name>` (and `<a …>`) markers are considered —
    everything else has already been escaped upstream. Text is preserved;
    only broken formatting markers are removed, so MDX/JSX nesting stays
    valid no matter what DOORS or the MT model emitted.
    """
    parts = re.split(r"(</?[A-Za-z][^<>\n]*>)", s or "")
    # first pass: drop closers without a matching opener
    depth: dict[str, int] = {}
    kept: list[str] = []
    for p in parts:
        m = re.fullmatch(r"</([A-Za-z][A-Za-z0-9]*)>", p or "")
        if m and m.group(1).lower() in BALANCED_INLINE_TAGS:
            name = m.group(1).lower()
            if depth.get(name, 0) > 0:
                depth[name] -= 1
                kept.append(p)
            # else: stray closer -> drop
        else:
            kept.append(p)
            mo = re.fullmatch(r"<([A-Za-z][A-Za-z0-9]*)(\s[^<>]*)?>", p or "")
            if mo and mo.group(1).lower() in BALANCED_INLINE_TAGS:
                depth[mo.group(1).lower()] = depth.get(mo.group(1).lower(), 0) + 1
    # second pass (reverse): drop openers that were never closed
    depth2: dict[str, int] = {}
    out: list[str] = []
    for p in reversed(kept):
        m = re.fullmatch(r"</([A-Za-z][A-Za-z0-9]*)>", p or "")
        if m and m.group(1).lower() in BALANCED_INLINE_TAGS:
            depth2[m.group(1).lower()] = depth2.get(m.group(1).lower(), 0) + 1
            out.append(p)
            continue
        mo = re.fullmatch(r"<([A-Za-z][A-Za-z0-9]*)(\s[^<>]*)?>", p or "")
        if mo and mo.group(1).lower() in BALANCED_INLINE_TAGS:
            name = mo.group(1).lower()
            if depth2.get(name, 0) > 0:
                depth2[name] -= 1
                out.append(p)
            # else: unclosed opener -> drop (keep its text, which stays)
            continue
        out.append(p)
    return "".join(reversed(out))


def inline_md(s: str) -> str:
    """Minimal markdown→HTML for single-line contexts inside JSX/HTML blocks
    (MDX does not process `**`/` `` ` inside raw HTML). Keeps an allowlist of
    inline tags (sub/sup/u/code/br/a/span), escapes everything else, and
    converts `**x**` to <strong>."""
    allowed = {"sub", "sup", "u", "code", "br", "a", "span", "em", "strong"}
    # NOTE: `<` starts a tag only when followed by a letter (or `/`+letter).
    # A bare `<` before digits/spaces (`<0,4`, `<75%`, `a < b`) is prose and
    # must be escaped — otherwise `<0,4…/cm<sup>` would be swallowed as one
    # chunk and the real `<sup>` lost (dangling `</sup>` breaks the MDX build).
    parts = re.split(r"(</?[A-Za-z][^>]*>)", s or "")
    out = []
    for p in parts:
        if not p:
            continue
        if p.startswith("<") and p.endswith(">"):
            m = re.match(r"</?([A-Za-z0-9]+)", p)
            out.append(p if m and m.group(1).lower() in allowed else html.escape(p))
        else:
            t = html.escape(p, quote=False)
            t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
            t = t.replace("{", "&#123;").replace("}", "&#125;")
            out.append(t)
    return balance_inline_tags("".join(out))


# tags that may pass through into MDX output untouched (all other
# angle-bracket constructs are escaped — a bare `<`, `{` or `}` in JSX
# scanned content is a fatal MDX build error, e.g. "<75%").
MDX_ALLOWED_TAGS = frozenset({
    "a", "br", "code", "details", "div", "em", "figcaption", "figure",
    "img", "span", "strong", "sub", "summary", "sup", "u",
})


def body_mdx_safe(s: str) -> str:
    """Escape MDX/JSX-hostile characters in text nodes while keeping an
    allowlist of HTML tags. `**bold**`, lists and tables pass through (they
    are real markdown); `<`, `{`, `}` and bare `&` in prose become entities.
    A `<` only starts a tag when followed by a letter (`</?letter`); a bare
    `<` before digits/spaces (`<0,4`, `<75%`) is prose (see inline_md note).
    """
    parts = re.split(r"(</?[A-Za-z][^>\n]*>?|<!--[^\n]*-->?)", s or "")
    out = []
    for p in parts:
        if not p:
            continue
        if p.startswith("<"):
            m = re.match(r"</?([A-Za-z0-9]+)", p)
            if m and m.group(1).lower() in MDX_ALLOWED_TAGS and p.endswith(">"):
                out.append(p)
            else:
                out.append(html.escape(p))
        else:
            t = p.replace("&", "\x00AMP\x00")
            t = html.escape(t, quote=False).replace("\x00AMP\x00", "&")
            # restore well-formed entities the text may already contain
            t = re.sub(r"&amp;(#\d+|\w+;)", r"&\1", t)
            t = t.replace("{", "&#123;").replace("}", "&#125;")
            out.append(t)
    # MT/DOORS noise can leave e.g. a stray `</sub>` behind (opener dropped);
    # repair so JSX nesting is always valid.
    return balance_inline_tags("".join(out))


def badges(typ, validity, st_vw, st_sup, lang):
    b = []
    if typ:
        if lang == "en" and typ in TYP_EN and TYP_EN[typ] != typ:
            b.append(f"<span class=\"badge badge-type\" title=\"{html.escape(typ)}\"><code>{html.escape(typ)}</code> · {html.escape(TYP_EN[typ])}</span>")
        else:
            b.append(f"<span class=\"badge badge-type\"><code>{html.escape(typ)}</code></span>")
    if validity:
        v = validity.strip()
        if lang == "en" and v.lower() in VALIDITY_EN and VALIDITY_EN[v.lower()] != v:
            en_v = VALIDITY_EN[v.lower()]
            cls = "ok" if en_v.startswith(("v", "g")) else ""
            b.append(f"<span class=\"badge badge-valid {cls}\" title=\"DE: {html.escape(v)}\">{html.escape(en_v)}</span>")
        else:
            cls = "ok" if v.lower().startswith(("g", "v")) else ""
            b.append(f"<span class=\"badge badge-valid {cls}\">{html.escape(v)}</span>")
    if st_vw:
        b.append(f"<span class=\"badge\">VW: {html.escape(st_vw)}</span>")
    if st_sup:
        sup_label = "Supplier" if lang == "en" else "Lieferant"
        b.append(f"<span class=\"badge\">{sup_label}: {html.escape(st_sup)}</span>")
    return " ".join(b)

# ---------------------------------------------------------------- page render

def note(text: str) -> str:
    """Starlight aside (:::note) — renders as a callout box on the site and
    degrades to plain text in the GitHub mirror."""
    return ":::note\n" + text.strip() + "\n:::"


def filter_html(lang: str) -> str:
    if lang == "en":
        ph = "Filter by ID or text…"
        all_opt = "Type: all"
        opts = [("Anforderung", "requirement"), ("Information", "information"),
                ("TBD", "TBD"), ("Überschrift", "heading")]
        opt_html = "\n".join(
            f'    <option value="{v}">{v} · {lbl}</option>' for v, lbl in opts)
    else:
        ph = "Nach ID oder Text filtern…"
        all_opt = "Typ: alle"
        opt_html = ("\n".join(
            f'    <option value="{v}">{v}</option>'
            for v in ("Anforderung", "Information", "TBD", "Überschrift")))
    head = f"""
<div class="req-filter" role="search">
  <input id="req-q" type="search" placeholder="{ph}" aria-label="Filter requirements" />
  <select id="req-t" aria-label="Filter by type">
    <option value="">{all_opt}</option>
{opt_html}
  </select>
  <span class="req-count" id="req-count"></span>
</div>
"""
    # NOTE: the companion filter behaviour script is NOT inlined here (MDX
    # cannot contain raw <script> with braces). It is loaded globally via
    # docs/astro.config.mjs `head` from docs/src/scripts/req-filter.js.
    return head


def render_requirement(oid_ref, vals, enums, module_dir, lang, anchor):
    obj_id = g(vals, "Object ID") or anchor
    typ = enums.get("Typ", "")
    validity = g(vals, "Validity_MQB-37_R-EPS")
    version = g(vals, "Version")
    st_vw = enums.get("Status VW MQB-37W R-EPS Nexteer", "")
    st_sup = enums.get("Status Nexteer MQB-37W R-EPS", "")
    c_vw = g(vals, "Comment VW MQB-37W R-EPS Nexteer")
    c_sup = g(vals, "Comment Nexteer MQB-37W R-EPS")
    if lang == "de":
        head = g(vals, "Object Heading")
        text = g(vals, "Object Text")
        oles = oles_of(vals, "Object Heading", "Object Text")
        mt_used, mt_partial = False, False
    else:
        head_en = g(vals, "Object Heading English")
        text_en = g(vals, "Object Text English")
        head_de = g(vals, "Object Heading")
        text_de = g(vals, "Object Text")
        # Figures: DOORS keeps parallel variants per language (_2_1 = German,
        # _9_1 = English) of the same drawing. Prefer the English variants on
        # EN pages; fall back to German only when no English figure exists
        # (otherwise both variants would render side by side).
        en_oles = oles_of(vals, "Object Heading English", "Object Text English")
        de_oles = oles_of(vals, "Object Heading", "Object Text")
        oles = en_oles if en_oles else de_oles
        # official EN first, MT (Helsinki + glossary) second, German fallback last
        mt_used, mt_partial = False, False
        if head_en:
            head, head_mt = head_en, False
        elif head_de:
            head, head_full = mt_translate(head_de)
            head_mt = True
            mt_used = True
            mt_partial = mt_partial or not head_full
        else:
            head, head_mt = "", False
        if text_en:
            text, text_mt = text_en, False
        elif text_de:
            # translate only the prose; FIGURE tokens are preserved by MT,
            # but mask them anyway for absolute safety
            figs = re.findall(r"\{\{FIGURE:.+?\}\}", text_de)
            masked = re.sub(r"\{\{FIGURE:.+?\}\}", "{{FIGURE}}", text_de)
            text, text_full = mt_translate(masked)
            text = re.sub(r"\{\{FIGURE\}\}",
                          lambda _m: figs.pop(0) if figs else "{{FIGURE}}", text)
            text_mt = True
            mt_used = True
            mt_partial = mt_partial or not text_full
        else:
            text, text_mt = "", False
    search = f"{obj_id} {head} {text} {typ} {validity} {st_vw} {st_sup}"
    if lang == "en":
        search += f" {TYP_EN.get(typ, '')} {VALIDITY_EN.get(validity.strip().lower(), '')}"
        if mt_used:
            search += " MT machine-translated"
    search = re.sub(r"\{\{FIGURE[^}]*\}\}?", " ", search)  # never leak placeholders
    search = re.sub(r"<[^>]+>", " ", search)
    search = html.unescape(search)
    search = re.sub(r"\s+", " ", search)
    # Per-requirement MT marking is a compact badge (not a full :::note callout:
    # 900+ duplicate callouts per page destroyed readability). The page-level
    # note in render_module() explains the MT pipeline once.
    if lang == "en" and mt_used:
        if mt_partial:
            mt_badge = (" <span class=\"badge badge-mt\" title=\"Partly machine-translated from German "
                        "(Helsinki MT + automotive glossary); untranslated lines show the German original. "
                        "Official DOORS English preferred where present.\">MT-partial</span>")
        else:
            mt_badge = (" <span class=\"badge badge-mt\" title=\"Machine-translated from German "
                        "(Helsinki MT + automotive glossary). Official DOORS English preferred where present — "
                        "German original on the DE pages.\">MT</span>")
    else:
        mt_badge = ""
    out = [f"<div class=\"req-card\" id=\"{html.escape(obj_id)}\" data-type=\"{html.escape(typ)}\" "
           f"data-search=\"{html.escape(search)}\">"]
    if typ == "Überschrift":
        pass  # heading rendered by caller; card keeps anchor only
    out.append(f"<div class=\"req-head\"><code>{html.escape(obj_id)}</code> {badges(typ, validity, st_vw, st_sup, lang)}{mt_badge}"
               + (f" — <strong>{inline_md(head)}</strong>" if head else "")
               + "</div>\n")
    if text:
        # figures
        def repl(m):
            return "\n\n" + figure_block(m.group(1), module_dir, obj_id, lang) + "\n\n"
        text = re.sub(r"\{\{FIGURE:(.+?)\}\}", repl, text)
        # drop any mangled leftover tokens (safety net below re-appends figures)
        text = re.sub(r"\{\{FIGURE[^}]*\}\}?", "", text)
        # escape MDX-hostile chars in prose (keeps markdown + allowlisted tags)
        text = body_mdx_safe(text)
        out.append(text)
        out.append("")
    # figures referenced only via oles list but not inline (safety net)
    inline_figs = set(re.findall(r"fig-(.+?)[\"\s]", "\n".join(out)))
    for ob in oles:
        stem = re.sub(r"\.ole$", "", ob, flags=re.I)
        if stem not in inline_figs:
            out.append(figure_block(ob, module_dir, obj_id, lang))
            out.append("")
    rows = []
    if version:
        rows.append(("Version", md_escape_table(version)))
    if validity:
        v = validity.strip()
        if lang == "en" and v.lower() in VALIDITY_EN and VALIDITY_EN[v.lower()] != v:
            rows.append(("Validity", f"{md_escape_table(VALIDITY_EN[v.lower()])} (DE: {md_escape_table(v)})"))
        else:
            rows.append(("Validity" if lang == "en" else "Gültigkeit", md_escape_table(v)))
    if st_vw:
        rows.append(("Status VW", md_escape_table(st_vw)))
    if st_sup:
        rows.append(("Status supplier" if lang == "en" else "Status Lieferant", md_escape_table(st_sup)))
    if rows:
        out.append("| | |")
        out.append("|---|---|")
        for a, b in rows:
            out.append(f"| {a} | {b} |")
        out.append("")
    if c_vw or c_sup:
        label = "Comments" if lang == "en" else "Kommentare"
        out.append("<details>\n<summary>" + label + "</summary>\n")
        if c_vw:
            out.append(f"<strong>VW:</strong> {inline_md(c_vw)}\n")
        if c_sup:
            out.append(f"<strong>Nexteer:</strong> {inline_md(c_sup)}\n")
        out.append("</details>\n")
    out.append("</div>")
    return "\n".join(out), typ, head


def render_module(module_dir, slug, xml_path, stats_acc):
    data = parse_module(xml_path)
    objects, order = data["objects"], data["order"]
    # coverage
    n_req = n_info = n_tbd = n_head = n_fig = n_en = 0
    for oid, _d in order:
        o = objects.get(oid)
        if not o:
            continue
        t = o["enums"].get("Typ", "")
        if t == "Anforderung":
            n_req += 1
        elif t == "Information":
            n_info += 1
        elif t == "TBD":
            n_tbd += 1
        elif "berschrift" in t:
            n_head += 1
        v = o["vals"]
        if g(v, "Object Text English") or g(v, "Object Heading English"):
            n_en += 1
        n_fig += len(oles_of(v, *list(v.keys())))
    total = len(order)
    stats_acc[slug] = {"module": module_dir, "total": total, "req": n_req,
                       "info": n_info, "tbd": n_tbd, "headings": n_head,
                       "figures": n_fig, "with_en": n_en,
                       "creation": data["creation"], "tool": data["tool"]}
    pages = {}
    for lang in ("de", "en"):
        title = DE_TITLE[module_dir] if lang == "de" else EN_TITLE[module_dir]
        desc = (f"DOORS RIF EXERPT 2016-05-09 · {total} Objekte · {n_fig} Abbildungen"
                if lang == "de" else
                f"DOORS RIF excerpt 2016-05-09 · {total} objects · {n_fig} figures (EN default, DE original)")
        L = []
        L.append("---")
        L.append(f"title: {json.dumps(title)}")
        L.append(f"description: {json.dumps(desc)}")
        L.append(f"sidebar: {json.dumps({'order': 10 + list(SLUG_OF.values()).index(slug)})}")
        L.append("---")
        L.append(f"# {title}")
        L.append("")
        if lang == "en":
            n_mt = total - n_en
            L.append(note("Default language is **English**. Official DOORS English is used where present "
                          f"({n_en}/{total} objects); the remaining {n_mt} objects carry a yellow **MT** badge "
                          "(machine-translated from German with Helsinki-NLP/opus-mt-de-en + automotive glossary; "
                          "type `MT` in the filter box to list them). "
                          "Use the language switcher for the full German edition."))
            L.append("")
        else:
            L.append(f"> Quelle: `{os.path.basename(xml_path)}` · Export {data['creation']} · "
                     f"{data['tool']} · {total} Objekte · {n_fig} eingebettete OLE-Abbildungen (als PNG).")
            L.append("")
        L.append(f"| {'Kennzahl' if lang=='de' else 'Metric'} | {'Wert' if lang=='de' else 'Value'} |")
        L.append("|---|---|")
        rows_meta = [("Objekte / objects", total), ("Anforderungen / requirements", n_req),
                     ("Informationen / information", n_info), ("TBD", n_tbd),
                     ("Überschriften / headings", n_head),
                     ("Abbildungen / figures", n_fig)]
        if lang == "en":
            rows_meta.append(("Official EN text", f"{n_en} ({round(100*n_en/total) if total else 0}%)"))
            rows_meta.append(("Machine-translated EN", f"{total-n_en}"))
        else:
            rows_meta.append(("Mit EN-Text", n_en))
        for k, v in rows_meta:
            L.append(f"| {k} | {v} |")
        L.append("")
        L.append(filter_html(lang))
        L.append("")
        # body in hierarchy order
        for oid, depth in order:
            o = objects.get(oid)
            if not o:
                continue
            anchor = o["long"] or oid
            card, typ, head = render_requirement(oid, o["vals"], o["enums"], module_dir, lang, anchor)
            is_head = "berschrift" in typ
            if is_head and head:
                lvl = min(4, max(2, depth + 1))
                # NOTE: no `{#id}` suffix — MDX parses `{…}` as expressions.
                # Deep links use the card's own id="ObjectID" below.
                L.append(f"{'#'*lvl} {body_mdx_safe(head)}")
                L.append("")
                # deep links use the card's own id="ObjectID" (rendered below);
                # no extra <a> anchor (would duplicate the id).
                L.append(card)
            else:
                L.append(card)
            L.append("")
        # footer: provenance
        L.append("---")
        if lang == "de":
            L.append(f"*Original: RIF `{(os.path.basename(xml_path))}` · DOORS-Export vom {data['creation']}. "
                     "OLE-Objekte (Word/Excel/Paint) wurden originalgetreu als PNG eingebettet "
                     "(Bitmap-DIBs 1:1, Vektor-WMF als saubere Nachzeichnung inkl. Transkription); "
                     "Originale sind pro Abbildung verlinkt.*")
        else:
            L.append(f"*Source RIF `{(os.path.basename(xml_path))}` ({data['creation']}). "
                     "Embedded OLE objects (Word/Excel/Paint) are embedded as PNG "
                     "(bitmap DIBs 1:1, vector WMF re-rendered + transcribed); originals linked per figure. "
                     "English: official DOORS text where available, otherwise machine-translated "
                     "(Helsinki-NLP/opus-mt-de-en + automotive glossary), marked per requirement.*")
        pages[lang] = "\n".join(L)
    return pages

# ---------------------------------------------------------------- index

def render_index(stats, lang):
    if lang == "de":
        title, desc = "EPS-Lastenhefte (RIF) — Dokumentation", "Alle DOORS-RIF-Module, zweisprachig, mit Abbildungen"
    else:
        title, desc = "EPS Specifications (RIF) — Documentation", "All DOORS RIF modules, bilingual, with figures"
    L = ["---", f"title: {json.dumps(title)}", f"description: {json.dumps(desc)}",
         'sidebar: {"order": 1}', "---", f"# {title}", ""]
    if lang == "en":
        L.append(note("Default language **English**. Official DOORS English is used where present; "
                      "remaining objects carry a yellow **MT** badge (machine-translated from German "
                      "with Helsinki MT + automotive glossary). "
                      "Switch to **DE** for the German original."))
    else:
        L.append(note("Deutsche Originalausgabe des DOORS-RIF-Exzerpts vom 09.05.2016 (Export 17.05.2016). "
                      "Die englische Ausgabe ist Standard (EN): offizielle DOORS-Übersetzung wo vorhanden, "
                      "sonst maschinell übersetzt (Helsinki MT + Glossar, pro Anforderung mit gelbem **MT**-Badge markiert)."))
    L += ["", f"| {'Modul' if lang=='de' else 'Module'} | {'Objekte' if lang=='de' else 'Objects'} | "
             f"{'Anford.' if lang=='de' else 'Requir.'} | {'Abb.' if lang=='de' else 'Figs'} | "
             f"{'EN offiz.' if lang=='de' else 'EN offic.'} |",
           "|---|---|---|---|---|"]
    for mod_dir, slug, en_t, de_t in MODULES:
        s = stats.get(slug, {})
        tot = s.get("total", 0)
        enp = round(100 * s.get("with_en", 0) / tot) if tot else 0
        name = de_t if lang == "de" else en_t
        L.append(f"| [{name}](./{slug}/) | {tot} | {s.get('req',0)} | {s.get('figures',0)} | {enp}% + MT |")
    L += ["",
          ("## Hinweise zur Treue / Fidelity notes" if lang == "de" else "## Fidelity notes"),
          (("- Reihenfolge = DOORS-Spec-Hierarchy (wie Word-Export)."
            "\n- `Überschrift` → echte Markdown-Headings (TOC/Suche), sonst Anforderungskarten mit ID-Anker `#L_…`."
            "\n- OLE: Bitmap-DIBs pixelgenau; Vektor-WMF (Word/Excel-Tabellen) als lesbare Nachzeichnung + Transkription + Original-Download."
            "\n- Filterbox oben auf jeder Seite (ID/Text/Typ). Druck-CSS für Word-ähnlichen Ausdruck.")
           if lang == "de" else
            ("- Order = DOORS spec hierarchy (like the Word export)."
             "\n- `Überschrift` → real Markdown headings (TOC/search); otherwise requirement cards with ID anchors `#L_…`."
             "\n- OLE: bitmap DIBs pixel-exact; vector WMF (Word/Excel tables) re-rendered readably + transcribed (EN transcriptions machine-translated) + original download."
             "\n- English: official DOORS EN preferred; yellow **MT** badge = Helsinki-NLP/opus-mt-de-en + automotive glossary (type `MT` in the filter to list them)."
             "\n- Filter box on every page (ID/text/type). Print CSS for Word-like printing. Deep-link any requirement via `#<Object-ID>`."))]
    return "\n".join(L) + "\n"

# ---------------------------------------------------------------- main

def main():
    os.chdir(REPO)
    stats: dict = {}
    per_module_pages: dict[str, dict] = {}
    for mod_dir, slug, _en, _de in MODULES:
        xml = mod_dir + ".xml"
        if not os.path.exists(xml):
            print(f"SKIP missing {xml}")
            continue
        print(f".. {xml}")
        per_module_pages[mod_dir] = render_module(mod_dir, slug, xml, stats)

    for mod_dir, slug, _en, _de in MODULES:
        pages = per_module_pages.get(mod_dir)
        if not pages:
            continue
        for lang in ("en", "de"):
            out = os.path.join(REPO, "docs", "src", "content", "docs", lang, slug + ".mdx")
            os.makedirs(os.path.dirname(out), exist_ok=True)
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(pages[lang])
            # plain-MD mirror for GitHub browsing
            mir = os.path.join(REPO, "converted", DE_TITLE[mod_dir] if lang == "de" else EN_TITLE[mod_dir])
            os.makedirs(mir, exist_ok=True)
            fn = "README-DE.md" if lang == "de" else "README-EN.md"
            with open(os.path.join(mir, fn), "w", encoding="utf-8") as fh:
                fh.write(pages[lang].replace("../../assets/", "../../docs/public/assets/"))
        print(f"   wrote {slug} ({stats[slug]['total']} obj, {stats[slug]['figures']} fig)")

    for lang in ("en", "de"):
        out = os.path.join(REPO, "docs", "src", "content", "docs", lang, "index.mdx")
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w", encoding="utf-8") as fh:
            fh.write(render_index(stats, lang))
    with open(os.path.join(REPO, "docs", "src", "content", "docs", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=1, ensure_ascii=False)
    os.makedirs(os.path.join(REPO, "converted"), exist_ok=True)
    with open(os.path.join(REPO, "converted", "manifest.json"), "w", encoding="utf-8") as fh:
        json.dump(stats, fh, indent=1, ensure_ascii=False)
    tot = sum(s["total"] for s in stats.values())
    fig = sum(s["figures"] for s in stats.values())
    print(f"DONE: {len(stats)} modules, {tot} objects, {fig} figure refs")


if __name__ == "__main__":
    sys.exit(main())
