"""
Verify site — a small self-hosted document verification service.

Mechanics (same idea as the government / bank QR-verification portals you've
seen, applied to YOUR OWN documents, on YOUR OWN domain):

    1. You (the admin) create a document record -> gets a unique ID.
    2. The site can stamp a QR code onto an existing PDF. The QR encodes
       this site's own verify URL for that ID (e.g. https://yourdomain.tld/verify/AB12CD34).
    3. Anyone who scans the QR is sent to that URL, which looks the ID up
       in the database and shows what's on file for it.
    4. You can add / edit documents any time from /admin — no redeploy needed.

IMPORTANT: This is a teaching/demo-grade project, not a production identity
system. Don't put real people's sensitive data (national ID numbers, real
financial data, etc.) into it unless you've added proper security review,
HTTPS, backups, and access controls beyond the basics here.
"""

import io
import os
import secrets
import string
from datetime import datetime

from flask import (
    Flask, render_template, request, redirect, url_for,
    session, send_file, abort, flash
)
from flask_sqlalchemy import SQLAlchemy
import qrcode
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.lib.utils import ImageReader

# --------------------------------------------------------------------------
# App / config
# --------------------------------------------------------------------------

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-change-me")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024  # 20 MB upload cap

# DATABASE_URL lets you point at a hosted Postgres (Neon, Supabase, Railway...)
# so your data survives redeploys even on hosts with an ephemeral filesystem.
# If it's not set, falls back to a local SQLite file (fine for local testing,
# NOT reliable on a host that wipes its disk on every deploy).
#
# Needs the driver in requirements-postgres.txt (not installed by default —
# see that file if `pip install` for it fails on your machine). If you used
# the "psycopg[binary]" alternative mentioned there instead of psycopg2,
# change the replacement below to "postgresql+psycopg://".
db_url = os.environ.get("DATABASE_URL", "sqlite:///local.db")
# Always use the psycopg (v3) driver explicitly for Postgres -- newer
# SQLAlchemy versions default to it anyway, and being explicit avoids
# "No module named psycopg / psycopg2" surprises.
if db_url.startswith("postgres://"):
    db_url = "postgresql+psycopg://" + db_url[len("postgres://"):]
elif db_url.startswith("postgresql://"):
    db_url = "postgresql+psycopg://" + db_url[len("postgresql://"):]
app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {"pool_pre_ping": True}

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "change-me")

db = SQLAlchemy(app)


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------

