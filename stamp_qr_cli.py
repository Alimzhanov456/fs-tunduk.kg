"""
Standalone helper: stamp a QR code (encoding any URL you give it) onto one
page of an existing PDF. Useful for quick local testing without running the
whole Flask site — e.g. to check how a QR looks on your real document layout
before wiring up the web app.

Usage:
    python stamp_qr_cli.py INPUT.pdf "https://yourdomain.tld/verify/AB12CD34" OUTPUT.pdf \
        --page 1 --x 40 --y 40 --size 110

Requires: qrcode[pil], reportlab, pypdf  (all already in requirements.txt)
"""

import argparse
import io

import qrcode
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.utils import ImageReader


def stamp(input_pdf: str, url: str, output_pdf: str,
          page: int = 1, x: float = 40, y: float = 40, size: float = 110):
    reader = PdfReader(input_pdf)
    page_index = max(page - 1, 0)
    if page_index >= len(reader.pages):
        raise SystemExit(f"PDF has only {len(reader.pages)} page(s); can't stamp page {page}")

    target = reader.pages[page_index]
    page_w = float(target.mediabox.width)
    page_h = float(target.mediabox.height)

    qr = qrcode.QRCode(border=1, box_size=10)
    qr.add_data(url)
    qr.make(fit=True)
    qr_img = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    overlay_buf = io.BytesIO()
    c = pdfcanvas.Canvas(overlay_buf, pagesize=(page_w, page_h))
    c.drawImage(ImageReader(qr_img), x, y, width=size, height=size, mask="auto")
    c.setFont("Helvetica", 6)
    c.drawString(x, max(y - 9, 2), f"Verify: {url}")
    c.save()
    overlay_buf.seek(0)

    overlay_reader = PdfReader(overlay_buf)
    target.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)

    with open(output_pdf, "wb") as f:
        writer.write(f)

    print(f"Wrote {output_pdf} (QR -> {url})")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input_pdf")
    parser.add_argument("url", help="The verify URL to encode, e.g. https://yourdomain.tld/verify/AB12CD34")
    parser.add_argument("output_pdf")
    parser.add_argument("--page", type=int, default=1)
    parser.add_argument("--x", type=float, default=40)
    parser.add_argument("--y", type=float, default=40)
    parser.add_argument("--size", type=float, default=110)
    args = parser.parse_args()
    stamp(args.input_pdf, args.url, args.output_pdf, args.page, args.x, args.y, args.size)
