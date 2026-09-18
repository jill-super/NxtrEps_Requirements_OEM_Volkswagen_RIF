#!/usr/bin/env python3
"""OLE (RTF-wrapped WMF) -> PNG extractor for DOORS RIF exports.

Handles two families found in this repo:
  * BITMAP WMF: WMF contains an embedded DIB (StretchDIB / DIBStretchBlt).
    -> decode DIB, wrap as BMP, convert to PNG with Pillow (1/4/8/24/32-bit).
  * VECTOR WMF: WMF contains ExtTextOut/TextOut + line/rect records
    (Word/Excel table fallback rendering).
    -> extract positioned text (cp1252), cluster into rows/columns,
       render a clean table-like PNG with Pillow + return markdown + text.

Also extracts the raw OLE objdata payload (Word/Excel compound document)
for download/archival purposes.

Usage:
    python tools/ole_to_png.py "<module dir>/123_2_1.ole" out_dir
    python tools/ole_to_png.py --all
"""
from __future__ import annotations
import io
import os
import re
import struct
import sys
import glob
from PIL import Image, ImageDraw, ImageFont

FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

# ---------------------------------------------------------------- helpers

def read_rtf(path: str) -> str:
    with open(path, "r", encoding="cp1252", errors="replace") as fh:
        return fh.read()


def extract_wmf_blobs(rtf: str) -> list[bytes]:
    """Return decoded WMF byte blobs from {\\pict\\wmetafile ... hex} sections."""
    blobs: list[bytes] = []
    for m in re.finditer(r"pichgoal\d+\s+([0-9a-fA-F\s\r\n]+)\}", rtf):
        hexblob = "".join(m.group(1).split())
        try:
            blobs.append(bytes.fromhex(hexblob))
        except ValueError:
            continue
    return blobs


def extract_objdata(rtf: str) -> bytes | None:
    """Return decoded \\objdata payload (OLE compound doc) if present."""
    m = re.search(r"\\objdata\s+([0-9a-fA-F\s\r\n]+)", rtf)
    if not m:
        return None
    # objdata hex runs until a non-hex token (\\result etc.). Take leading hex run.
    tail = m.group(1)
    hexrun = []
    for tok in re.split(r"\s+", tail):
        if re.fullmatch(r"[0-9a-fA-F]+", tok or ""):
            hexrun.append(tok)
        else:
            break
    if not hexrun:
        return None
    try:
        return bytes.fromhex("".join(hexrun))
    except ValueError:
        return None


def objclass_name(rtf: str) -> str:
    m = re.search(r"\\objclass\s+([^}\\]+)", rtf)
    return m.group(1).strip() if m else "unknown"


# ------------------------------------------------------- DIB -> PNG

def find_dibs(wmf: bytes):
    """Yield (offset, w, h, bpp, comp) for plausible BITMAPINFOHEADERs."""
    i = 0
    while True:
        j = wmf.find(b"\x28\x00\x00\x00", i)
        if j < 0:
            return
        if j + 40 <= len(wmf):
            try:
                bsize, bw, bh, planes, bpp, comp, simg, xp, yp, cu, ci = struct.unpack(
                    "<IiiHHIIIIII", wmf[j:j + 40])
                if (bsize == 40 and 0 < abs(bw) <= 10000 and 0 < abs(bh) <= 10000
                        and bpp in (1, 4, 8, 16, 24, 32) and planes == 1
                        and comp in (0, 1, 2, 3)):
                    yield (j, bw, bh, bpp, comp)
            except struct.error:
                pass
        i = j + 1


