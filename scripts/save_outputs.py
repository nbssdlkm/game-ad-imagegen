#!/usr/bin/env python
"""
game-ad-imagegen / save_outputs.py - 归档 + contact sheet
==========================================================

把 image_gen_batch.py 出来的 N 张 PNG + meta 整理成发布版：
  out_dir/
    01.png, 02.png, ...
    meta.json
    contact_sheet.png

调用：
  python save_outputs.py --batch-meta batch_meta.json --out-dir final/run1/
"""
import argparse
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def make_contact_sheet(images: list[Path], out_path: Path,
                       cell_w: int = 480, cell_h: int = 270,
                       padding: int = 8, label_h: int = 28):
    """N 张图拼成一张 contact sheet"""
    n = len(images)
    if n == 0:
        return None

    # 网格：尽量正方形
    import math
    cols = math.ceil(math.sqrt(n))
    rows = math.ceil(n / cols)

    W = cols * (cell_w + padding) + padding
    H = rows * (cell_h + label_h + padding) + padding

    canvas = Image.new("RGB", (W, H), "white")
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 14)
    except Exception:
        font = ImageFont.load_default()

    for i, p in enumerate(images):
        r, c = divmod(i, cols)
        x = padding + c * (cell_w + padding)
        y = padding + r * (cell_h + label_h + padding)
        try:
            im = Image.open(p)
            im.thumbnail((cell_w, cell_h), Image.LANCZOS)
            px = x + (cell_w - im.width) // 2
            py = y + (cell_h - im.height) // 2
            canvas.paste(im, (px, py))
            draw.rectangle([x, y, x + cell_w, y + cell_h], outline="#cbd5e1", width=1)
        except Exception as e:
            draw.text((x + 4, y + 4), f"err: {e}", fill="red", font=font)
        # label
        draw.text((x + 4, y + cell_h + 4), p.name, fill="#0f172a", font=font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, optimize=True)
    return out_path


def archive(batch_meta_path: Path, out_dir: Path) -> dict:
    """从 batch_meta.json 收集 N 张图，重组成发布版"""
    batch_meta = json.loads(batch_meta_path.read_text(encoding="utf-8"))
    out_dir.mkdir(parents=True, exist_ok=True)

    saved = []
    revised = []
    for r in batch_meta.get("results", []):
        if r.get("http_status") != 200:
            continue
        src = Path(r["out_path"])
        if not src.exists():
            continue
        seq = r.get("seq", len(saved) + 1)
        dst = out_dir / f"{seq:02d}.png"
        # 复制（不移动，保留 batch run 原文件作 audit）
        import shutil
        shutil.copy2(src, dst)
        saved.append(dst)
        revised.append(r.get("revised_prompt"))

    contact = make_contact_sheet(saved, out_dir / "contact_sheet.png")

    final_meta = {
        "batch_meta_source": str(batch_meta_path),
        "config": batch_meta.get("config"),
        "saved_files": [p.name for p in saved],
        "revised_prompts": revised,
        "contact_sheet": str(contact.name) if contact else None,
        "ok_count": len(saved),
        "total": batch_meta.get("total"),
    }
    (out_dir / "meta.json").write_text(
        json.dumps(final_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"  archived: {len(saved)} 张 → {out_dir}")
    if contact:
        print(f"  contact_sheet: {contact}")
    return final_meta


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-meta", required=True, help="batch run 产生的 _batch_meta.json")
    ap.add_argument("--out-dir", required=True, help="发布版输出目录")
    args = ap.parse_args()
    archive(Path(args.batch_meta), Path(args.out_dir))


if __name__ == "__main__":
    main()
