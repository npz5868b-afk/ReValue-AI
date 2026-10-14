"""Immutable, privacy-safe report snapshots and professional report rendering."""
from __future__ import annotations
from contextlib import contextmanager
from datetime import datetime, timezone
from io import BytesIO
import html, json, os, re, secrets, sqlite3, time
from pathlib import Path
from typing import Any, Iterator

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

REPORT_TTL=7*86400
REPORT_DB=Path(os.environ.get("REVALUE_REPORT_DB",str(Path.home()/".revalue-ai"/"report_snapshots.sqlite3")))
MASK_RE=re.compile(r"[•*]{11}\d{4}")
NAVY=colors.HexColor("#071B2B"); INK=colors.HexColor("#102B3A"); CYAN=colors.HexColor("#078C9C")
TEAL=colors.HexColor("#0FBBAD"); PALE=colors.HexColor("#EDF8F7"); LINE=colors.HexColor("#B7DBD8"); MUTED=colors.HexColor("#5C7480")

def check_imei(raw: Any)->dict[str,Any]:
    value=str(raw or "").strip().replace(" ","").replace("-","")
    mask=("•"*11+value[-4:]) if len(value)==15 and value.isascii() and value.isdigit() else None
    if not mask:return {"status":"invalid","format_valid":False,"checksum_valid":False,"masked_imei":None,"blacklist_status":"not_checked","model_status":"not_checked","message":"Enter exactly 15 IMEI digits. This tool does not check stolen or locked status."}
    total=0
    for i,char in enumerate(value):
        digit=int(char)
        if i%2:digit=digit*2-9 if digit>4 else digit*2
        total+=digit
    valid=total%10==0
    return {"status":"checksum_valid" if valid else "invalid_checksum","format_valid":True,"checksum_valid":valid,"masked_imei":mask,"blacklist_status":"not_checked","model_status":"not_checked","message":"IMEI check digit is valid; this does NOT verify ownership, identity, blacklist or lock status." if valid else "IMEI check digit does not match; recheck the number. No blacklist lookup was performed."}

def _clean(value:Any,limit:int=150)->str:
    return "".join(c for c in value[:limit] if c>=" " and c!="\x7f") if isinstance(value,str) else ""
def _display(value:Any)->str:return str(value or "Not available")

class ReportStorageUnavailable(RuntimeError):
    """Storage unavailable; never fall back to ephemeral SQLite in cloud mode."""


