#!/usr/bin/env python3
"""Bulk DE->EN translation for objects lacking official English text.

Uses Helsinki-NLP/opus-mt-de-en locally (no quota, requirements-style
"shall" English). Incremental with a persistent cache so runs are resumable:

  converted/mt_cache_de_en.json   {german_segment: english_segment}

Also writes per-figure English transcriptions:
  docs/public/assets/<module>/<stem>.en.txt

Usage:
  python3 tools/translate_en.py            # everything missing
  python3 tools/translate_en.py Basismodul # filter by module dir substring
  python3 tools/translate_en.py --stats    # show missing counts only
"""
from __future__ import annotations
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE = os.path.join(REPO, "converted", "mt_cache_de_en.json")

NS = {"r": "http://automotive-his.de/200706/rif",
      "x": "http://automotive-his.de/200706/rif-xhtml"}
XR = "{http://automotive-his.de/200706/rif-xhtml}"

# rule-based post fixes for known MT weaknesses (automotive VW terms)
POSTFIX = [
    (re.compile(r"load booklet", re.I), "specification"),
    (re.compile(r"Load Booklet"), "Specification"),
    (re.compile(r"component specification module", re.I), "component specification module"),
    (re.compile(r"rack-and-pinion force", re.I), "rack force"),
    (re.compile(r"steering wheel torque detection", re.I), "steering torque sensing"),
]


def load_cache() -> dict:
    if os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as fh:
            return json.load(fh)
    return {}


def save_cache(c: dict):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    tmp = CACHE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(c, fh, indent=1, ensure_ascii=False)
    os.replace(tmp, CACHE)