def dib_slice(wmf: bytes, offset: int) -> bytes:
    """Slice a single DIB (header+palette+pixels) starting at offset."""
    bsize, bw, bh, planes, bpp, comp, simg, xp, yp, cu, ci = struct.unpack(
        "<IiiHHIIIIII", wmf[offset:offset + 40])
    ah = abs(bh)
    if bpp <= 8:
        ncol = cu if cu != 0 else (1 << bpp)
        pal = ncol * 4
    else:
        pal = 12 if comp == 3 else 0
    if comp in (1, 2):
        # RLE compressed: take rest of WMF (Pillow BMP cannot handle RLE well;
        # we still try, capped).
        return wmf[offset:offset + 40 + pal + 1_000_000]
    stride = ((bw * bpp + 31) // 32) * 4
    total = 40 + pal + stride * ah
    return wmf[offset:offset + total]


def dib_to_pil(dib: bytes) -> Image.Image:
    bsize, bw, bh, planes, bpp, comp, simg, xp, yp, cu, ci = struct.unpack(
        "<IiiHHIIIIII", dib[:40])
    if bpp <= 8:
        ncol = cu if cu != 0 else (1 << bpp)
        pal_size = ncol * 4
    else:
        pal_size = 12 if comp == 3 else 0
    stride = ((bw * bpp + 31) // 32) * 4
    ah = abs(bh)
    need = 40 + pal_size + stride * ah
    dib = dib[:need] if len(dib) >= need else dib + b"\x00" * (need - len(dib))
    bf_off = 14 + 40 + pal_size
    bf_size = bf_off + stride * ah
    hdr = struct.pack("<2sIHHI", b"BM", bf_size, 0, 0, bf_off)
    img = Image.open(io.BytesIO(hdr + dib))
    img.load()
    # Normalize modes: 1/P -> RGB for consistent docs rendering (keep P palette if nice?)
    if img.mode == "P":
        img = img.convert("RGB")
    elif img.mode in ("1", "L"):
        img = img.convert("RGB")
    elif img.mode == "RGBA":
        # flatten on white (Word-like page)
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[3])
        img = bg
    return img


# ------------------------------------------------------- WMF vector parse

def parse_wmf_records(wmf: bytes):
    """Yield (func, params_bytes)."""
    if len(wmf) < 18:
        return
    off = 18
    n = len(wmf)
    while off + 6 <= n:
        size, func = struct.unpack("<IH", wmf[off:off + 6])
        if size == 0 or size > 200000:
            return
        end = off + size * 2
        if end > n + 2:
            return
        yield (func, wmf[off + 6:end])
        if func == 0x0000:
            return
        off = end


def _clean_wmf_string(raw: bytes) -> str:
    """Decode an ExtTextOut/TextOut string payload.

    DOORS-emitted WMF mixes ANSI (cp1252) text with trailing dx-advance
    WORD arrays and, occasionally, UTF-16LE runs. NUL bytes never occur in
    legitimate cp1252 text, so:
      * pure ANSI (no NUL) -> cp1252, control chars stripped;
      * NUL present -> try UTF-16LE on the even-length prefix; if that
        yields plausible word text use it, else keep cp1252 bytes up to the
        first NUL (drops dx-array garbage) and strip controls.
    """
    if not raw:
        return ""
    if b"\x00" not in raw:
        return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "",
                      raw.decode("cp1252", errors="replace"))
    even = raw[:len(raw) // 2 * 2]
    try:
        uni = even.decode("utf-16-le", errors="strict")
    except (UnicodeDecodeError, ValueError):
        uni = ""
    if uni and re.search(r"[A-Za-zÄÖÜäöüß]{2,}", uni):
        word_chars = sum(1 for ch in uni if ch.isalnum() or ch.isspace()
                         or ch in ".,;:!?-/()[]\"'’–—+*=%°§")
        if word_chars / max(1, len(uni)) > 0.6:
            return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", uni)
    head = raw.split(b"\x00")[0].decode("cp1252", errors="replace")
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", head)


def extract_vector_texts(wmf: bytes):
    """Return list of (x, y, text, font_height_hint)."""
    texts: list[tuple[int, int, str]] = []
    fonts: dict[int, dict] = {}
    order: list[tuple[str, dict]] = []  # GDI object table
    cur_font_h = 0
    for func, rec in parse_wmf_records(wmf):
        if func == 0x02FB and len(rec) >= 18:  # CreateFontIndirect
            try:
                h, w = struct.unpack("<hh", rec[:4])
                weight = struct.unpack("<h", rec[8:10])[0]
                italic = rec[10]
                # facename at offset 18, null-terminated
                face = rec[18:50].split(b"\x00")[0].decode("latin1", "replace")
                order.append(("font", {"h": h, "w": w, "weight": weight,
                                       "italic": italic, "face": face}))
            except Exception:
                order.append(("font", {"h": 0}))
        elif func in (0x02FA, 0x02FC) and len(rec) >= 2:
            order.append(("other", {}))
        elif func == 0x012D and len(rec) >= 2:  # SelectObject
            idx = struct.unpack("<H", rec[:2])[0]
            if idx < len(order) and order[idx][0] == "font":
                cur_font_h = abs(order[idx][1].get("h", 0))
        elif func == 0x0A32 and len(rec) >= 16:  # ExtTextOut
            try:
                y, x, slen, opts = struct.unpack("<hhhh", rec[:8])
                s = rec[16:16 + max(0, slen)]
                txt = _clean_wmf_string(s)
                if txt.strip():
                    texts.append((x, y, txt))
            except Exception:
                continue
        elif func in (0x0521,) and len(rec) >= 6:
            try:
                # META_TEXTOUT layout: len(2) string(len) y(2)? x(2)? - try common order
                slen = struct.unpack("<h", rec[:2])[0]
                if 0 < slen < 500:
                    s = rec[2:2 + slen]
                    y, x = struct.unpack("<hh", rec[2 + slen:2 + slen + 4])
                    txt = _clean_wmf_string(s)
                    if txt.strip():
                        texts.append((x, y, txt))
            except Exception:
                continue
    return texts


def _join_row_fragments(row):
    """Join [(x, frag)] sorted by x into a single line.

    Uses estimated char width to distinguish split-word fragments
    ('Ger'+'ä'+'te' -> 'Geräte', 'Are'+'a Network' -> 'Area Network')
    from real word gaps.
    """
    if not row:
        return ""
    gaps = []
    for (x1, f1), (x2, _f2) in zip(row, row[1:]):
        if len(f1) > 0 and x2 > x1:
            gaps.append((x2 - x1) / max(1, len(f1)))
    char_w = sorted(gaps)[len(gaps) // 2] if gaps else 30.0
    char_w = max(8.0, min(120.0, char_w))
    out = row[0][1]
    prev_x, prev_f = row[0]
    for x, frag in row[1:]:
        prev_end = prev_x + len(prev_f) * char_w
        gap = x - prev_end
        if gap < char_w * 0.6:
            # continuation of the same word (split umlaut etc.)
            if out.endswith(" ") and frag.startswith(" "):
                out += frag.lstrip(" ")
            elif out.endswith(" ") or frag.startswith(" "):
                out += frag
            else:
                out += frag
        elif gap < char_w * 3.5:
            if not out.endswith(" ") and not frag.startswith(" "):
                out += " "
            out += frag.lstrip(" ") if out.endswith(" ") and frag.startswith(" ") else frag
        else:
            # wide gutter (table columns) -> ensure separation
            if not out.endswith(" ") and not out.endswith("\t"):
                out += "   "
            out += frag.lstrip(" ")
        prev_x, prev_f = x, frag
    return re.sub(r"[ \t]+", " ", out).strip()


def cluster_rows(texts):
    """Group (x,y,txt) fragments into rows by y proximity. Returns rows:
    list of dicts with frags/line/x0/x1."""
    if not texts:
        return []
    by_y = sorted(texts, key=lambda t: t[1])
    # row threshold: median dy * 0.5, clamped 4..40
    dys = [b[1] - a[1] for a, b in zip(by_y, by_y[1:]) if 0 < b[1] - a[1] < 1000]
    if dys:
        dys.sort()
        med = dys[len(dys) // 2]
        thr = max(4, min(40, med * 0.6))
    else:
        thr = 8
    rows: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, int, str]] = []
    cur_y = by_y[0][1]
    for x, y, t in by_y:
        if abs(y - cur_y) <= thr:
            cur.append((x, y, t))
        else:
            rows.append(sorted([(x, tt) for x, _, tt in cur]))
            cur = [(x, y, t)]
            cur_y = y
    if cur:
        rows.append(sorted([(x, tt) for x, _, tt in cur]))
    merged = []
    for row in rows:
        out = _join_row_fragments(row)
        xs = [x for x, _ in row]
        merged.append({"y": 0, "x0": min(xs), "x1": max(xs),
                       "frags": row, "line": out})
    return merged


def vector_to_markdown(rows) -> str:
    """Best-effort markdown from clustered rows: 2-col definition tables -> table."""
    if not rows:
        return ""
    # detect 2-column layout: many rows have a fragment with x < split and one with x >= split
    all_x = sorted({x for r in rows for x, _ in r["frags"]})
    # candidate split: look for bimodal gap
    split = None
    if len(all_x) > 3:
        gaps = [(b - a, a, b) for a, b in zip(all_x, all_x[1:])]
        gaps.sort(reverse=True)
        # biggest gap in the middle third is likely the column gutter
        for g, a, b in gaps[:3]:
            if g > 20 and a > min(all_x) + 10 and b < max(all_x) - 5:
                split = (a + b) // 2
                break
    if split is not None:
        n_two = sum(1 for r in rows if any(x < split for x, _ in r["frags"]) and any(x >= split for x, _ in r["frags"]))
        if n_two >= max(2, len(rows) // 3):
            lines = ["| | |", "|---|---|"]
            for r in rows:
                left = _join_row_fragments([(x, f) for x, f in r["frags"] if x < split]).strip()
                right = _join_row_fragments([(x, f) for x, f in r["frags"] if x >= split]).strip()
                left = left.replace("|", "\\|")
                right = right.replace("|", "\\|")
                lines.append(f"| {left} | {right} |")
            return "\n".join(lines)
    return "\n\n".join(r["line"] for r in rows if r["line"])


def render_vector_png(texts, out_path: str, title: str = "") -> dict:
    """Render positioned texts to a clean Word-like PNG. Returns meta."""
    if not texts:
        raise ValueError("no texts")
    xs = [x for x, _, _ in texts]
    ys = [y for _, y, _ in texts]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    # estimate max line length for width
    rows = cluster_rows(texts)
    maxlen = max((len(r["line"]) for r in rows), default=20)
    W = 1600
    # height from row count
    nrows = max(1, len(rows))
    # row height in logical units
    dys = []
    srt = sorted(set(y for _, y, _ in texts))
    for a, b in zip(srt, srt[1:]):
        if 0 < b - a < 2000:
            dys.append(b - a)
    med_dy = sorted(dys)[len(dys) // 2] if dys else 40
    scale_x = W / max(50, (x1 - x0 + 120))
    # keep aspect: scale_y ~ scale_x but ensure readable line spacing
    line_px = max(22, min(44, int(med_dy * scale_x * 0.9)))
    scale_y = line_px / max(1, med_dy)
    H = int((y1 - y0 + med_dy * 1.5) * scale_y) + 40
    H = max(120, min(H, 4000))
    # if too tall, rescale
    img = Image.new("RGB", (W, H), "white")
    dr = ImageDraw.Draw(img)
    # light border like Word page
    dr.rectangle([4, 4, W - 5, H - 5], outline="#d4d4d8", width=2)
    try:
        f_reg = ImageFont.truetype(FONT_REGULAR, max(12, int(line_px * 0.72)))
        f_bold = ImageFont.truetype(FONT_BOLD, max(12, int(line_px * 0.72)))
    except Exception:
        f_reg = ImageFont.load_default()
        f_bold = f_reg
    for r in rows:
        # row baseline
        ry = r["frags"][0][0]  # x of first frag; need y: recover from texts
        # find y of this row (min y of frags)
        frag_xs = {x for x, _ in r["frags"]}
        y_vals = [y for x, y, _ in texts if x in frag_xs]
        base_y = min(y_vals) if y_vals else y0
        py = int((base_y - y0) * scale_y) + 20
        # draw fragments at scaled x
        for x, frag in r["frags"]:
            px = int((x - x0) * scale_x) + 24
            dr.text((px, py), frag, fill="black", font=f_reg)
    img.save(out_path)
    return {"width": W, "height": H, "rows": len(rows)}


# ------------------------------------------------------- main entry

def convert_ole(ole_path: str, out_dir: str, base: str | None = None) -> dict:
    """Convert one .ole file. Returns manifest dict."""
    os.makedirs(out_dir, exist_ok=True)
    stem = base or os.path.splitext(os.path.basename(ole_path))[0]
    rtf = read_rtf(ole_path)
    oclass = objclass_name(rtf)
    blobs = extract_wmf_blobs(rtf)
    res: dict = {"source": ole_path, "stem": stem, "class": oclass,
                 "png": None, "kind": None, "text": "", "markdown": "",
                 "objdata_file": None, "width": 0, "height": 0}
    # save objdata payload for download
    payload = extract_objdata(rtf)
    if payload:
        ext = ".bin"
        if payload.startswith(b"\xd0\xcf\x11\xe0"):
            if "Excel" in oclass:
                ext = ".xls"
            elif "Word" in oclass:
                ext = ".doc"
            else:
                ext = ".olebin"
        elif payload.startswith(b"BM"):
            ext = ".bmp"
        of = os.path.join(out_dir, stem + ext)
        with open(of, "wb") as fh:
            fh.write(payload)
        res["objdata_file"] = os.path.basename(of)
        res["objdata_class"] = oclass

    if not blobs:
        res["kind"] = "empty"
        return res
    wmf = max(blobs, key=len)
    dibs = list(find_dibs(wmf))
    # text record count
    ntext = 0
    for func, _rec in parse_wmf_records(wmf):
        if func in (0x0A32, 0x0521, 0x0531):
            ntext += 1
    big = [(o, w, h, b, c) for o, w, h, b, c in dibs if abs(w * h) > 50000]
    try:
        if big:
            # BITMAP path: largest DIB
            big.sort(key=lambda t: abs(t[1] * t[2]), reverse=True)
            o, w, h, b, c = big[0]
            dib = dib_slice(wmf, o)
            img = dib_to_pil(dib)
            # cap width for docs
            if img.width > 1600:
                r = 1600 / img.width
                img = img.resize((1600, int(img.height * r)), Image.LANCZOS)
            png = os.path.join(out_dir, stem + ".png")
            img.save(png)
            res.update({"png": os.path.basename(png), "kind": "bitmap",
                        "width": img.width, "height": img.height})
            # also harvest any overlay text as caption
            texts = extract_vector_texts(wmf)
            if texts:
                rows = cluster_rows(texts)
                res["text"] = "\n".join(r["line"] for r in rows)
                res["markdown"] = vector_to_markdown(rows)
        else:
            texts = extract_vector_texts(wmf)
            if not texts:
                # tiny DIB fallback (e.g. 218x30 symbol)
                if dibs:
                    dibs.sort(key=lambda t: abs(t[1] * t[2]), reverse=True)
                    o, w, h, b, c = dibs[0]
                    img = dib_to_pil(dib_slice(wmf, o))
                    # upscale tiny symbols x3 for readability
                    if img.width < 400:
                        img = img.resize((img.width * 3, img.height * 3), Image.NEAREST)
                    png = os.path.join(out_dir, stem + ".png")
                    img.save(png)
                    res.update({"png": os.path.basename(png), "kind": "bitmap-small",
                                "width": img.width, "height": img.height})
                else:
                    res["kind"] = "empty"
            else:
                rows = cluster_rows(texts)
                res["text"] = "\n".join(r["line"] for r in rows)
                res["markdown"] = vector_to_markdown(rows)
                png = os.path.join(out_dir, stem + ".png")
                meta = render_vector_png(texts, png, title=stem)
                res.update({"png": os.path.basename(png), "kind": "vector",
                            "width": meta["width"], "height": meta["height"],
                            "rows": meta["rows"]})
    except Exception as e:  # never fail the whole batch on one image
        res["kind"] = "error"
        res["error"] = str(e)
    # save sidecar text for search/indexing
    if res.get("text"):
        with open(os.path.join(out_dir, stem + ".txt"), "w", encoding="utf-8") as fh:
            fh.write(res["text"])
    return res


def main():
    import json
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        all_oles = sorted(glob.glob("*.ole") + glob.glob("*/*.ole"))
        if not all_oles:
            # fallback: run from anywhere inside the repo checkout
            here = os.path.dirname(os.path.abspath(__file__))
            repo = os.path.dirname(here)
            all_oles = sorted(glob.glob(os.path.join(repo, "*", "*.ole")))
        manifest = {}
        for p in all_oles:
            module = os.path.basename(os.path.dirname(p))
            out = os.path.join("docs", "public", "assets", module)
            print(f".. {p}")
            r = convert_ole(p, out)
            manifest[p] = r
            print(f"   -> {r.get('kind')} {r.get('png')}")
        with open("/tmp/ole_manifest.json", "w") as fh:
            json.dump(manifest, fh, indent=1, ensure_ascii=False)
        kinds = {}
        for v in manifest.values():
            kinds[v.get("kind")] = kinds.get(v.get("kind"), 0) + 1
        print(kinds)
    else:
        src = sys.argv[1]
        out = sys.argv[2] if len(sys.argv) > 2 else "/tmp/ole_out"
        print(convert_ole(src, out))


if __name__ == "__main__":
    main()