def _execute(conn: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
    """Keep SQLite's ? parameters while using Psycopg's %s for PostgreSQL."""
    if not isinstance(conn, sqlite3.Connection):
        query = query.replace("?", "%s")
    return conn.execute(query, params)


@contextmanager
def _db() -> Iterator[Any]:
    """SQLite locally; PostgreSQL only when explicitly configured on Render.

    REVALUE_REPORT_DATABASE_URL is server-side only. No URI or credentials are
    logged, returned through HTTP, or silently replaced with SQLite on failure.
    """
    url = os.environ.get("REVALUE_REPORT_DATABASE_URL", "").strip()
    if url:
        if not url.startswith(("postgresql://", "postgres://")):
            raise ReportStorageUnavailable("report_storage_configuration_invalid")
        try:
            import psycopg
        except ImportError as exc:
            raise ReportStorageUnavailable("report_database_driver_missing") from exc
        try:
            # Use Supabase's IPv4-compatible *session pooler* URI on Render Free.
            # Disabling auto-prepared statements also supports transaction poolers.
            with psycopg.connect(url, connect_timeout=8, sslmode="require", prepare_threshold=None) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS reports (token TEXT PRIMARY KEY, created BIGINT NOT NULL, expires BIGINT NOT NULL, snapshot TEXT NOT NULL)")
                yield conn
        except psycopg.Error as exc:
            raise ReportStorageUnavailable("report_database_unavailable") from exc
        return

    try:
        REPORT_DB.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(REPORT_DB, timeout=8)
        try:
            with conn:
                conn.execute("CREATE TABLE IF NOT EXISTS reports (token TEXT PRIMARY KEY, created INTEGER NOT NULL, expires INTEGER NOT NULL, snapshot TEXT NOT NULL)")
                yield conn
        finally:
            conn.close()
    except (sqlite3.Error, OSError) as exc:
        raise ReportStorageUnavailable("report_database_unavailable") from exc

def _imei(payload:dict[str,Any])->dict[str,str]:
    item=payload.get("imei")
    if not isinstance(item,dict):return {"status":"not_checked"}
    status=_clean(item.get("status"),32)
    if status=="checksum_valid" and MASK_RE.fullmatch(_clean(item.get("masked_imei"),20)):
        return {"status":status,"masked_imei":_clean(item["masked_imei"],20),"blacklist_status":"not_checked","model_status":"not_checked"}
    if status=="skipped":return {"status":"skipped"}
    return {"status":"check_not_completed" if status in {"invalid","invalid_checksum","check_not_completed"} else "not_checked"}

def safe_snapshot(payload:Any)->dict[str,Any]:
    if not isinstance(payload,dict):raise ValueError("invalid_snapshot")
    fields={"report_id":64,"device_name":120,"storage":28,"exterior_grade":12,"market_value":100,"damage":240,"recommendation":160,"evidence":300,"evidence_date":40,"assessment_mode":12,"rationale":600,"methodology":500,"evidence_strength":80,"comparable_count":40,"assessment_generated_at":40}
    snap={key:_clean(payload.get(key),limit) for key,limit in fields.items()}
    if not snap["device_name"] or not snap["market_value"]:raise ValueError("device_name_and_market_value_required")
    snap["imei"]=_imei(payload)
    if payload.get("internal_profile")=="sample_a_minus":snap["internal_profile"]="sample_a_minus"
    snap["disclaimer"]="Indicative assessment. Not a guaranteed merchant offer, verified hardware report, IMEI blacklist certificate, ownership check, or stolen-device clearance."
    return snap

def save_report(payload:Any)->tuple[str,int,dict[str,Any]]:
    snap=safe_snapshot(payload); token=secrets.token_urlsafe(24); now=int(time.time()); expires=now+REPORT_TTL
    snap["share_created_at"]=datetime.fromtimestamp(now,timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    with _db() as conn:
        _execute(conn, "DELETE FROM reports WHERE expires < ?", (now,))
        _execute(conn, "INSERT INTO reports (token,created,expires,snapshot) VALUES (?,?,?,?)",
                 (token, now, expires, json.dumps(snap, ensure_ascii=False)))
    return token,expires,snap

def load_report(token:str)->dict[str,Any]|None:
    if not re.fullmatch(r"[A-Za-z0-9_-]{32}",token):return None
    with _db() as conn:
        row = _execute(conn, "SELECT snapshot,expires FROM reports WHERE token=?", (token,)).fetchone()
    return json.loads(row[0]) if row and row[1]>=int(time.time()) else None

def _imei_text(snap:dict[str,Any])->str:
    item=snap.get("imei") or {}; status=item.get("status")
    if status=="checksum_valid":return f"Checksum valid - {_display(item.get('masked_imei'))}. blacklist not checked; ownership not checked."
    if status=="skipped":return "Skipped by user."
    return "Check not completed." if status=="check_not_completed" else "Not checked."
def _internal(snap:dict[str,Any])->str:
    return "A- sample profile only - Battery 91%, Performance 94%, Connectivity 96%, Risk Low." if snap.get("internal_profile")=="sample_a_minus" else "No independently verified hardware measurements."

def report_html(snap:dict[str,Any])->str:
    esc=lambda val:html.escape(_display(val),quote=True)
    cells=[("Exterior condition",snap.get("exterior_grade")),("Visible condition",snap.get("damage")),("Internal profile",_internal(snap)),("Device identity / IMEI",_imei_text(snap)),("Market evidence",snap.get("evidence")),("Evidence observation",snap.get("evidence_date")),("Comparable count",snap.get("comparable_count")),("Evidence strength",snap.get("evidence_strength"))]
    tiles="".join(f'<article class="tile"><small>{html.escape(label)}</small><strong>{esc(value)}</strong></article>' for label,value in cells)
    return f'''<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow,noarchive"><meta name="referrer" content="no-referrer"><title>ReValue AI assessment report</title><style>
:root{{--navy:#04131f;--panel:#092735;--line:rgba(126,246,232,.25);--cyan:#69f8ec;--mint:#b9ffbd;--ink:#edfffc;--muted:#9bc2ca}}*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 72% -12%,#0c5360 0,transparent 36%),linear-gradient(155deg,#03101b,#061d2b 56%,#031018);color:var(--ink);font-family:Inter,ui-sans-serif,system-ui,sans-serif}}main{{max-width:850px;margin:auto;padding:34px 18px 70px}}.brand{{font-weight:900;letter-spacing:.16em;font-size:11px;color:var(--cyan)}}h1{{font-size:clamp(31px,6vw,48px);line-height:1.04;margin:14px 0 7px}}.sub{{color:var(--muted);margin:0 0 20px;font-size:13px}}.hero,.section,.tile{{border:1px solid var(--line);background:linear-gradient(145deg,rgba(13,58,70,.86),rgba(5,24,40,.94));box-shadow:0 18px 56px rgba(0,0,0,.22)}}.hero{{padding:25px;border-radius:24px}}.kicker,h2{{color:var(--cyan);font-size:11px;font-weight:900;letter-spacing:.12em}}.value{{font-size:clamp(31px,8vw,58px);letter-spacing:-.05em;font-weight:900;color:#d7fff8;margin:10px 0}}.qualifier{{color:#b0d2d6;font-size:13px;line-height:1.55;margin:0}}.section{{border-radius:20px;padding:19px;margin-top:13px}}h2{{margin:0 0 13px}}.recommendation{{font-size:clamp(21px,5vw,31px);font-weight:900;color:var(--mint);margin:0 0 7px}}p{{color:#c0d8dc;line-height:1.58;margin:0}}.grid{{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:13px}}.tile{{border-radius:15px;padding:13px;min-width:0}}small{{display:block;color:var(--muted);font-size:10px;letter-spacing:.07em;margin-bottom:7px}}strong{{display:block;overflow-wrap:anywhere;font-size:13px;line-height:1.4}}a{{display:inline-block;margin-top:16px;background:linear-gradient(105deg,#0ed9c7,#b8ffad,#0bc1bd);color:#03262b;border-radius:13px;padding:13px 16px;font-weight:900;text-decoration:none}}footer{{border-top:1px solid var(--line);padding-top:17px;margin-top:24px;color:var(--muted);font-size:11px;line-height:1.55}}@media(max-width:560px){{main{{padding:26px 14px 48px}}.grid{{grid-template-columns:1fr}}.hero,.section{{padding:17px}}}}</style></head><body><main><div class="brand">REVALUE AI / ASSESSMENT SNAPSHOT</div><h1>{esc(snap.get("device_name"))}</h1><p class="sub">{esc(snap.get("storage"))} - Report {esc(snap.get("report_id"))} - Indicative Assessment</p><section class="hero"><div class="kicker">ESTIMATED MARKET VALUE</div><div class="value">{esc(snap.get("market_value"))}</div><p class="qualifier">Indicative market range - not a guaranteed merchant offer.</p></section><section class="section"><h2>REVALUE AI RECOMMENDS</h2><div class="recommendation">{esc(snap.get("recommendation"))}</div><p>{esc(snap.get("rationale"))}</p></section><section class="grid">{tiles}</section><section class="section"><h2>METHODOLOGY &amp; LIMITATIONS</h2><p>{esc(snap.get("methodology"))}</p><p style="margin-top:10px">{esc(snap.get("disclaimer"))}</p></section><a href="?download=pdf">Download PDF</a><footer>Assessment reference {esc(snap.get("report_id"))}. Snapshot created {esc(snap.get("share_created_at"))}. Access expires after 7 days.</footer></main></body></html>'''

# ---------------------------------------------------------------------------
# Premium A4 PDF renderer. Share storage, sanitisation and endpoints unchanged.
# Fonts are loaded from the host at runtime; no font assets are distributed.
# ---------------------------------------------------------------------------

def _pdf_fonts() -> tuple[str, str]:
    """Choose installed TrueType faces with Unicode punctuation support."""
    candidates = []
    override = os.environ.get("REVALUE_PDF_FONT", "")
    if override:
        candidates.append((Path(override), Path(override)))
    win_fonts = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"
    candidates += [
        (win_fonts / "segoeui.ttf", win_fonts / "segoeuib.ttf"),
        (Path("/usr/share/fonts/truetype/lato/Lato-Regular.ttf"), Path("/usr/share/fonts/truetype/lato/Lato-Bold.ttf")),
        (Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")),
    ]
    for regular_path, bold_path in candidates:
        if regular_path.is_file():
            try:
                if "ReValuePdfRegular" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("ReValuePdfRegular", str(regular_path)))
                if "ReValuePdfBold" not in pdfmetrics.getRegisteredFontNames():
                    pdfmetrics.registerFont(TTFont("ReValuePdfBold", str(bold_path if bold_path.is_file() else regular_path)))
                return "ReValuePdfRegular", "ReValuePdfBold"
            except Exception:
                continue
    # Helvetica is always present in PDF viewers. Safe ASCII punctuation fallback.
    return "Helvetica", "Helvetica-Bold"


def _pdf_string(value: Any, font: str) -> str:
    text = _display(value).replace("\r", "")
    if font.startswith("Helvetica"):
        return text.replace("\u2013", "-").replace("\u2014", "-").replace("\u2022", "*").replace("\u2212", "-")
    return text


def _pdf_paragraph(text: Any, width: float, font: str, size: float = 9.1,
                   leading: float | None = None, color: Any = None,
                   bold: bool = False, max_lines: int | None = None):
    """Measure wrapped, escaped text; optionally produce a short hero excerpt."""
    from reportlab.platypus import Paragraph
    from reportlab.lib.styles import ParagraphStyle
    text = _pdf_string(text, font)
    leading = leading or size * 1.46
    style = ParagraphStyle("rv_pdf", fontName=font, fontSize=size, leading=leading,
                           textColor=color or colors.HexColor("#577181"),
                           spaceBefore=0, spaceAfter=0,
                           allowWidows=0, allowOrphans=0)

    def make(source: str):
        p = Paragraph(html.escape(source).replace("\n", "<br/>"), style)
        _, height = p.wrap(width, 2000)
        return p, height

    para, height = make(text)
    if max_lines is not None and height > (max_lines * leading + .15):
        # Display excerpt only in hero; full rationale remains on page two.
        lo, hi, best = 0, len(text), ""
        while lo <= hi:
            mid = (lo + hi) // 2
            attempt = text[:mid].rstrip(" .,;:-") + "..."
            candidate, measured = make(attempt)
            if measured <= max_lines * leading + .15:
                best, para, height = attempt, candidate, measured
                lo = mid + 1
            else:
                hi = mid - 1
        if not best:
            para, height = make("...")
    return para, height


def _pdf_draw_para(c: Any, text: Any, x: float, top: float, width: float, font: str,
                   size: float = 9.1, leading: float | None = None,
                   color: Any = None, max_lines: int | None = None) -> float:
    para, height = _pdf_paragraph(text, width, font, size, leading, color, max_lines=max_lines)
    para.drawOn(c, x, top - height)
    return top - height


def _pdf_label(c: Any, text: str, x: float, baseline: float, face: str,
               size: float = 8.0, tint: Any = None) -> None:
    c.setFont(face, size)
    c.setFillColor(tint or colors.HexColor("#617e8e"))
    c.drawString(x, baseline, _pdf_string(text.upper(), face))


def _pdf_round(c: Any, x: float, y: float, w: float, h: float, radius: float = 9,
               fill: Any = None, stroke: Any = None, line_width: float = .65) -> None:
    c.setLineWidth(line_width)
    if fill:
        c.setFillColor(fill)
    if stroke:
        c.setStrokeColor(stroke)
    c.roundRect(x, y, w, h, radius, stroke=int(bool(stroke)), fill=int(bool(fill)))


def _pdf_phone(c: Any, x: float, y: float, w: float, h: float, stroke: Any) -> None:
    c.setLineWidth(1.55)
    c.setStrokeColor(stroke)
    c.roundRect(x, y, w, h, 4, stroke=1, fill=0)
    c.line(x + w*.30, y+h-5, x+w*.70, y+h-5)


def _pdf_brand(c: Any, face: str, bold: str, x: float, y: float, tag: str) -> None:
    mint=colors.HexColor("#55E5DD")
    _pdf_round(c,x,y-17,28,28,7,fill=mint)
    _pdf_phone(c,x+8,y-12,12,19,colors.HexColor("#042431"))
    c.setFillColor(colors.HexColor("#F4FFFE"))
    c.setFont(bold,12)
    c.drawString(x+42,y-5,"R E V A L U E   A I")
    c.setFont(bold,8)
    c.setFillColor(mint)
    c.drawRightString(A4[0]-42,y-4,tag)


def _pdf_orb(c: Any, cx: float, cy: float) -> None:
    import math
    cyan=colors.HexColor("#4BE5DC")
    dim=colors.HexColor("#144D5A")
    for r in (38,60,78):
        c.setLineWidth(.8)
        c.setStrokeColor(dim)
        c.circle(cx,cy,r,stroke=1,fill=0)
    for i in range(44):
        angle=2*math.pi*i/44
        r0=72 if i%4 else 69
        r1=77
        c.setStrokeColor(colors.HexColor("#1B6570"))
        c.setLineWidth(.55)
        c.line(cx+r0*math.sin(angle),cy+r0*math.cos(angle),cx+r1*math.sin(angle),cy+r1*math.cos(angle))
    c.setLineWidth(1.8)
    c.setStrokeColor(colors.HexColor("#309BA2"))
    c.arc(cx-60,cy-60,cx+60,cy+60,10,75)
    c.arc(cx-60,cy-60,cx+60,cy+60,175,56)
    _pdf_phone(c,cx-13,cy-24,26,49,cyan)


def _pdf_footer(c: Any, face: str, bold: str, report_id: str, page: int, total: int) -> None:
    left=42
    ink=colors.HexColor("#163643")
    subtle=colors.HexColor("#708995")
    c.setStrokeColor(colors.HexColor("#C5DFDF"))
    c.setLineWidth(.6)
    c.line(left,41,A4[0]-left,41)
    _pdf_label(c,"REVALUE AI",left,25,bold,7.4,ink)
    c.setFillColor(subtle)
    c.setFont(face,7.0)
    c.drawString(131,25,"INDICATIVE DEVICE ASSESSMENT")
    identifier=_pdf_string(report_id or "Not available",face)
    while pdfmetrics.stringWidth(identifier,face,7)>122 and len(identifier)>5:
        identifier=identifier[:-5]+"..."
    c.drawString(306,25,identifier)
    c.setFillColor(ink)
    c.setFont(bold,8)
    c.drawRightString(A4[0]-left,25,f"{page} / {total:02d}")


def _pdf_cover_header(c: Any, snap: dict[str,Any], face: str, bold: str) -> None:
    dark=colors.HexColor("#071E2C")
    mint=colors.HexColor("#ACF8AA")
    cyan=colors.HexColor("#52E1D8")
    c.setFillColor(dark)
    c.rect(0,566,A4[0],A4[1]-566,stroke=0,fill=1)
    c.setStrokeColor(colors.HexColor("#30606B"))
    c.setLineWidth(.55)
    c.line(42,778,A4[0]-42,778)
    _pdf_brand(c,face,bold,42,807,"ASSESSMENT / 01")
    _pdf_label(c,"DEVICE ASSESSMENT REPORT",42,750,bold,8.9,cyan)
    c.setFillColor(colors.white)
    c.setFont(bold,24)
    c.drawString(42,712,"Your phone. Decoded.")
    device=" ".join(v for v in (_display(snap.get("device_name")) if snap.get("device_name") else "", snap.get("storage") or "") if v)
    _pdf_draw_para(c,device,42,696,355,face,size=10.2,leading=13,color=colors.HexColor("#D8EEF0"),max_lines=2)
    _pdf_label(c,"ESTIMATED MARKET VALUE",42,651,bold,8.6,mint)
    price=_pdf_string(snap.get("market_value"),bold)
    # Preserve correct punctuation while fitting the value beside the orbital motif.
    font_size=29.5
    while pdfmetrics.stringWidth(price,bold,font_size)>339 and font_size>15:
        font_size-=.5
    c.setFont(bold,font_size)
    c.setFillColor(colors.white)
    if pdfmetrics.stringWidth(price,bold,font_size)<=343:
        c.drawString(42,613,price)
    else:
        _pdf_draw_para(c,price,42,643,338,bold,size=15,leading=18,color=colors.white,max_lines=2)
    c.setFillColor(colors.HexColor("#B5D2D8"))
    c.setFont(face,8.5)
    c.drawString(42,592,"Indicative market range  -  Not a guaranteed merchant offer")
    _pdf_orb(c,500,664)
    c.setFillColor(cyan)
    c.rect(0,563,A4[0],3,stroke=0,fill=1)
    c.setFillColor(colors.HexColor("#07BDAE"))
    c.rect(0,559,A4[0],3,stroke=0,fill=1)


def _pdf_section(c: Any, idx: str, title: str, top: float, bold: str) -> float:
    _pdf_label(c,idx,42,top-11,bold,8.6,colors.HexColor("#00BDB3"))
    c.setFillColor(colors.HexColor("#102D3B"))
    c.setFont(bold,13.3)
    c.drawString(73,top-12,title)
    c.setStrokeColor(colors.HexColor("#C6DFE0"))
    c.setLineWidth(.52)
    c.line(42,top-24,A4[0]-42,top-24)
    return top-34


def _pdf_one(c: Any, snap: dict[str,Any], face: str, bold: str) -> None:
    ink=colors.HexColor("#102E3D")
    muted=colors.HexColor("#5E7A87")
    mint=colors.HexColor("#04C5B9")
    dark=colors.HexColor("#071E2C")
    pale=colors.HexColor("#F0F8F7")
    _pdf_cover_header(c,snap,face,bold)
    _pdf_round(c,42,524,511,30,7,fill=pale)
    for x in (223,394):
        c.setStrokeColor(colors.HexColor("#C4DDDD"))
        c.line(x,529,x,548)
    vals=[("REPORT ID",snap.get("report_id")),("ASSESSMENT DATE",snap.get("assessment_generated_at")),("DOCUMENT TYPE","INDICATIVE")]
    for x,(label,val) in zip((55,238,409),vals):
        _pdf_label(c,label,x,541,bold,7.1,muted)
        _pdf_draw_para(c,val,x,539,138,bold,size=9.0,leading=10.4,color=ink,max_lines=1)

    y=_pdf_section(c,"01","Condition intelligence",508,bold)
    box_y=296;box_h=176
    _pdf_round(c,42,box_y,511,box_h,12,fill=colors.white,stroke=colors.HexColor("#B6DBD9"))
    c.setFillColor(colors.HexColor("#4FDFD7"))
    c.roundRect(42,box_y+box_h-2.5,152,2.5,1,stroke=0,fill=1)
    c.setStrokeColor(colors.HexColor("#CCE3E2"))
    c.line(290,box_y+17,290,box_y+box_h-16)
    _pdf_label(c,"EXTERIOR ASSESSMENT",59,box_y+149,bold,7.4,muted)
    grade=_pdf_string(snap.get("exterior_grade"),bold)
    c.setFillColor(ink)
    c.setFont(bold,32 if len(grade)<4 else 22)
    c.drawString(59,box_y+100,grade)
    if len(grade)<6:
        _pdf_round(c,118,box_y+105,60,18,9,fill=colors.HexColor("#E8F7F2"))
        _pdf_label(c,"GRADE "+grade,128,box_y+111,bold,7.2,colors.HexColor("#008F8B"))
    _pdf_draw_para(c,snap.get("damage") or "Visible damage not confirmed.",59,box_y+83,209,face,size=9.2,leading=12.3,color=muted,max_lines=3)
    c.setStrokeColor(colors.HexColor("#04C6B8"))
    c.setLineWidth(3.6)
    c.line(61,box_y+27,271,box_y+27)
    _pdf_label(c,"INTERNAL HEALTH PROFILE",306,box_y+149,bold,7.4,muted)
    if snap.get("internal_profile")=="sample_a_minus":
        c.setFillColor(ink)
        c.setFont(bold,29)
        c.drawString(306,box_y+98,"A-")
        _pdf_round(c,358,box_y+105,62,18,9,fill=colors.HexColor("#E8F7F2"))
        _pdf_label(c,"SAMPLE",373,box_y+111,bold,7.0,colors.HexColor("#008F8B"))
        for x,label,val in ((306,"BATTERY","91%"),(383,"PERFORMANCE","94%"),(472,"CONNECTIVITY","96%")):
            _pdf_label(c,label,x,box_y+76,bold,6.3,muted)
            c.setFillColor(ink)
            c.setFont(bold,13.5)
            c.drawString(x,box_y+52,val)
        c.setFillColor(ink);c.setFont(bold,8.0);c.drawString(306,box_y+29,"Risk profile: Low")
        c.setFillColor(muted);c.setFont(face,7.1);c.drawString(306,box_y+15,"Illustrative; not measured hardware.")
    else:
        _pdf_draw_para(c,"Not independently verified",306,box_y+124,224,bold,size=11.0,leading=15,color=ink)
        _pdf_draw_para(c,"No measured internal hardware results were supplied.",306,box_y+77,225,face,size=9,leading=12,color=muted)

    _pdf_section(c,"02","AI recommendation",282,bold)
    _pdf_round(c,42,155,511,93,11,fill=dark)
    c.setFillColor(mint);c.roundRect(42,155,3.5,93,1.5,stroke=0,fill=1)
    _pdf_round(c,57,192,35,37,8,fill=colors.HexColor("#50E1D6"))
    c.setStrokeColor(colors.HexColor("#08333C"));c.setLineWidth(2)
    c.line(66,210,83,210);c.line(78,215,83,210);c.line(78,205,83,210)
    _pdf_label(c,"REVALUE AI RECOMMENDS",106,224,bold,7.9,colors.HexColor("#56E3DA"))
    top=_pdf_draw_para(c,snap.get("recommendation"),106,215,427,bold,size=17.0,leading=19.2,color=colors.white,max_lines=2)
    rationale_top=min(top-4,191)
    rationale_lines=2 if rationale_top-22>=160 else (1 if rationale_top-11>=160 else 0)
    if rationale_lines:
        _pdf_draw_para(c,snap.get("rationale") or "Recommendation based on the available assessment evidence.",106,rationale_top,420,face,size=8.3,leading=11.0,color=colors.HexColor("#C0DDE1"),max_lines=rationale_lines)

    _pdf_section(c,"03","Device identity & IMEI",147,bold)
    _pdf_round(c,42,60,511,53,8,fill=pale,stroke=colors.HexColor("#C1DFDC"))
    imei=snap.get("imei") or {}; status=imei.get("status")
    state={"checksum_valid":"CHECKSUM VALID","skipped":"SKIPPED","check_not_completed":"INCOMPLETE"}.get(status,"NOT CHECKED")
    _pdf_round(c,53,84,126,20,10,fill=colors.HexColor("#D7F8EC"))
    _pdf_label(c,state,68,91,bold,7.5,colors.HexColor("#087A78"))
    if status=="checksum_valid":
        _pdf_draw_para(c,imei.get("masked_imei"),190,101,350,bold,size=12,leading=14,color=ink,max_lines=1)
    _pdf_draw_para(c,"Checksum validation does not verify blacklist, ownership, device authenticity or activation lock.",54,81,484,face,size=7.55,leading=10,color=muted,max_lines=2)


def _pdf_evidence_header(c: Any, snap: dict[str,Any], face: str, bold: str, page_number: int) -> None:
    dark=colors.HexColor("#071E2C")
    cyan=colors.HexColor("#54E3DC")
    c.setFillColor(dark);c.rect(0,708,A4[0],A4[1]-708,stroke=0,fill=1)
    _pdf_brand(c,face,bold,42,806,"EVIDENCE / %02d"%page_number)
    c.setFont(bold,23 if page_number==2 else 22)
    c.setFillColor(colors.white)
    c.drawString(42,743,"Evidence & assessment details" if page_number==2 else "Assessment details / continued")
    sub=f"{_display(snap.get('report_id'))}   |   {_display(snap.get('assessment_generated_at'))}"
    _pdf_draw_para(c,sub,42,731,492,face,size=8.7,leading=11,color=colors.HexColor("#C4E0E1"),max_lines=1)
    c.setFillColor(cyan);c.rect(0,704,A4[0],4,fill=1,stroke=0)


def _pdf_evidence_page(c: Any, snap: dict[str,Any], face: str, bold: str, total: int) -> int:
    dark=colors.HexColor("#071E2C")
    pale=colors.HexColor("#EDF8F6")
    ink=colors.HexColor("#153340")
    muted=colors.HexColor("#5C7785")
    teal=colors.HexColor("#05BDB5")
    page=2
    _pdf_evidence_header(c,snap,face,bold,page)
    y=686

    def next_page(needed: float):
        nonlocal y,page
        if y-needed>=66:return
        _pdf_footer(c,face,bold,_clean(snap.get("report_id"),64),page,total)
        c.showPage();page+=1;_pdf_evidence_header(c,snap,face,bold,page);y=682

    # Market statistics with a purpose-built three-column editorial layout.
    next_page(190)
    y=_pdf_section(c,"04","Market evidence",y,bold)+4
    cards=[
        ("COMPARABLES",snap.get("comparable_count"),"As provided by this assessment"),
        ("MARKET EVIDENCE",snap.get("evidence"),"Reported evidence source"),
        ("OBSERVED",snap.get("evidence_date"),"Evidence observation date"),
    ]
    gap=9;w=(511-2*gap)/3;h=76;top=y
    for i,(label,val,sub) in enumerate(cards):
        x=42+i*(w+gap)
        _pdf_round(c,x,top-h,w,h,8,fill=pale)
        _pdf_label(c,label,x+11,top-16,bold,7.1,muted)
        _pdf_draw_para(c,val,x+11,top-25,w-22,bold,size=12.4,leading=14.5,color=ink,max_lines=2)
        _pdf_draw_para(c,sub,x+11,top-54,w-22,face,size=7.15,leading=9,color=muted,max_lines=2)
    y=top-h-12
    strength=snap.get("evidence_strength") or "Not available"
    p,hp=_pdf_paragraph(strength,483,bold,9.6,12.5,ink)
    band_h=max(37,hp+23)
    _pdf_round(c,42,y-band_h,511,band_h,7,fill=colors.white,stroke=colors.HexColor("#C9E2E0"))
    c.setFillColor(teal);c.roundRect(42,y-band_h,4,band_h,1,fill=1,stroke=0)
    _pdf_label(c,"EVIDENCE STRENGTH",54,y-14,bold,7.3,colors.HexColor("#078C8A"))
    _pdf_draw_para(c,strength,54,y-21,477,bold,size=9.0,leading=11.5,color=ink)
    y=y-band_h-11
    # Long evidence strings are preserved in detail rather than silently clipped by statistic cards.
    evidence_text=_display(snap.get("evidence"))
    # If the compact market card would elide any evidence text, provide the
    # complete source field below it, without manufacturing extra facts.
    _, evidence_card_height = _pdf_paragraph(evidence_text, w-22, bold, 12.4, 14.5, ink)
    if evidence_card_height > 29.1:
        p,ph=_pdf_paragraph(evidence_text,484,face,8.4,11.8,ink)
        next_page(ph+40)
        _pdf_label(c,"EVIDENCE DETAIL",43,y-5,bold,7.5,muted)
        y=_pdf_draw_para(c,evidence_text,43,y-15,483,face,size=8.4,leading=11.8,color=ink)-17

    rationale=_display(snap.get("rationale"))
    _,rh=_pdf_paragraph(rationale,475,face,9.35,13.4,ink)
    panel_h=max(57,rh+25)
    next_page(panel_h+56)
    y=_pdf_section(c,"05","Decision rationale",y,bold)+4
    _pdf_round(c,42,y-panel_h,511,panel_h,9,fill=colors.HexColor("#F2F9F7"))
    c.setFillColor(colors.HexColor("#A8F7A5"));c.roundRect(42,y-panel_h,4,panel_h,2,fill=1,stroke=0)
    _pdf_draw_para(c,rationale,56,y-12,479,face,size=9.35,leading=13.4,color=ink)
    y=y-panel_h-12

    verification=[
        ("Exterior assessment","Assessed - Grade "+_display(snap.get("exterior_grade"))),
        ("Internal hardware","Sample profile - not independently measured" if snap.get("internal_profile")=="sample_a_minus" else "Not independently verified"),
        ("IMEI checksum",_imei_text(snap)),
        ("Blacklist and ownership","Not independently verified"),
        ("Merchant offer","Not independently verified"),
    ]
    widths=(211,276)
    sizes=[]
    for a,b in verification:
        _,h1=_pdf_paragraph(a,widths[0]-16,face,8.9,12,ink)
        _,h2=_pdf_paragraph(b,widths[1]-17,face,8.9,12,ink)
        sizes.append(max(25,max(h1,h2)+11))
    table_height=31+sum(sizes)
    next_page(table_height+61)
    y=_pdf_section(c,"06","Verification status",y,bold)+4
    c.setFillColor(dark);c.rect(42,y-29,511,29,fill=1,stroke=0)
    _pdf_label(c,"ASSESSMENT CHECK",54,y-19,bold,7.5,colors.HexColor("#5CE8E0"))
    _pdf_label(c,"CURRENT STATUS",274,y-19,bold,7.5,colors.HexColor("#5CE8E0"))
    y-=29
    for row_index,((name,status),rh) in enumerate(zip(verification,sizes)):
        c.setFillColor(pale if row_index%2==0 else colors.white)
        c.rect(42,y-rh,511,rh,fill=1,stroke=0)
        c.setStrokeColor(colors.HexColor("#D2E4E4"));c.setLineWidth(.45)
        c.rect(42,y-rh,511,rh,fill=0,stroke=1)
        c.line(264,y-rh,264,y)
        _pdf_draw_para(c,name,54,y-9,193,face,size=8.9,leading=12,color=ink)
        c.setFillColor(teal if name=="Exterior assessment" or (name=="IMEI checksum" and (snap.get("imei") or {}).get("status")=="checksum_valid") else colors.HexColor("#A7C3C4"))
        c.circle(279,y-16,2.4,fill=1,stroke=0)
        _pdf_draw_para(c,status,291,y-8,245,face,size=8.9,leading=12,color=ink)
        y-=rh
    y-=9

    methodology=_display(snap.get("methodology"))
    disclaimer=_display(snap.get("disclaimer"))
    _,h1=_pdf_paragraph(methodology,510,face,8.9,12.9,ink)
    _,h2=_pdf_paragraph(disclaimer,510,face,7.8,11.5,muted)
    needed=h1+h2+50
    next_page(needed)
    y=_pdf_section(c,"07","Methodology & limitations",y,bold)+4
    y=_pdf_draw_para(c,methodology,42,y-2,510,face,size=8.9,leading=12.9,color=ink)-18
    y=_pdf_draw_para(c,disclaimer,42,y,510,face,size=7.8,leading=11.5,color=muted)
    return page


def report_pdf(snap: dict[str, Any]) -> bytes:
    """Generate a polished, printable assessment from a sanitized snapshot.

    The report does not create a public share token or change database state.
    Standard ReportLab layout is retained, so API call sites remain compatible.
    """
    from reportlab.pdfgen import canvas as _canvas
    regular,bold=_pdf_fonts()
    report_id=_clean(snap.get("report_id"),64)

    def compose(total: int) -> tuple[bytes,int]:
        out=BytesIO()
        c=_canvas.Canvas(out,pagesize=A4,pageCompression=1,
                         invariant=1,
                         initialFontName=regular)
        c.setTitle("ReValue AI | Device Assessment Report")
        c.setAuthor("ReValue AI")
        c.setSubject("Indicative device assessment")
        _pdf_one(c,snap,regular,bold)
        # Capture page-one footer after total page count is known (second pass).
        _pdf_footer(c,regular,bold,report_id,1,total)
        c.showPage()
        pages=_pdf_evidence_page(c,snap,regular,bold,total)
        _pdf_footer(c,regular,bold,report_id,pages,total)
        c.save()
        return out.getvalue(),pages

    # Two-pass generation allows correct page totals when long evidence creates
    # a continuation page; typical assessments use the intended two-page layout.
    _,total=compose(2)
    output,_=compose(total)
    return output