def xhtml_text(xc) -> str:
    """Plain-ish text with placeholders kept (for translationFRONT; formatting
    markers are preserved by the MT model, verified in testing)."""
    if xc is None:
        return ""
    parts: list[str] = []

    def conv(el) -> str:
        tag = el.tag[len(XR):] if el.tag.startswith(XR) else el.tag.split("}", 1)[-1]
        if tag in ("XHTML-CONTENT", "div", "p"):
            return (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
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
            return f"<u>{inner.strip()}</u>" if inner.strip() else ""
        if tag == "sub":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return f"<sub>{inner}</sub>"
        if tag == "sup":
            inner = (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)
            return f"<sup>{inner}</sup>"
        if tag == "ul":
            items = []
            for c in el:
                ctag = c.tag[len(XR):] if c.tag.startswith(XR) else c.tag
                if ctag == "li":
                    t = (c.text or "") + "".join(conv(g) + (g.tail or "") for g in c)
                    t = re.sub(r"\s*\n\s*", " ", t).strip()
                    items.append(f"- {t}")
            return "\n" + "\n".join(items) + "\n"
        if tag == "object":
            data = el.get("data", "")
            base = os.path.basename(data) if data else el.get("name", "")
            return f"\n\n{{{{FIGURE:{base}}}}}\n\n" if base else ""
        return (el.text or "") + "".join(conv(c) + (c.tail or "") for c in el)

    for child in list(xc):
        parts.append(conv(child) + (child.tail or ""))
    if xc.text and xc.text.strip():
        parts.insert(0, xc.text)
    import html as _h
    md = _h.unescape("".join(parts))
    return re.sub(r"\n{3,}", "\n\n", md).strip()


def collect(filter_sub: str | None = None):
    """Return work items: dicts {key, de} where key is stable cache key."""
    import glob as _g
    items: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(de: str):
        de = (de or "").strip()
        if not de:
            return
        # translate line-by-line to preserve lists/paragraphs; each
        # non-empty line is its own cache unit (do NOT mark the whole
        # blob seen first — single-line inputs would then be skipped).
        for line in de.split("\n"):
            s = line.strip()
            if s and s not in seen:
                seen.add(s)
                items.append((s, s))

    xmls = sorted(_g.glob(os.path.join(REPO, "*.xml")))
    for xml in xmls:
        mod = os.path.splitext(os.path.basename(xml))[0]
        if filter_sub and filter_sub.lower() not in mod.lower() and filter_sub != "--stats":
            continue
        tree = ET.parse(xml)
        root = tree.getroot()
        defmap = {}
        for at in root.findall(".//r:SPEC-TYPE/r:SPEC-ATTRIBUTES/*", NS):
            defmap[at.find("r:IDENTIFIER", NS).text] = at.find("r:LONG-NAME", NS).text
        for o in root.findall(".//r:SPEC-OBJECT", NS):
            vals: dict[str, str] = {}
            for v in o.findall("r:VALUES/*", NS):
                d = v.find("r:DEFINITION/*", NS)
                if d is None:
                    continue
                name = defmap.get(d.text, "")
                xc = v.find("r:XHTML-CONTENT", NS)
                if xc is not None:
                    vals[name] = xhtml_text(xc)
            if vals.get("Object Text English", "").strip() or vals.get("Object Heading English", "").strip():
                # has official EN: still may need heading or text individually
                if not vals.get("Object Heading English", "").strip() and vals.get("Object Heading", "").strip():
                    add(vals["Object Heading"])
                if not vals.get("Object Text English", "").strip() and vals.get("Object Text", "").strip():
                    add(vals["Object Text"])
            else:
                if vals.get("Object Heading", "").strip():
                    add(vals["Object Heading"])
                if vals.get("Object Text", "").strip():
                    add(vals["Object Text"])
    # figure transcriptions (vector .txt, German)
    import glob as _g2
    for txt in sorted(_g2.glob(os.path.join(REPO, "docs", "public", "assets", "*", "*.txt"))):
        if txt.endswith(".en.txt"):
            continue
        mod = os.path.basename(os.path.dirname(txt))
        if filter_sub and filter_sub.lower() not in mod.lower() and filter_sub != "--stats":
            continue
        en_side = txt[:-4] + ".en.txt"
        if os.path.exists(en_side):
            continue
        with open(txt, encoding="utf-8", errors="replace") as fh:
            for line in fh.read().splitlines():
                s = line.strip()
                if s and "|" not in s:  # table markdown handled line-wise too
                    add(s)
                elif s:
                    add(s)
    return items


def post_fix(s: str) -> str:
    for rx, rep in POSTFIX:
        s = rx.sub(rep, s)
    return s


def run(items: list[tuple[str, str]], cache: dict, batch: int = 32):
    from transformers import AutoTokenizer, AutoModelForSeq2SeqLM
    import torch
    mname = "Helsinki-NLP/opus-mt-de-en"
    tok = AutoTokenizer.from_pretrained(mname)
    mdl = AutoModelForSeq2SeqLM.from_pretrained(mname)
    mdl.eval()
    todo = [(k, s) for k, s in items if s not in cache]
    print(f"segments total={len(items)} cached={len(items)-len(todo)} to_translate={len(todo)}")
    for i in range(0, len(todo), batch):
        chunk = todo[i:i + batch]
        srcs = [s for _, s in chunk]
        inp = tok(srcs, return_tensors="pt", padding=True, truncation=True, max_length=512)
        with torch.no_grad():
            out = mdl.generate(**inp, max_length=512, num_beams=1)
        for (k, _s), o in zip(chunk, out):
            cache[k] = post_fix(tok.decode(o, skip_special_tokens=True).strip())
        save_cache(cache)
        print(f"  {min(i+batch, len(todo))}/{len(todo)}", flush=True)
    return cache


def write_figure_en(cache: dict):
    import glob as _g
    n = 0
    for txt in sorted(_g.glob(os.path.join(REPO, "docs", "public", "assets", "*", "*.txt"))):
        if txt.endswith(".en.txt"):
            continue
        en_side = txt[:-4] + ".en.txt"
        if os.path.exists(en_side):
            continue
        with open(txt, encoding="utf-8", errors="replace") as fh:
            lines = fh.read().splitlines()
        out = []
        for ln in lines:
            s = ln.strip()
            out.append(cache.get(s, ln) if s else "")
        with open(en_side, "w", encoding="utf-8") as fh:
            fh.write("\n".join(out).strip() + "\n")
        n += 1
    print(f"wrote {n} .en.txt figure transcriptions")


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    items = collect(arg)
    if arg == "--stats":
        cache = load_cache()
        missing = [s for _, s in items if s not in cache]
        print(f"segments={len(items)} cached={len(items)-len(missing)} missing={len(missing)}")
        chars = sum(len(s) for s in missing)
        print(f"chars to translate ≈ {chars}")
        return 0
    cache = load_cache()
    cache = run(items, cache)
    write_figure_en(cache)
    print(f"cache size={len(cache)} -> {CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