class Document(db.Model):
    __tablename__ = "documents"
    id = db.Column(db.String(16), primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    holder_name = db.Column(db.String(200), nullable=False)
    issue_date = db.Column(db.String(40), nullable=False)
    status = db.Column(db.String(60), default="Действителен")
    extra_fields = db.Column(db.JSON, default=list)  # [{"k": "...", "v": "..."}]
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class PdfFile(db.Model):
    """An uploaded PDF, served back as-is. The QR for one of these points
    straight at the file (it opens the PDF itself), unlike Document above
    (whose QR points at a data page)."""
    __tablename__ = "pdf_files"
    id = db.Column(db.String(16), primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    data = db.Column(db.LargeBinary, nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def new_id(length=8):
    alphabet = string.ascii_uppercase + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if not Document.query.get(candidate):
            return candidate


def new_pdf_id(length=8):
    alphabet = string.ascii_uppercase + string.digits
    while True:
        candidate = "".join(secrets.choice(alphabet) for _ in range(length))
        if not PdfFile.query.get(candidate):
            return candidate


def verify_url_for(doc_id: str) -> str:
    # request.host_url already reflects whatever domain the app is served on,
    # so this works unmodified on localhost, a *.onrender.com URL, and your
    # own custom domain once it's pointed at the host.
    return request.host_url.rstrip("/") + url_for("verify", doc_id=doc_id)


def pdf_url_for(pdf_id: str) -> str:
    # Same idea, but points straight at the PDF file itself rather than a
    # data page -- this is the URL that goes in the QR you print / embed.
    return request.host_url.rstrip("/") + url_for("view_pdf", pdf_id=pdf_id)


def root_url() -> str:
    # Bare domain, no trailing slash and no path -- e.g. "https://yourdomain.tld".
    # Computed from the request, so it's whatever domain the site is
    # actually opened on (localhost, onrender.com, or your real domain
    # once it's connected) -- nothing here is hardcoded.
    # This is what index() serves the latest PDF from.
    return request.host_url.rstrip("/")


def make_qr_image(data: str):
    qr = qrcode.QRCode(border=1, box_size=10)
    qr.add_data(data)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").convert("RGB")


def require_login():
    if not session.get("is_admin"):
        abort(redirect(url_for("login", next=request.path)))


# --------------------------------------------------------------------------
# Public routes
# --------------------------------------------------------------------------

STATUS_PAGE_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>erbol.kg</title>
<style>
  html,body{height:100%;margin:0}
  body{display:flex;flex-direction:column;align-items:center;justify-content:center;
       background:#0f1115;color:#e8eaed;font-family:system-ui,"Segoe UI",Arial,sans-serif;
       text-align:center;padding:24px;box-sizing:border-box}
  svg{width:min(60vw,280px);height:auto;margin-bottom:28px}
  h1{font-size:clamp(1.3rem,4.5vw,2rem);font-weight:600;margin:0}
</style>
</head>
<body>
  <svg viewBox="0 0 200 220" fill="none" stroke="#9aa0a6" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
    <rect x="25" y="20" width="150" height="50" rx="8"/>
    <rect x="25" y="85" width="150" height="50" rx="8"/>
    <rect x="25" y="150" width="150" height="50" rx="8"/>
    <path d="M52 37l16 16M68 37L52 53" stroke="#ea4335"/>
    <path d="M52 102l16 16M68 102L52 118" stroke="#ea4335"/>
    <path d="M52 167l16 16M68 167L52 183" stroke="#ea4335"/>
    <path d="M110 45h45M110 110h45M110 175h45" stroke-width="5"/>
  </svg>
  <h1>Сервис не доступен, ведутся работы</h1>
</body>
</html>
"""


@app.get("/")
def index():
    # The bare domain always shows this plain status page. Uploaded PDFs are
    # reachable only through their own link, /f/<ID> (that's the link the
    # per-file QR from the "Мои PDF" section encodes).
    return STATUS_PAGE_HTML


@app.get("/verify/<doc_id>")
def verify(doc_id):
    doc = Document.query.get(doc_id.upper())
    return render_template("verify.html", doc=doc, doc_id=doc_id.upper())


# --------------------------------------------------------------------------
# Admin auth
# --------------------------------------------------------------------------

@app.route("/admin/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if secrets.compare_digest(request.form.get("password", ""), ADMIN_PASSWORD):
            session["is_admin"] = True
            return redirect(request.args.get("next") or url_for("admin_home"))
        flash("Неверный пароль")
    return render_template("login.html")


@app.get("/admin/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# --------------------------------------------------------------------------
# Admin: manage documents
# --------------------------------------------------------------------------

@app.route("/admin", methods=["GET", "POST"])
def admin_home():
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))

    if request.method == "POST":
        doc_id = new_id()
        keys = request.form.getlist("field_key")
        vals = request.form.getlist("field_val")
        extra = [{"k": k, "v": v} for k, v in zip(keys, vals) if k.strip() or v.strip()]

        doc = Document(
            id=doc_id,
            title=request.form["title"].strip(),
            holder_name=request.form["holder_name"].strip(),
            issue_date=request.form.get("issue_date") or datetime.utcnow().strftime("%d.%m.%Y"),
            status=request.form.get("status") or "Действителен",
            extra_fields=extra,
        )
        db.session.add(doc)
        db.session.commit()
        return redirect(url_for("admin_doc", doc_id=doc_id))

    docs = Document.query.order_by(Document.created_at.desc()).limit(50).all()
    return render_template("admin.html", docs=docs)


@app.get("/admin/doc/<doc_id>")
def admin_doc(doc_id):
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))
    doc = Document.query.get_or_404(doc_id)
    return render_template(
        "admin_doc.html", doc=doc, verify_url=verify_url_for(doc.id)
    )


@app.get("/admin/doc/<doc_id>/delete")
def admin_delete(doc_id):
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))
    doc = Document.query.get_or_404(doc_id)
    db.session.delete(doc)
    db.session.commit()
    return redirect(url_for("admin_home"))


# --------------------------------------------------------------------------
# Admin: upload your own PDF, get a QR that opens THAT FILE directly
# (simpler than Document above -- no data page, the QR just points at the
# PDF itself, served from your own domain once deployed).
# --------------------------------------------------------------------------

@app.route("/admin/pdfs", methods=["GET", "POST"])
def admin_pdfs():
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))

    if request.method == "POST":
        upload = request.files.get("pdf_file")
        if not upload or upload.filename == "":
            flash("Выберите PDF-файл")
            return redirect(url_for("admin_pdfs"))
        if not upload.filename.lower().endswith(".pdf"):
            flash("Файл должен быть в формате .pdf")
            return redirect(url_for("admin_pdfs"))

        data = upload.read()
        # Basic sanity check: real PDFs start with "%PDF-"
        if not data.startswith(b"%PDF-"):
            flash("Это не похоже на корректный PDF-файл")
            return redirect(url_for("admin_pdfs"))

        pdf_id = new_pdf_id()
        record = PdfFile(id=pdf_id, filename=upload.filename, data=data)
        db.session.add(record)
        db.session.commit()
        return redirect(url_for("admin_pdf_detail", pdf_id=pdf_id))

    pdfs = PdfFile.query.order_by(PdfFile.uploaded_at.desc()).limit(50).all()
    active_id = pdfs[0].id if pdfs else None
    return render_template("admin_pdfs.html", pdfs=pdfs, active_id=active_id, root_url=root_url())


@app.get("/admin/pdfs/<pdf_id>")
def admin_pdf_detail(pdf_id):
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))
    record = PdfFile.query.get_or_404(pdf_id)
    latest = PdfFile.query.order_by(PdfFile.uploaded_at.desc()).first()
    is_active = latest is not None and latest.id == record.id
    return render_template(
        "admin_pdf_detail.html",
        pdf=record,
        file_url=pdf_url_for(record.id),
        root_url=root_url(),
        is_active=is_active,
    )


@app.get("/admin/pdfs/<pdf_id>/delete")
def admin_pdf_delete(pdf_id):
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))
    record = PdfFile.query.get_or_404(pdf_id)
    db.session.delete(record)
    db.session.commit()
    return redirect(url_for("admin_pdfs"))


@app.get("/f/<pdf_id>")
def view_pdf(pdf_id):
    """Public route -- this is what the QR encodes. Serves the PDF inline
    so scanning the QR opens it straight in the phone's browser, the way a
    normal hosted PDF link behaves."""
    record = PdfFile.query.get_or_404(pdf_id)
    return send_file(
        io.BytesIO(record.data),
        mimetype="application/pdf",
        download_name=record.filename,
        as_attachment=False,  # inline, not a forced download
    )


@app.get("/qr/pdf/<pdf_id>.png")
def qr_png_pdf(pdf_id):
    record = PdfFile.query.get_or_404(pdf_id)
    img = make_qr_image(pdf_url_for(record.id))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"{record.id}-qr.png")


@app.get("/qr/site.png")
def qr_png_site():
    """QR that encodes the bare domain (no path, no ID) -- scanning it opens
    whatever PDF is currently the latest upload, served from index()."""
    img = make_qr_image(root_url())
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name="site-qr.png")


# --------------------------------------------------------------------------
# QR image for one document (used both standalone and for PDF stamping)
# --------------------------------------------------------------------------

@app.get("/qr/<doc_id>.png")
def qr_png(doc_id):
    doc = Document.query.get_or_404(doc_id)
    img = make_qr_image(verify_url_for(doc.id))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png", download_name=f"{doc.id}-qr.png")


# --------------------------------------------------------------------------
# Stamp the QR onto an uploaded PDF
# --------------------------------------------------------------------------

@app.route("/admin/doc/<doc_id>/stamp", methods=["POST"])
def stamp_pdf(doc_id):
    if not session.get("is_admin"):
        return redirect(url_for("login", next=request.path))
    doc = Document.query.get_or_404(doc_id)

    upload = request.files.get("pdf_file")
    if not upload or upload.filename == "":
        flash("Выберите PDF-файл")
        return redirect(url_for("admin_doc", doc_id=doc_id))

    page_index = max(int(request.form.get("page", 1)) - 1, 0)
    x = float(request.form.get("x", 40))
    y = float(request.form.get("y", 40))
    size = float(request.form.get("size", 110))

    reader = PdfReader(upload.stream)
    if page_index >= len(reader.pages):
        page_index = len(reader.pages) - 1

    target_page = reader.pages[page_index]
    page_w = float(target_page.mediabox.width)
    page_h = float(target_page.mediabox.height)

    qr_img = make_qr_image(verify_url_for(doc.id))

    overlay_buf = io.BytesIO()
    c = pdfcanvas.Canvas(overlay_buf, pagesize=(page_w, page_h))
    c.drawImage(ImageReader(qr_img), x, y, width=size, height=size, mask="auto")
    c.setFont("Helvetica", 6)
    c.drawString(x, max(y - 9, 2), f"Verify: {verify_url_for(doc.id)}")
    c.save()
    overlay_buf.seek(0)

    overlay_reader = PdfReader(overlay_buf)
    target_page.merge_page(overlay_reader.pages[0])

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    out_buf = io.BytesIO()
    writer.write(out_buf)
    out_buf.seek(0)

    return send_file(
        out_buf,
        mimetype="application/pdf",
        download_name=f"{doc.id}-with-qr.pdf",
        as_attachment=True,
    )


if __name__ == "__main__":
    # host="0.0.0.0" makes the site reachable from other devices on the same
    # Wi-Fi (your phone, to actually scan a QR), not just this computer.
    # Open the site using your PC's LAN IP (see README) rather than
    # "localhost" so the generated QR encodes a URL your phone can reach.
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
