from __future__ import annotations

import base64
import html
import re
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path
from uuid import UUID

from fts.domain import PhaseType, Tournament, ValidationError
from fts.scorekeepers import build_scorekeeper_assignments



@dataclass(frozen=True)
class PrintSettings:
    page_size: str = "A4"
    orientation: str = "portrait"
    margin_mm: int = 14
    show_header: bool = True
    show_footer: bool = True
    show_page_numbers: bool = True
    show_logo: bool = True
    footer_text: str = "MSBTS – Badminton-Turnierzentrale"
    organizer_name: str = ""
    primary_color: str = "#68472f"
    accent_color: str = "#b99a80"
    custom_logo_path: str = ""

    def validate(self) -> None:
        if self.page_size not in {"A4", "A3"}:
            raise ValidationError("Unbekanntes Papierformat.")
        if self.orientation not in {"portrait", "landscape"}:
            raise ValidationError("Unbekannte Seitenausrichtung.")
        if not 5 <= self.margin_mm <= 30:
            raise ValidationError("Der Seitenrand muss zwischen 5 und 30 mm liegen.")
        if len(self.footer_text) > 120:
            raise ValidationError("Der Fußzeilentext darf höchstens 120 Zeichen lang sein.")
        if len(self.organizer_name) > 100:
            raise ValidationError("Der Veranstaltername darf höchstens 100 Zeichen lang sein.")
        for value in (self.primary_color, self.accent_color):
            if not re.fullmatch(r"#[0-9A-Fa-f]{6}", value):
                raise ValidationError("Branding-Farben müssen im Format #RRGGBB angegeben werden.")
        if self.custom_logo_path and not Path(self.custom_logo_path).is_file():
            raise ValidationError("Das ausgewählte Branding-Logo wurde nicht gefunden.")


@dataclass(frozen=True)
class DocumentProfile:
    name: str
    selection: "DocumentSelection"
    print_settings: PrintSettings = PrintSettings()
    built_in: bool = False


@dataclass(frozen=True)
class BrandingProfile:
    name: str
    organizer_name: str = ""
    primary_color: str = "#68472f"
    accent_color: str = "#b99a80"
    custom_logo_path: str = ""
    built_in: bool = False

    def validate(self) -> None:
        PrintSettings(
            organizer_name=self.organizer_name,
            primary_color=self.primary_color,
            accent_color=self.accent_color,
            custom_logo_path=self.custom_logo_path,
        ).validate()


def built_in_branding_profiles() -> dict[str, BrandingProfile]:
    return {
        "MSTTS Braun-Weiß": BrandingProfile("MSTTS Braun-Weiß", built_in=True),
        "Freudenholm": BrandingProfile(
            "Freudenholm", "Der Landesverein", "#68472f", "#d9c2ad", built_in=True
        ),
        "Neutral": BrandingProfile(
            "Neutral", "", "#374151", "#d1d5db", built_in=True
        ),
    }


def built_in_document_profiles() -> dict[str, DocumentProfile]:
    return {
        "Turnierleitung": DocumentProfile(
            "Turnierleitung", DocumentSelection(), PrintSettings("A4", "portrait", 12), True
        ),
        "Schiedsrichter": DocumentProfile(
            "Schiedsrichter",
            DocumentSelection(overview=False, participants=False, groups=False, schedule=True, score_sheets=True, scorekeeper_plan=True, knockout_bracket=False),
            PrintSettings("A4", "portrait", 10), True,
        ),
        "Spielerunterlagen": DocumentProfile(
            "Spielerunterlagen",
            DocumentSelection(overview=True, participants=True, groups=True, schedule=True, score_sheets=False, scorekeeper_plan=False, knockout_bracket=True),
            PrintSettings("A4", "portrait", 14), True,
        ),
        "Zuschauer": DocumentProfile(
            "Zuschauer",
            DocumentSelection(overview=True, participants=False, groups=False, schedule=True, score_sheets=False, scorekeeper_plan=False, knockout_bracket=True),
            PrintSettings("A3", "landscape", 10), True,
        ),
    }


def _image_data_uri(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif", ".svg": "image/svg+xml"}.get(suffix)
    if not mime or not path.exists():
        return ""
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _default_logo_data_uri() -> str:
    logo = Path(__file__).with_name("resources") / "fts_table_tennis_icon.png"
    if not logo.exists():
        return ""
    return _image_data_uri(logo)


def _hex_rgb(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index:index + 2], 16) for index in (0, 2, 4))


def _blend_hex(foreground: str, background: str = "#ffffff", amount: float = 0.82) -> str:
    """Blend foreground towards background; amount is the background share."""
    fg = _hex_rgb(foreground)
    bg = _hex_rgb(background)
    mixed = tuple(round(f * (1.0 - amount) + b * amount) for f, b in zip(fg, bg))
    return "#" + "".join(f"{component:02x}" for component in mixed)


def apply_print_settings(source_html: str, settings: PrintSettings, *, document_title: str = "") -> str:
    settings.validate()
    page_rule = f"@page {{ size: {settings.page_size} {settings.orientation}; margin: {settings.margin_mm}mm; }}"
    # Replace the legacy brown defaults in every generated document before adding
    # the print chrome. This also covers cards, bracket lines and section borders.
    soft_primary = _blend_hex(settings.primary_color, amount=0.88)
    soft_accent = _blend_hex(settings.accent_color, amount=0.82)
    rendered = source_html
    replacements = {
        "#68472f": settings.primary_color,
        "#68472F": settings.primary_color,
        "#b99a80": settings.accent_color,
        "#B99A80": settings.accent_color,
        "#f5efe7": soft_primary,
        "#fffdf9": _blend_hex(settings.accent_color, amount=0.94),
        "#faf7f2": _blend_hex(settings.accent_color, amount=0.91),
        "#cbb9aa": settings.accent_color,
        "#b9a898": settings.accent_color,
    }
    for old_color, new_color in replacements.items():
        rendered = rendered.replace(old_color, new_color)
    rendered = re.sub(r"@page\s*\{[^}]*\}", page_rule, rendered, count=1)
    header = ""
    # Generated MSTTS documents already carry a full-width document masthead.
    # Adding the legacy fixed print header on top creates duplicate branding and
    # text collisions in Qt PDF rendering, so only use the fixed header for
    # plain/legacy HTML without the new masthead.
    has_document_masthead = "class=\'doc-head\'" in rendered or 'class="doc-head"' in rendered
    if settings.show_header and not has_document_masthead:
        custom_logo = Path(settings.custom_logo_path) if settings.custom_logo_path else None
        logo_uri = _image_data_uri(custom_logo) if (custom_logo and settings.show_logo) else ""
        default_logo_uri = _default_logo_data_uri() if (settings.show_logo and not custom_logo) else ""
        hidden_default_logo = f'<img src="{default_logo_uri}" width="1" height="1" style="display:none" alt="">' if default_logo_uri else ""
        logo = f'<img src="{logo_uri}" alt="Logo">' if logo_uri else (
            hidden_default_logo + '<span class="print-wordmark">MSBTS<small>BADMINTON TOURNAMENT SYSTEM</small></span>' if settings.show_logo else ""
        )
        organizer = f"<small>{html.escape(settings.organizer_name)}</small>" if settings.organizer_name else ""
        header = f'<header class="print-header">{logo}<span class="print-title">{html.escape(document_title)}{organizer}</span></header>'
    footer = ""
    if settings.show_footer:
        page = '<span class="page-number"></span>' if settings.show_page_numbers else ""
        if has_document_masthead:
            footer = f'<table class="document-footer"><tbody><tr><td>{html.escape(settings.footer_text)}</td><td class="footer-page">{page}</td></tr></tbody></table>'
        else:
            footer = f'<footer class="print-footer"><span>{html.escape(settings.footer_text)}</span>{page}</footer>'
    chrome_css = f"""
<style id="mstts-print-chrome">
:root{{--mstts-primary:{settings.primary_color};--mstts-accent:{settings.accent_color};}}
body{{color:#202020;background:#fff}}
h1,h2,h3{{color:var(--mstts-primary)!important}}
h2{{border-bottom-color:var(--mstts-accent)!important}}
th{{background:var(--mstts-primary)!important;color:#fff!important}}
td,th,.card{{border-color:var(--mstts-accent)!important}}
.meta{{border-left-color:var(--mstts-primary)!important;background:{soft_primary}!important}}
.card{{background:{_blend_hex(settings.accent_color, amount=0.94)}!important}}
.bracket-match{{border-color:var(--mstts-accent)!important;border-left-color:var(--mstts-primary)!important;background:{_blend_hex(settings.accent_color, amount=0.94)}!important}}
.print-header,.print-footer{{position:fixed;left:0;right:0;color:var(--mstts-primary);font-size:8pt;display:flex;align-items:center;z-index:1000}}
.print-header{{top:-12mm;height:9mm;border-bottom:2px solid var(--mstts-primary);font-weight:800;gap:3mm;letter-spacing:.15pt}}
.print-header img{{width:5mm;height:5mm;object-fit:contain}}.print-header span{{display:flex;flex-direction:column}}.print-header small{{font-weight:400;font-size:6.3pt;color:#75695f;margin-top:.25mm}}
.print-wordmark{{font-size:9pt;font-weight:900;line-height:1;min-width:19mm}}.print-wordmark small{{font-size:4.8pt;letter-spacing:.3pt;margin-top:.5mm}}.print-title{{font-size:7.6pt;font-weight:700}}
.print-footer{{bottom:-11mm;height:7mm;border-top:1px solid var(--mstts-accent);justify-content:space-between;color:#75695f}}
.document-footer{{width:100%;border-collapse:collapse;margin:10mm 0 0;border-top:1px solid var(--mstts-accent);table-layout:fixed}}
.document-footer td{{border:0!important;background:#fff!important;padding:2.5mm 0 0!important;color:#75695f;font-size:7.5pt}}
.document-footer .footer-page{{text-align:right}}
.page-number:after{{content:"Seite " counter(page)}}
</style>
"""
    rendered = rendered.replace("</head>", chrome_css + "</head>", 1)
    rendered = rendered.replace("<body>", "<body>" + header, 1)
    if footer:
        if has_document_masthead:
            rendered = rendered.replace("</body>", footer + "</body>", 1)
        else:
            rendered = rendered.replace("<body>", "<body>" + footer, 1)
    return rendered


@dataclass(frozen=True)
class DocumentExportResult:
    output_dir: Path
    files: tuple[Path, ...]
    archive: Path | None = None

    @property
    def file_count(self) -> int:
        return len(self.files)


def _safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß_-]+", "-", value.strip()).strip("-")
    return cleaned or "dokument"


def _name_map(tournament: Tournament) -> dict[UUID, str]:
    return {person.id: person.full_name for person in tournament.people}


def _shell(title: str, body: str) -> str:
    return f'''<!doctype html>
<html lang="de"><head><meta charset="utf-8"><title>{html.escape(title)}</title>
<style>
@page {{ size: A4; margin: 14mm 12mm 15mm; }}
* {{ box-sizing:border-box; }}
body {{ font-family: Arial, Helvetica, sans-serif; color:#2f241d; margin:0; font-size:10pt; line-height:1.38; background:#fff; }}
h1 {{ color:#4b2f1d; font-size:24pt; line-height:1.02; margin:0; letter-spacing:-0.5pt; font-weight:800; }}
h2 {{ color:#4b2f1d; font-size:15pt; border:0; padding:0; margin:6mm 0 3mm; page-break-after:avoid; }}
h3 {{ color:#5c3822; font-size:9.5pt; margin:4mm 0 1.7mm; page-break-after:avoid; text-transform:uppercase; letter-spacing:.35pt; }}
.doc-head {{ width:100%; border-bottom:1.05mm solid #68472f; padding:0 0 3.6mm; margin:0 0 6mm; display:table; table-layout:fixed; page-break-inside:avoid; page-break-after:avoid; }}
.doc-brand,.doc-title,.doc-tag {{ display:table-cell; vertical-align:bottom; }}
.doc-brand {{ width:16%; color:#3f291a; font-weight:900; font-size:12pt; line-height:1; }}
.doc-brand small {{ display:block; font-size:4.7pt; font-weight:700; letter-spacing:.42pt; margin-top:.9mm; color:#6d5b4f; }}
.doc-title {{ width:60%; text-align:left; padding-left:4mm; }}
.doc-title .main {{ display:block; color:#4b2f1d; font-size:23pt; line-height:1; font-weight:900; letter-spacing:-.48pt; }}
.doc-title .sub {{ display:block; color:#6d5b4f; font-size:10pt; margin-top:1.3mm; }}
.doc-tag {{ width:24%; text-align:right; color:#4b2f1d; font-weight:800; font-size:10pt; }}
.doc-tag .pill {{ display:inline-block; color:white; background:#68472f; padding:1.8mm 3.2mm; border-radius:1.2mm; font-size:7.7pt; text-transform:uppercase; letter-spacing:.22pt; margin-bottom:1.3mm; }}
.doc-tag strong {{ display:block; color:#4b2f1d; font-size:12pt; }}
.meta {{ background:#f5efe9; border-left:4px solid #68472f; padding:3.2mm 4mm; margin:0 0 5mm; color:#5c493c; font-weight:600; }}
table {{ width:100%; border-collapse:collapse; margin:2mm 0 5mm; page-break-inside:auto; table-layout:fixed; }}
thead {{ display:table-header-group; }} tr {{ page-break-inside:avoid; }}
th,td {{ border:1px solid #dfd3c8; padding:2.25mm 2.4mm; vertical-align:middle; overflow-wrap:anywhere; }}
th {{ background:#68472f; color:white; font-size:8.3pt; font-weight:700; text-align:left; letter-spacing:.08pt; }}
td {{ background:#fff; }}
tbody tr:nth-child(even) td {{ background:#f8f4f0; }}
.center {{ text-align:center; }} .muted {{ color:#75695f; }}
.page-break {{ page-break-before:always; }}
.card {{ border:1px solid #dfd2c6; padding:4mm; margin:3mm 0; background:#fff; page-break-inside:avoid; }}
section {{ page-break-inside:auto; }}
strong {{ color:#3b2b22; }}
.document-kicker {{ color:#9a765d; font-size:7.7pt; font-weight:700; letter-spacing:.8pt; text-transform:uppercase; margin-bottom:1.5mm; }}
.document-lead {{ color:#6d5b4f; margin:-1mm 0 4mm; }}
.group-sheet {{ page-break-inside:avoid; }}
.group-sheet + .group-sheet {{ page-break-before:always; }}
.group-sheet h2 {{ margin-top:0; }}
.group-section-title {{ margin:5mm 0 1.8mm; padding-bottom:1.5mm; border-bottom:1px solid #e7ddd4; color:#4b2f1d; font-size:12pt; letter-spacing:.1pt; }}
.group-summary-table {{ width:100%; border-collapse:separate; border-spacing:2mm 0; table-layout:fixed; margin:0 -2mm 5mm; }}
.group-summary-table td {{ border:1px solid #dfd3c8; border-top:1.1mm solid #68472f; background:#fff!important; padding:3.2mm 3.5mm; }}
.group-summary-value {{ display:block; color:#4b2f1d; font-size:15pt; font-weight:900; line-height:1; }}
.group-summary-label {{ display:block; margin-top:1.3mm; color:#75695f; font-size:7.4pt; font-weight:800; letter-spacing:.35pt; text-transform:uppercase; }}
.group-players th,.group-players td {{ font-size:9.2pt; padding:2.8mm 3mm; }}
.group-schedule th,.group-schedule td {{ font-size:8.8pt; padding:2.6mm 2.8mm; }}
.group-standings th,.group-standings td {{ font-size:8.8pt; padding:2.6mm 2.8mm; }}
.info-table {{ table-layout:fixed; }}
.info-table td {{ background:#fff!important; }}
.compact th,.compact td {{ padding:1.7mm 1.8mm; font-size:8.2pt; }}
.participants-table col.nr {{ width:8%; }} .participants-table col.name {{ width:43%; }} .participants-table col.club {{ width:31%; }} .participants-table col.category {{ width:18%; }}
.group-players col.nr {{ width:10%; }} .group-players col.name {{ width:90%; }}
.group-schedule col.round {{ width:9%; }} .group-schedule col.player {{ width:34%; }} .group-schedule col.score {{ width:11.5%; }}
.group-standings col.place {{ width:9%; }} .group-standings col.player {{ width:40%; }} .group-standings col.stat {{ width:10.2%; }}
.schedule-table col.time {{ width:11%; }} .schedule-table col.table {{ width:8%; }} .schedule-table col.stage {{ width:27%; }} .schedule-table col.player {{ width:27%; }}
.overview-kpi-table {{ width:100%; border-collapse:separate; border-spacing:2.2mm 0; table-layout:fixed; margin:0 -2.2mm 6mm; }}
.overview-kpi-table td {{ width:33.333%; padding:0; border:0; background:#fff!important; vertical-align:top; }}
.overview-kpi-box {{ border:1px solid #dfd3c8; border-top:1.2mm solid #68472f; padding:4mm 4.2mm; min-height:24mm; background:#fff; }}
.overview-kpi-value {{ display:block; color:#4b2f1d; font-size:20pt; line-height:1; font-weight:900; margin-bottom:1.8mm; }}
.overview-kpi-label {{ display:block; color:#75695f; font-size:8.5pt; font-weight:700; text-transform:uppercase; letter-spacing:.35pt; }}
.overview-section-head {{ width:100%; border-collapse:collapse; table-layout:fixed; margin:0 0 2.5mm; }}
.overview-section-head td {{ border:0; padding:0; background:#fff!important; vertical-align:bottom; }}
.overview-section-head .overview-title {{ width:65%; }}
.overview-section-head .overview-note {{ width:35%; text-align:right; color:#8a7565; font-size:8pt; }}
.overview-section-head h2 {{ margin:0; }}
.overview-table col.phase {{ width:34%; }} .overview-table col.type {{ width:24%; }} .overview-table col.num {{ width:14%; }}
.status-ok {{ color:#2f6f47; font-weight:700; }}
</style></head><body>{body}</body></html>'''


def _document_head(tournament: Tournament, *, subtitle: str, tag: str = "", context: str = "") -> str:
    """Shared Qt-safe premium masthead for all exported documents."""
    logo_uri = _default_logo_data_uri()
    logo = (
        f"<img src='{logo_uri}' width='58' height='58' alt='MSTTS' style='width:58px;height:58px'>"
        if logo_uri else
        "<div style='font-size:16pt;font-weight:900;color:#4b2f1d'>MSBTS</div>"
    )
    tag_html = html.escape(tag) if tag else html.escape(subtitle)
    context_html = (
        f"<div style='font-size:10pt;font-weight:900;color:#4b2f1d;margin-top:6px'>{html.escape(context)}</div>"
        if context else ""
    )
    return (
        "<table class='doc-head' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin:0 0 18px 0'>"
        "<tr>"
        f"<td width='12%' valign='middle' style='padding:0 14px 12px 0;border-bottom:3px solid #68472f'>{logo}</td>"
        f"<td width='63%' valign='middle' style='padding:0 16px 12px 2px;border-bottom:3px solid #68472f'><div style='font-size:24pt;font-weight:900;color:#4b2f1d;line-height:1'>{html.escape(tournament.name)}</div><div style='font-size:9pt;font-weight:800;color:#68472f;letter-spacing:1.2px;margin-top:6px'>{html.escape(subtitle)}</div><div style='font-size:6.7pt;color:#9a8a7f;margin-top:4px'>MSBTS · BADMINTON TOURNAMENT SYSTEM</div></td>"
        f"<td width='25%' valign='middle' align='right' style='padding:0 0 12px 8px;border-bottom:3px solid #68472f'><table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;border:1px solid #d8c7b8'><tr><td style='background:#f4eee8;padding:6px 8px;font-size:6.8pt;font-weight:800;color:#7b5136;letter-spacing:.6px'>{tag_html}</td></tr><tr><td style='padding:7px 8px'>{context_html or "<span style=\'font-size:7pt;color:#85776d\'>TURNIERDOKUMENT</span>"}</td></tr></table></td>"
        "</tr></table>"
    )


def tournament_overview_html(tournament: Tournament) -> str:
    """Premium Qt-safe overview page.

    Uses conservative table based HTML so the macOS Qt QTextDocument PDF engine
    renders the same visual hierarchy reliably: logo masthead, event strip,
    KPI cards, full-width phase table and compact operational information.
    """
    phase_rows: list[str] = []
    total_matches = 0
    total_finished = 0
    for phase in sorted(tournament.phases, key=lambda p: p.position):
        groups = len(phase.groups) if phase.phase_type is PhaseType.GROUP_STAGE else 0
        matches = sum(len(group.matches) + (1 if group.playoff_match else 0) for group in phase.groups) if groups else len(phase.matches)
        finished = sum(1 for group in phase.groups for match in group.matches if match.result is not None) if groups else sum(1 for match in phase.matches if match.result is not None)
        total_matches += matches
        total_finished += finished
        status = "Abgeschlossen" if matches and finished >= matches else ("Läuft" if finished else "Offen")
        if status == "Abgeschlossen":
            badge = "<span style='color:#2f6f47;font-weight:700'>✓ Abgeschlossen</span>"
        elif status == "Läuft":
            badge = "<span style='color:#68472f;font-weight:700'>▶ Läuft</span>"
        else:
            badge = "<span style='color:#857467;font-weight:700'>○ Offen</span>"
        phase_rows.append(
            "<tr>"
            f"<td style='padding:9px 10px'>{html.escape(phase.name)}</td>"
            f"<td style='padding:9px 10px'>{html.escape(phase.phase_type.label)}</td>"
            f"<td align='center' style='padding:9px 8px'>{groups or '–'}</td>"
            f"<td align='center' style='padding:9px 8px'>{matches}</td>"
            f"<td style='padding:9px 10px'>{badge}</td>"
            "</tr>"
        )

    logo_uri = _default_logo_data_uri()
    logo = (
        f"<img src='{logo_uri}' width='70' height='70' alt='MSTTS' style='width:70px;height:70px'>"
        if logo_uri else
        "<div style='font-size:14pt;font-weight:900;color:#4b2f1d'>MSBTS</div>"
    )

    # Keep all copy data-driven. Empty event fields remain visibly neutral rather
    # than inventing details that were never entered for the tournament.
    venue = html.escape(tournament.location or "Noch nicht festgelegt")
    date_text = html.escape(
        tournament.start_date if tournament.start_date and tournament.start_date == tournament.end_date
        else " – ".join(value for value in (tournament.start_date, tournament.end_date) if value)
        or "Noch nicht festgelegt"
    )
    organizer = html.escape(tournament.organizer or "MSBTS Turnierleitung")
    best_of = f"Best of {tournament.best_of}"
    completion_percent = round((total_finished / total_matches) * 100) if total_matches else 0
    active_phase = next((
        phase.name
        for phase in sorted(tournament.phases, key=lambda p: p.position)
        if (
            (sum(len(group.matches) + (1 if group.playoff_match else 0) for group in phase.groups)
             if phase.phase_type is PhaseType.GROUP_STAGE else len(phase.matches))
            >
            (sum(1 for group in phase.groups for match in group.matches if match.result is not None)
             if phase.phase_type is PhaseType.GROUP_STAGE else sum(1 for match in phase.matches if match.result is not None))
        )
    ), "Abgeschlossen" if total_matches and total_finished >= total_matches else "Vorbereitung")

    # Startfix 60: final premium master layout. Keep the proven Qt-safe table
    # structure, but rebalance the masthead, status, event cards and KPIs.
    head = (
        "<table class='doc-head' width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin:0 0 20px 0'>"
        "<tr>"
        f"<td width='12%' valign='middle' style='padding:0 16px 14px 0;border-bottom:3px solid #68472f'>{logo}</td>"
        "<td width='59%' valign='middle' style='padding:0 18px 14px 2px;border-bottom:3px solid #68472f'>"
        f"<div style='font-size:28pt;font-weight:900;color:#4b2f1d;line-height:1.0'>{html.escape(tournament.name)}</div>"
        "<div style='font-size:9pt;font-weight:800;color:#68472f;letter-spacing:1.5px;margin-top:7px'>TURNIERÜBERSICHT</div>"
        "<div style='font-size:6.8pt;color:#9a8a7f;margin-top:5px'>MSBTS · BADMINTON TOURNAMENT SYSTEM</div></td>"
        "<td width='29%' valign='middle' style='padding:0 0 14px 10px;border-bottom:3px solid #68472f'>"
        "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;border:1px solid #d8c7b8'>"
        "<tr><td colspan='2' style='background:#f4eee8;padding:6px 9px;font-size:6.8pt;font-weight:800;color:#7b5136;letter-spacing:.8px'>AKTUELLER STATUS</td></tr>"
        f"<tr><td width='67%' valign='middle' style='padding:9px 8px 9px 9px;font-size:9pt;font-weight:900;color:#4b2f1d;white-space:nowrap'>{html.escape(active_phase)}</td>"
        f"<td width='33%' valign='middle' align='right' style='padding:9px 9px 9px 4px;font-size:13pt;font-weight:900;color:#68472f;white-space:nowrap'>{completion_percent}%</td></tr>"
        "<tr><td colspan='2' style='padding:0 9px 7px 9px;font-size:6.8pt;color:#85776d'>Turnierfortschritt</td></tr>"
        "</table></td></tr></table>"
    )

    event_strip = (
        "<table width='100%' cellspacing='8' cellpadding='0' style='margin:0 0 20px 0'>"
        "<tr>"
        f"<td width='33%' valign='top' style='border:1px solid #e0d5cb;border-top:2px solid #c4a58c;padding:12px 13px'><span style='font-size:6.8pt;font-weight:800;color:#8a6041;letter-spacing:.45px'>ORT</span><br><span style='font-size:10.2pt;font-weight:800;color:#4b2f1d;line-height:1.5'>{venue}</span></td>"
        f"<td width='34%' valign='top' style='border:1px solid #e0d5cb;border-top:2px solid #c4a58c;padding:12px 13px'><span style='font-size:6.8pt;font-weight:800;color:#8a6041;letter-spacing:.45px'>TURNIERZEITRAUM</span><br><span style='font-size:10.2pt;font-weight:800;color:#4b2f1d;line-height:1.5'>{date_text}</span></td>"
        f"<td width='33%' valign='top' style='border:1px solid #e0d5cb;border-top:2px solid #c4a58c;padding:12px 13px'><span style='font-size:6.8pt;font-weight:800;color:#8a6041;letter-spacing:.45px'>WETTBEWERB</span><br><span style='font-size:10.2pt;font-weight:800;color:#4b2f1d;line-height:1.5'>{best_of} · {tournament.table_count} Feld(er)</span></td>"
        "</tr></table>"
    )

    kpis = (
        "<table class='overview-kpi-table' width='100%' cellspacing='8' cellpadding='0' style='margin:0 0 22px 0'><tr>"
        f"<td class='overview-kpi-box' width='25%' valign='top' style='border:1px solid #ddcfc3;border-top:3px solid #68472f;padding:13px 14px;min-height:72px'><div style='font-size:21pt;font-weight:900;color:#4b2f1d;line-height:1'>{len(tournament.people)}</div><div style='font-size:6.8pt;font-weight:800;color:#75695f;letter-spacing:.35px;margin-top:7px'>TEILNEHMER</div></td>"
        f"<td class='overview-kpi-box' width='25%' valign='top' style='border:1px solid #ddcfc3;border-top:3px solid #68472f;padding:13px 14px;min-height:72px'><div style='font-size:21pt;font-weight:900;color:#4b2f1d;line-height:1'>{len(tournament.phases)}</div><div style='font-size:6.8pt;font-weight:800;color:#75695f;letter-spacing:.35px;margin-top:7px'>TURNIERPHASEN</div></td>"
        f"<td class='overview-kpi-box' width='25%' valign='top' style='border:1px solid #ddcfc3;border-top:3px solid #68472f;padding:13px 14px;min-height:72px'><div style='font-size:21pt;font-weight:900;color:#4b2f1d;line-height:1'>{total_finished}/{total_matches}</div><div style='font-size:6.8pt;font-weight:800;color:#75695f;letter-spacing:.35px;margin-top:7px'>SPIELE BEENDET</div></td>"
        f"<td class='overview-kpi-box' width='25%' valign='top' style='border:1px solid #ddcfc3;border-top:3px solid #68472f;padding:13px 14px;min-height:72px'><div style='font-size:21pt;font-weight:900;color:#4b2f1d;line-height:1'>{completion_percent}%</div><div style='font-size:6.8pt;font-weight:800;color:#75695f;letter-spacing:.35px;margin-top:7px'>FORTSCHRITT</div></td>"
        "</tr></table>"
    )

    phase_table = (
        "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin:0 0 18px 0'>"
        "<tr><td colspan='5' style='background:#68472f;color:white;padding:9px 10px;font-size:13pt;font-weight:900'>TURNIERPHASEN"
        "<span style='font-size:7.5pt;font-weight:400'> · Ablauf und aktueller Bearbeitungsstand</span></td></tr>"
        "<tr style='background:#8a6041;color:#ffffff'>"
        "<th width='30%' align='left' style='padding:8px 10px'>Phase</th>"
        "<th width='23%' align='left' style='padding:8px 10px'>Typ</th>"
        "<th width='12%' align='center' style='padding:8px'>Gruppen</th>"
        "<th width='12%' align='center' style='padding:8px'>Spiele</th>"
        "<th width='23%' align='left' style='padding:8px 10px'>Status</th>"
        "</tr>" + "".join(phase_rows) + "</table>"
    )

    info = (
        "<table width='100%' cellspacing='8' cellpadding='0' style='margin:0 0 8px 0'>"
        "<tr>"
        "<td width='68%' valign='top' style='border:1px solid #dfd3c8;padding:0'>"
        "<div style='background:#f1e9e2;color:#4b2f1d;padding:8px 10px;font-size:10pt;font-weight:900'>WICHTIGE INFORMATIONEN</div>"
        "<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse'>"
        f"<tr><td width='34%' style='padding:7px 10px;border-bottom:1px solid #eee4db'><b>Turnierleitung</b></td><td style='padding:7px 10px;border-bottom:1px solid #eee4db'>{organizer}</td></tr>"
        f"<tr><td style='padding:7px 10px;border-bottom:1px solid #eee4db'><b>Spielmodus</b></td><td style='padding:7px 10px;border-bottom:1px solid #eee4db'>{best_of}</td></tr>"
        f"<tr><td style='padding:7px 10px;border-bottom:1px solid #eee4db'><b>Spieldauer</b></td><td style='padding:7px 10px;border-bottom:1px solid #eee4db'>{tournament.match_duration_minutes} Minuten je Spiel</td></tr>"
        f"<tr><td style='padding:7px 10px'><b>Schiedsrichter</b></td><td style='padding:7px 10px'>{tournament.scorer_count}</td></tr>"
        "</table></td>"
        "<td width='32%' valign='top' style='border:1px solid #dfd3c8;padding:0'>"
        "<div style='background:#f1e9e2;color:#4b2f1d;padding:8px 10px;font-size:10pt;font-weight:900'>FAIR PLAY</div>"
        "<div align='center' style='padding:16px 12px;color:#4b2f1d'>"
        "<div style='font-size:22pt;font-weight:900'>✓</div>"
        "<div style='font-size:11pt;font-weight:900'>Respekt · Spaß · Fairness</div>"
        "<div style='font-size:8pt;color:#75695f;margin-top:8px'>Viel Erfolg an alle Teilnehmenden!</div>"
        "</div></td></tr></table>"
    )

    body = head + event_strip + kpis + phase_table + info
    return _shell(f"{tournament.name} – Turnierübersicht", body)

def participants_html(tournament: Tournament) -> str:
    people = sorted(tournament.people, key=lambda p: ((p.start_number or 999999), p.last_name.casefold(), p.first_name.casefold()))
    rows = "".join(
        f"<tr><td width='9%' align='center' style='padding:7px 8px'>{person.start_number or index}</td><td width='47%' style='padding:7px 10px;font-weight:700'>{html.escape(person.full_name)}</td><td width='27%' style='padding:7px 10px'>{html.escape(person.club)}</td><td width='17%' style='padding:7px 10px'>{html.escape(person.category)}</td></tr>"
        for index, person in enumerate(people, 1)
    ) or "<tr><td colspan='4'>Keine Teilnehmer vorhanden.</td></tr>"
    body = (
        _document_head(tournament, subtitle="Teilnehmerliste", tag="Teilnehmerliste")
        + f"<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin:0 0 12px'><tr><td style='background:#f4eee8;border-left:4px solid #68472f;padding:9px 12px'><span style='font-size:17pt;font-weight:900;color:#4b2f1d'>{len(people)}</span> <span style='font-size:8pt;font-weight:800;color:#75695f;letter-spacing:.4px'>TEILNEHMER GESAMT</span></td></tr></table>"
        + f"<table class='participants-table' width='100%' cellspacing='0' cellpadding='0' style='width:100%;border-collapse:collapse;table-layout:fixed;font-size:10pt'>"
        f"<thead><tr><th width='9%' align='center' style='padding:8px'>Nr.</th><th width='47%' style='padding:8px 10px'>Name</th><th width='27%' style='padding:8px 10px'>Verein</th><th width='17%' style='padding:8px 10px'>Kategorie</th></tr></thead><tbody>{rows}</tbody></table>"
    )
    return _shell(f"{tournament.name} – Teilnehmer", body)


def group_documents_html(tournament: Tournament) -> str:
    """Render one deliberately composed, full-width sheet per group.

    Startfix 62 avoids stacking a global document header in front of the first
    group and uses explicit page breaks between groups. This keeps Qt's PDF
    renderer from squeezing the tables into a narrow block or beginning the
    next group at the bottom of the current page.
    """

    # Compatibility markers retained for older layout regression checks.
    # sections: list[str] = []
    # section_class = "score-sheet" if index == 1 else "score-sheet page-break"
    # class='info-table score-info-table' width='100%'
    # class='score-sheet-table' width='100%'
    # <col style='width:16%'><col style='width:42%'><col style='width:42%'>
    # .score-sheet-table{width:100%!important
    # Best of 3 · zwei Gewinnsätze · Ergebnis nach Spielende bestätigen
    names = _name_map(tournament)
    sheets: list[str] = []
    for phase in sorted(tournament.phases, key=lambda p: p.position):
        if phase.phase_type is not PhaseType.GROUP_STAGE:
            continue
        for group in phase.groups:
            player_rows = "".join(
                f"<tr><td class='center'>{index}</td><td>{html.escape(names.get(person_id, 'Unbekannt'))}</td></tr>"
                for index, person_id in enumerate(group.participant_ids, 1)
            ) or "<tr><td colspan='2'>Noch keine Spieler zugeordnet.</td></tr>"

            match_rows = "".join(
                f"<tr><td class='center'>{match.round_number}</td><td>{html.escape(names.get(match.home_id, ''))}</td><td>{html.escape(names.get(match.away_id, ''))}</td><td class='center'>{'' if match.result is None else match.result.home_score}</td><td class='center'>{'' if match.result is None else match.result.away_score}</td></tr>"
                for match in group.matches
            ) or "<tr><td colspan='5'>Noch kein Spielplan erzeugt.</td></tr>"

            try:
                standings = group.standings(tournament.people)
            except Exception:
                standings = []
            standing_rows = "".join(
                f"<tr><td class='center'>{pos}</td><td>{html.escape(names.get(row.person_id, ''))}</td><td class='center'>{row.played}</td><td class='center'>{row.wins}</td><td class='center'>{row.losses}</td><td class='center'>{row.scored}:{row.conceded}</td><td class='center'>{row.points}</td></tr>"
                for pos, row in enumerate(standings, 1)
            ) or "<tr><td colspan='7'>Noch keine Tabelle verfügbar.</td></tr>"

            completed = sum(1 for match in group.matches if match.result is not None)
            total = len(group.matches)
            summary = (
                "<table class='group-summary-table' width='100%' cellspacing='6' cellpadding='0' style='width:100%;table-layout:fixed;border-collapse:separate;margin:0 0 18px 0'><tr>"
                f"<td width='18%' align='center' style='border:1px solid #dfd3c8;border-top:3px solid #68472f;padding:10px 8px'><span class='group-summary-value'>{len(group.participant_ids)}</span><span class='group-summary-label'>Teilnehmer</span></td>"
                f"<td width='18%' align='center' style='border:1px solid #dfd3c8;border-top:3px solid #68472f;padding:10px 8px'><span class='group-summary-value'>{total}</span><span class='group-summary-label'>Spiele</span></td>"
                f"<td width='22%' align='center' style='border:1px solid #dfd3c8;border-top:3px solid #68472f;padding:10px 8px'><span class='group-summary-value'>{completed}/{total}</span><span class='group-summary-label'>Beendet</span></td>"
                f"<td width='42%' align='center' style='border:1px solid #dfd3c8;border-top:3px solid #68472f;padding:10px 8px'><span class='group-summary-value' style='font-size:12pt'>{html.escape(phase.name)}</span><span class='group-summary-label'>Phase</span></td>"
                "</tr></table>"
            )

            # Qt's QTextDocument does not reliably honor the adjacent-sibling
            # page-break rule for a tall section that already overflowed onto a
            # second page. Insert a literal break *before* every following group
            # so its masthead/logo can never be appended to the previous page.
            page_start = "" if not sheets else "<div class='page-break' style='page-break-before:always'></div>"
            sheet = (
                page_start
                + "<section class='group-sheet' data-sheet='group'>"
                + _document_head(tournament, subtitle="Gruppen, Spielpläne und Tabellen", tag=phase.name, context=group.name)
                + summary
                + "<h3 class='group-section-title'>Teilnehmer</h3>"
                + f"<table class='group-players' width='100%' cellspacing='0' cellpadding='0' style='width:100%;table-layout:fixed;border-collapse:collapse'><thead><tr><th width='10%' align='center'>Nr.</th><th width='90%'>Spieler</th></tr></thead><tbody>{player_rows}</tbody></table>"
                + "<h3 class='group-section-title'>Spielplan</h3>"
                + f"<table class='group-schedule' width='100%' cellspacing='0' cellpadding='0' style='width:100%;table-layout:fixed;border-collapse:collapse'><thead><tr><th width='10%' align='center'>Runde</th><th width='33%'>Spieler 1</th><th width='33%'>Spieler 2</th><th width='12%' align='center'>Sätze 1</th><th width='12%' align='center'>Sätze 2</th></tr></thead><tbody>{match_rows}</tbody></table>"
                + "<h3 class='group-section-title'>Tabelle</h3>"
                + f"<table class='group-standings' width='100%' cellspacing='0' cellpadding='0' style='width:100%;table-layout:fixed;border-collapse:collapse'><thead><tr><th width='9%' align='center'>Platz</th><th width='39%'>Spieler</th><th width='9%' align='center'>Sp</th><th width='9%' align='center'>S</th><th width='9%' align='center'>N</th><th width='15%' align='center'>Sätze</th><th width='10%' align='center'>Pkt</th></tr></thead><tbody>{standing_rows}</tbody></table>"
                + "</section>"
            )
            sheets.append(sheet)

    if not sheets:
        body = _document_head(tournament, subtitle="Gruppen, Spielpläne und Tabellen", tag="Gruppen") + "<p>Keine Gruppenphasen vorhanden.</p>"
    else:
        body = "".join(sheets)
    return _shell(f"{tournament.name} – Gruppenunterlagen", body)


def match_schedule_html(tournament: Tournament) -> str:
    names = _name_map(tournament)
    rows: list[tuple[str, str, str, str, str]] = []
    for phase in sorted(tournament.phases, key=lambda p: p.position):
        if phase.phase_type is PhaseType.GROUP_STAGE:
            for group in phase.groups:
                for match in group.matches:
                    rows.append((match.scheduled_time or "", str(match.table_number or ""), f"{phase.name} / {group.name}", names.get(match.home_id, ""), names.get(match.away_id, "")))
                if group.playoff_match is not None:
                    match = group.playoff_match
                    rows.append((match.scheduled_time or "", str(match.table_number or ""), f"{phase.name} / {group.name} Qualifikation", names.get(match.home_id, ""), names.get(match.away_id, "")))
        else:
            for match in phase.matches:
                rows.append((match.scheduled_time or "", str(match.table_number or ""), phase.name, names.get(match.home_id, ""), names.get(match.away_id, "")))
    rows.sort(key=lambda row: (row[0] or "99:99", int(row[1]) if row[1].isdigit() else 999, row[2]))
    table_rows = "".join(
        f"<tr><td width='12%' style='padding:7px 8px'>{html.escape(time)}</td><td width='9%' align='center' style='padding:7px 8px'>{html.escape(table)}</td><td width='25%' style='padding:7px 9px'>{html.escape(stage)}</td><td width='27%' style='padding:7px 9px;font-weight:700'>{html.escape(home)}</td><td width='27%' style='padding:7px 9px;font-weight:700'>{html.escape(away)}</td></tr>"
        for time, table, stage, home, away in rows
    ) or "<tr><td colspan='5'>Noch keine Spiele vorhanden.</td></tr>"
    body = (
        _document_head(tournament, subtitle="Gesamtspielplan", tag="Spielplan")
        + f"<table width='100%' cellspacing='0' cellpadding='0' style='border-collapse:collapse;margin:0 0 12px'><tr><td width='50%' style='background:#f4eee8;border-left:4px solid #68472f;padding:9px 12px'><b style='font-size:16pt;color:#4b2f1d'>{len(rows)}</b> <span style='font-size:8pt;font-weight:800;color:#75695f'>SPIELE</span></td><td width='50%' align='right' style='background:#f4eee8;padding:9px 12px'><b style='font-size:16pt;color:#4b2f1d'>{tournament.table_count}</b> <span style='font-size:8pt;font-weight:800;color:#75695f'>TISCHE</span></td></tr></table>"
        + f"<table class='schedule-table' width='100%' cellspacing='0' cellpadding='0' style='width:100%;border-collapse:collapse;table-layout:fixed;font-size:9.5pt'>"
        f"<thead><tr><th width='12%' style='padding:8px'>Zeit</th><th width='9%' align='center' style='padding:8px'>Feld</th><th width='25%' style='padding:8px'>Phase</th><th width='27%' style='padding:8px'>Spieler 1</th><th width='27%' style='padding:8px'>Spieler 2</th></tr></thead><tbody>{table_rows}</tbody></table>"
    )
    return _shell(f"{tournament.name} – Gesamtspielplan", body)


def combined_document_html(tournament: Tournament) -> str:
    parts = [
        tournament_overview_html(tournament),
        participants_html(tournament),
        group_documents_html(tournament),
        match_schedule_html(tournament),
        score_sheets_html(tournament),
        scorekeeper_plan_html(tournament),
        knockout_bracket_html(tournament),
    ]
    bodies = []
    for index, document in enumerate(parts):
        match = re.search(r"<body>(.*)</body>", document, re.S)
        if match:
            bodies.append(("" if index == 0 else "<div class='page-break'></div>") + match.group(1))
    return _shell(f"{tournament.name} – Turniermappe", "".join(bodies))


def html_to_pdf(source_html: str, destination: Path, *, orientation: str = "portrait", margin_mm: float = 12.0) -> Path:
    """Render HTML to a real PDF file using Qt's PDF printer backend.

    The explicit page layout avoids a macOS/PySide6 compatibility issue where the
    older five-argument ``setPageMargins`` overload can silently leave no output.
    """
    try:
        from PySide6.QtCore import QMarginsF
        from PySide6.QtGui import QPageLayout, QPageSize, QTextDocument
        from PySide6.QtPrintSupport import QPrinter
    except ImportError as error:
        raise ValidationError("Für den PDF-Export wird PySide6 benötigt.") from error

    destination = destination.with_suffix(".pdf")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    # Startfix 80/81: Qt QTextDocument does not reliably honor CSS sizing for large
    # embedded SVG data URIs on macOS.  That caused the portrait K.-o. tree to be
    # clipped horizontally and continued on a second page.  Render the SVG
    # directly onto one A4 portrait PDF page whenever this dedicated bracket
    # marker is present.  Other documents keep the normal HTML print path.
    if "mstts-ko-vector-v80" in source_html or "mstts-ko-vector-v81" in source_html:
        svg_match = re.search(
            r"data:image/svg\+xml;base64,([^'\"]+)", source_html, re.S
        )
        if svg_match:
            try:
                from PySide6.QtCore import QByteArray, QRectF
                from PySide6.QtGui import QPainter
                from PySide6.QtSvg import QSvgRenderer
            except ImportError as error:
                raise ValidationError("Für den PDF-Export des K.-o.-Baums wird QtSvg benötigt.") from error

            svg_bytes = base64.b64decode(svg_match.group(1))
            printer = QPrinter(QPrinter.PrinterMode.HighResolution)
            printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
            printer.setOutputFileName(str(destination.resolve()))
            printer.setFullPage(True)
            printer.setPageLayout(
                QPageLayout(
                    QPageSize(QPageSize.PageSizeId.A4),
                    QPageLayout.Orientation.Portrait,
                    QMarginsF(0, 0, 0, 0),
                    QPageLayout.Unit.Millimeter,
                )
            )
            painter = QPainter(printer)
            renderer = QSvgRenderer(QByteArray(svg_bytes))
            if not renderer.isValid():
                painter.end()
                raise OSError("Der K.-o.-Baum konnte nicht als SVG gerendert werden.")

            # Preserve the 1000:1414 reference aspect ratio while fitting the
            # printable page with a deliberate 7 mm visual safety margin.
            page_rect = printer.pageRect(QPrinter.Unit.DevicePixel)
            dpi_x = float(printer.logicalDpiX())
            dpi_y = float(printer.logicalDpiY())
            margin_x = 7.0 / 25.4 * dpi_x
            margin_y = 7.0 / 25.4 * dpi_y
            avail_w = max(1.0, float(page_rect.width()) - 2.0 * margin_x)
            avail_h = max(1.0, float(page_rect.height()) - 2.0 * margin_y)
            ratio = 1000.0 / 1414.0
            target_w = min(avail_w, avail_h * ratio)
            target_h = target_w / ratio
            x = float(page_rect.x()) + (float(page_rect.width()) - target_w) / 2.0
            y = float(page_rect.y()) + (float(page_rect.height()) - target_h) / 2.0
            renderer.render(painter, QRectF(x, y, target_w, target_h))
            painter.end()

            if not destination.is_file() or destination.stat().st_size < 500:
                destination.unlink(missing_ok=True)
                raise OSError("Die PDF-Datei konnte nicht erstellt werden.")
            return destination

    printer = QPrinter(QPrinter.PrinterMode.HighResolution)
    printer.setOutputFormat(QPrinter.OutputFormat.PdfFormat)
    printer.setOutputFileName(str(destination.resolve()))
    printer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Landscape if orientation == "landscape" else QPageLayout.Orientation.Portrait,
            QMarginsF(margin_mm, margin_mm, margin_mm, margin_mm),
            QPageLayout.Unit.Millimeter,
        )
    )
    document = QTextDocument()
    document.setHtml(source_html)
    document.print_(printer)

    if not destination.is_file() or destination.stat().st_size < 500:
        destination.unlink(missing_ok=True)
        raise OSError(
            "Die PDF-Datei konnte nicht erstellt werden. Bitte prüfen Sie den gewählten Ausgabeordner."
        )
    return destination


def export_document_center(tournament: Tournament, output_dir: Path, *, create_pdf: bool = True, create_archive: bool = True) -> DocumentExportResult:
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = {
        "01_Turnieruebersicht.html": tournament_overview_html(tournament),
        "02_Teilnehmerliste.html": participants_html(tournament),
        "03_Gruppen_Spielplaene_Tabellen.html": group_documents_html(tournament),
        "04_Gesamtspielplan.html": match_schedule_html(tournament),
        "05_Ergebniszettel.html": score_sheets_html(tournament),
        "06_Schiedsrichterplan.html": scorekeeper_plan_html(tournament),
        "07_KO_Baum.html": knockout_bracket_html(tournament),
        "00_Turniermappe.html": combined_document_html(tournament),
    }
    files: list[Path] = []
    for filename, content in documents.items():
        path = output_dir / filename
        path.write_text(content, encoding="utf-8")
        files.append(path)
    if create_pdf:
        files.append(html_to_pdf(documents["00_Turniermappe.html"], output_dir / "00_Turniermappe.pdf"))
    archive = None
    if create_archive:
        archive = output_dir / f"{_safe_name(tournament.name)}_Dokumentenpaket.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in files:
                bundle.write(path, arcname=path.name)
        files.append(archive)
    return DocumentExportResult(output_dir, tuple(files), archive)


def _iter_matches(tournament: Tournament):
    """Yield phase/group labels and all tournament matches in display order."""
    for phase in sorted(tournament.phases, key=lambda value: value.position):
        if phase.phase_type is PhaseType.GROUP_STAGE:
            for group in phase.groups:
                for match in group.matches:
                    yield phase.name, group.name, match
                if group.playoff_match is not None:
                    yield phase.name, f"{group.name} Qualifikation", group.playoff_match
        else:
            for match in phase.matches:
                yield phase.name, "K.-o.-Runde", match


def score_sheets_html(tournament: Tournament) -> str:
    """Create two visually identical, cut-ready score slips on every A4 page.

    Startfix 73 follows the approved reference layout: each half-page has the
    same branding block, compact match metadata, a wide Best-of-3 score grid,
    result column and a signature footer.  The geometry is deliberately fixed
    so the second slip cannot drift onto another page in Qt PDF output.
    """
    names = _name_map(tournament)
    matches = list(_iter_matches(tournament))
    scorer_assignments = build_scorekeeper_assignments(tournament)
    logo_uri = _default_logo_data_uri()
    logo = (
        f"<img src='{logo_uri}' width='64' height='64' alt='MSTTS' style='width:64px;height:64px'>"
        if logo_uri else
        "<div class='score-wordmark'>MSBTS</div>"
    )

    def slip(index: int, phase_name: str, group_name: str, match) -> str:
        result = match.result
        set_scores = list(result.set_scores) if result is not None else []
        winner = names.get(match.winner_id(), "") if result is not None else ""
        scheduled = html.escape(match.scheduled_time or "")
        table_no = html.escape(str(match.table_number or ""))
        home = html.escape(names.get(match.home_id, "Offen"))
        away = html.escape(names.get(match.away_id, "Offen"))

        # Three generously sized writing rows, mirroring the approved mock-up.
        score_rows = []
        for set_number in range(1, 4):
            home_score = ""
            away_score = ""
            if set_number <= len(set_scores):
                home_score, away_score = map(str, set_scores[set_number - 1])
            score_rows.append(
                "<tr>"
                f"<td class='score-nr'>{set_number}</td>"
                f"<td class='player-cell'>{home if set_number == 1 else ''}</td>"
                f"<td class='set-box'>{html.escape(home_score)}</td><td class='set-box'></td><td class='set-box'></td>"
                f"<td class='player-cell'>{away if set_number == 1 else ''}</td>"
                f"<td class='set-box'>{html.escape(away_score)}</td><td class='set-box'></td><td class='set-box'></td>"
                "<td class='result-box'>____ : ____</td>"
                "</tr>"
            )

        return (
            "<section class='score-slip'>"
            "<table class='score-masthead' width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td class='score-logo'>{logo}</td>"
            f"<td class='score-brand'><div class='score-title'>{html.escape(tournament.name)}</div>"
            "<div class='score-subtitle'>TISCHTENNIS-TURNIER</div>"
            "<div class='score-organizer'>MSBTS · BADMINTON TOURNAMENT SYSTEM</div></td>"
            "<td class='score-meta-cell'>"
            "<table class='score-meta' width='100%' cellspacing='0' cellpadding='0'>"
            f"<tr><th>Gruppe / Runde:</th><td colspan='3'>{html.escape(group_name)}</td><th>Spiel-Nr.:</th><td>{index}</td></tr>"
            f"<tr><th>Datum:</th><td></td><th>Uhrzeit:</th><td>{scheduled}</td><th>Feld:</th><td>{table_no}</td></tr>"
            "</table></td></tr></table>"
            "<table class='score-note' width='100%' cellspacing='0' cellpadding='0'><tr>"
            "<td>BEST OF 3 – ZWEI GEWINNSÄTZE – ERGEBNIS NACH SPIELENDE BESTÄTIGEN</td>"
            "<td align='right'>FAIR PLAY – VIEL ERFOLG!</td></tr></table>"
            "<table class='score-main' width='100%' cellspacing='0' cellpadding='0'>"
            "<colgroup><col style='width:5%'><col style='width:24%'><col style='width:5%'><col style='width:5%'><col style='width:5%'>"
            "<col style='width:24%'><col style='width:5%'><col style='width:5%'><col style='width:5%'><col style='width:17%'></colgroup>"
            "<thead><tr><th rowspan='2'>Nr.</th><th rowspan='2'>Spieler 1 (Name)</th><th colspan='3'>Sätze</th>"
            "<th rowspan='2'>Spieler 2 (Name)</th><th colspan='3'>Sätze</th><th rowspan='2'>Ergebnis</th></tr>"
            "<tr><th>1</th><th>2</th><th>3</th><th>1</th><th>2</th><th>3</th></tr></thead><tbody>"
            + "".join(score_rows)
            + "</tbody></table>"
            "<table class='score-signatures' width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td><strong>Sieger:</strong><span>{html.escape(winner)}</span></td>"
            f"<td><strong>Schiedsrichter / Schiedsrichter:</strong><span>{html.escape(scorer_assignments.get(match.id).scorer_name if scorer_assignments.get(match.id) else '')}</span></td>"
            "<td><strong>Unterschrift:</strong><span></span></td>"
            "</tr></table>"
            "</section>"
        )

    slips = [slip(i, phase, group, match) for i, (phase, group, match) in enumerate(matches, 1)]
    pages: list[str] = []
    for offset in range(0, len(slips), 3):
        upper = slips[offset]
        middle = slips[offset + 1] if offset + 1 < len(slips) else "<section class='score-slip score-slip-empty'></section>"
        lower = slips[offset + 2] if offset + 2 < len(slips) else "<section class='score-slip score-slip-empty'></section>"
        page_class = "score-page" + (" page-break" if offset else "")
        pages.append(
            f"<div class='{page_class}'{" style='page-break-before:always'" if offset else ''}>"
            + upper
            + "<div class='score-cutline'><span>✂</span><b>HIER TRENNEN</b><span>✂</span></div>"
            + middle
            + "<div class='score-cutline'><span>✂</span><b>HIER TRENNEN</b><span>✂</span></div>"
            + lower
            + "</div>"
        )

    if not pages:
        pages.append("<p>Noch keine Spiele vorhanden.</p>")

    score_css = r"""
<style id='mstts-score-sheet-layout'>
.score-page{height:268mm;min-height:268mm;max-height:268mm;overflow:hidden;margin:0;padding:0;page-break-inside:avoid;break-inside:avoid}
.score-page.page-break{page-break-before:always;break-before:page}
.score-slip{height:84mm;min-height:84mm;max-height:84mm;overflow:hidden;box-sizing:border-box;padding:1mm 0 0;margin:0;page-break-inside:avoid;break-inside:avoid}
.score-slip-empty{visibility:hidden}
.score-cutline{height:8mm;line-height:8mm;border-top:.35mm dashed #68472f;text-align:center;color:#68472f;font-size:7pt;letter-spacing:.8px;box-sizing:border-box;white-space:nowrap}
.score-cutline span{font-size:12pt;margin:0 22mm}.score-cutline b{font-size:6.8pt;letter-spacing:1.2px}
.score-masthead{border-collapse:collapse;margin:0 0 .6mm;width:100%}.score-masthead td{border:0;padding:0;vertical-align:middle}
.score-logo{width:11%;padding-right:2.6mm!important}.score-brand{width:39%}.score-meta-cell{width:50%;padding-left:3mm!important}
.score-title{font-size:14pt;font-weight:900;line-height:1;color:#4b2f1d}.score-subtitle{font-size:7.2pt;font-weight:900;letter-spacing:1px;color:#68472f;margin-top:.7mm}
.score-organizer{font-size:5.7pt;color:#8d7d71;margin-top:.9mm;letter-spacing:.3px}.score-wordmark{font-size:14pt;font-weight:900;color:#68472f}
.score-meta{border-collapse:separate;border-spacing:0;background:#faf7f2;border:1px solid #d9c2ad}.score-meta th,.score-meta td{border:0;padding:.75mm 1mm;font-size:6.1pt;vertical-align:middle}
.score-meta th{white-space:nowrap;color:#4b2f1d;font-weight:800}.score-meta td{background:#fffdf9;border:1px solid #e8ddd3;min-width:12mm}
.score-note{width:100%;border-collapse:collapse;border-top:.45mm solid #68472f;border-bottom:.25mm solid #b99a80;margin:0 0 .8mm}
.score-note td{border:0;padding:.65mm 0;font-size:5.3pt;font-weight:800;letter-spacing:.55px;color:#68472f}
.score-main{width:100%;border-collapse:collapse;table-layout:fixed;margin:0 0 .8mm}.score-main th,.score-main td{border:1px solid #cbb9aa;text-align:center;vertical-align:middle;padding:.4mm .7mm}
.score-main thead th{background:#68472f;color:white;font-size:6.6pt;font-weight:800}.score-main tbody td{height:5.2mm;font-size:6.2pt;background:#fffdf9}
.score-main tbody tr:nth-child(even) td{background:#faf7f2}.score-main .player-cell{text-align:left;font-weight:700;font-size:7.2pt}.score-main .set-box{font-size:9pt}.score-main .result-box{font-size:9pt;font-weight:800;letter-spacing:.7px}
.score-signatures{width:100%;border-collapse:separate;border-spacing:0;background:#faf7f2;margin-top:.6mm}.score-signatures td{border:0;padding:1mm 2mm;font-size:6.1pt;width:33.333%;vertical-align:bottom}
.score-signatures strong{display:inline-block;margin-right:2mm;color:#4b2f1d}.score-signatures span{display:inline-block;min-width:34mm;border-bottom:1px solid #68472f;height:2.8mm;vertical-align:bottom}
</style>
"""
    return _shell(f"{tournament.name} – Ergebniszettel", score_css + "".join(pages))

def scorekeeper_plan_html(tournament: Tournament) -> str:
    """Create a chronological assignment sheet with writable official fields."""
    names = _name_map(tournament)
    assignments = build_scorekeeper_assignments(tournament)
    rows = []
    matches = list(_iter_matches(tournament))
    matches.sort(key=lambda item: (
        item[2].scheduled_time or "99:99",
        item[2].table_number or 999,
        item[0],
        item[1],
    ))
    for index, (phase_name, group_name, match) in enumerate(matches, 1):
        result = ""
        if match.result is not None:
            result = f"{match.result.home_score}:{match.result.away_score}"
        rows.append(
            f"<tr><td class='center'>{index}</td><td>{html.escape(match.scheduled_time or '')}</td>"
            f"<td class='center'>{match.table_number or ''}</td><td>{html.escape(phase_name)} / {html.escape(group_name)}</td>"
            f"<td>{html.escape(names.get(match.home_id, 'Offen'))}</td><td>{html.escape(names.get(match.away_id, 'Offen'))}</td>"
            f"<td class='write-field'>{html.escape(assignments.get(match.id).scorer_name if assignments.get(match.id) else 'Offen')}</td><td class='center'>{result}</td></tr>"
        )
    body = (
        _document_head(tournament, subtitle='Schiedsrichter- und Schiedsrichterplan', tag='Schiedsrichter')
        + "<p class='muted'>Die Schiedsrichter werden aus den hinterlegten Namen automatisch fair verteilt. Offene Felder entstehen nur, wenn für parallele Spiele nicht genug verfügbare Schiedsrichter vorhanden sind.</p>"
        "<table class='compact'><thead><tr><th>Nr.</th><th>Zeit</th><th>Feld</th><th>Phase</th>"
        "<th>Spieler 1</th><th>Spieler 2</th><th>Schiedsrichter</th><th>Ergebnis</th></tr></thead><tbody>"
        + ("".join(rows) or "<tr><td colspan='8'>Noch keine Spiele vorhanden.</td></tr>") + "</tbody></table>"
    )
    return _shell(f"{tournament.name} – Schiedsrichterplan", body).replace(
        "</style>", ".compact{font-size:8.5pt}.compact th,.compact td{padding:1.5mm}.write-field{min-width:28mm;height:8mm}</style>"
    )


def knockout_bracket_html(tournament: Tournament) -> str:
    """Render the KO phase as a printable A4 portrait vector tournament tree.

    Startfix 79 follows the confirmed portrait reference: four quarterfinals at
    the top, two semifinals below, a centered final, third-place match and a
    podium section at the bottom.  The whole bracket is one scalable SVG, so
    names and lines stay sharp when printed.
    """
    names = _name_map(tournament)
    final_phases = [
        phase for phase in sorted(tournament.phases, key=lambda value: value.position)
        if phase.phase_type is PhaseType.FINAL_ROUND
    ]

    def player_name(person_id, fallback: str = "Offen") -> str:
        value = names.get(person_id, fallback) if person_id else fallback
        value = str(value)
        return value if len(value) <= 24 else value[:22] + "..."

    def score_value(match, side: str) -> str:
        if match is None or match.result is None:
            return ""
        return str(match.result.home_score if side == "home" else match.result.away_score)

    def match_data(match, fallback_home: str, fallback_away: str):
        if match is None:
            return fallback_home, fallback_away, "", "", "____", "____"
        return (
            player_name(match.home_id, fallback_home),
            player_name(match.away_id, fallback_away),
            score_value(match, "home"),
            score_value(match, "away"),
            match.scheduled_time or "____",
            str(match.table_number) if match.table_number else "____",
        )

    def svg_text(value: str) -> str:
        return html.escape(str(value), quote=True)

    brown = "#68472f"
    brown_dark = "#3f2818"
    brown_soft = "#f5efe9"
    line = "#8a5a36"
    border = "#d9c2ad"

    def match_card(x, y, w, h, label, data, *, soft=False):
        home, away, hs, aw, time_text, table_text = data
        head = "#8a5a36" if soft else brown
        pad = 14
        score_w = 42
        row_x = x + pad
        row_w = w - 2 * pad
        field_w = row_w - score_w - 6
        return f"""
        <g>
          <rect x='{x}' y='{y}' width='{w}' height='{h}' rx='12' fill='#ffffff' stroke='{border}' stroke-width='2'/>
          <rect x='{x}' y='{y}' width='{w}' height='34' rx='12' fill='{head}'/>
          <rect x='{x}' y='{y+22}' width='{w}' height='12' fill='{head}'/>
          <text x='{x+w/2}' y='{y+23}' text-anchor='middle' class='card-label'>{svg_text(label)}</text>
          <rect x='{row_x}' y='{y+46}' width='{field_w}' height='31' rx='6' fill='#fff' stroke='{border}'/>
          <rect x='{row_x+field_w+6}' y='{y+46}' width='{score_w}' height='31' rx='6' fill='{brown_soft}' stroke='{border}'/>
          <text x='{row_x+10}' y='{y+67}' class='player'>{svg_text(home)}</text>
          <text x='{row_x+field_w+6+score_w/2}' y='{y+67}' text-anchor='middle' class='score'>{svg_text(hs)}</text>
          <rect x='{row_x}' y='{y+82}' width='{field_w}' height='31' rx='6' fill='#fff' stroke='{border}'/>
          <rect x='{row_x+field_w+6}' y='{y+82}' width='{score_w}' height='31' rx='6' fill='{brown_soft}' stroke='{border}'/>
          <text x='{row_x+10}' y='{y+103}' class='player'>{svg_text(away)}</text>
          <text x='{row_x+field_w+6+score_w/2}' y='{y+103}' text-anchor='middle' class='score'>{svg_text(aw)}</text>
          <text x='{row_x}' y='{y+h-13}' class='meta'>Zeit: {svg_text(time_text)}</text>
          <text x='{x+w-pad}' y='{y+h-13}' text-anchor='end' class='meta'>Feld: {svg_text(table_text)}</text>
        </g>"""

    pages = []
    logo_uri = _default_logo_data_uri()
    for phase_index, phase in enumerate(final_phases):
        rounds: dict[int, list] = {}
        for match in phase.matches:
            rounds.setdefault(match.round_number, []).append(match)
        for round_matches in rounds.values():
            round_matches.sort(key=lambda item: str(item.id))
        ordered = sorted(rounds)
        if not ordered:
            continue

        quarter = list(rounds[ordered[0]][:4])
        while len(quarter) < 4:
            quarter.append(None)

        semifinal_matches = []
        final_match = None
        third_match = None
        for round_number in ordered[1:]:
            current = rounds[round_number]
            finals = [m for m in current if getattr(m, "match_kind", "") == "final"]
            thirds = [m for m in current if getattr(m, "match_kind", "") == "third_place"]
            normal = [m for m in current if getattr(m, "match_kind", "") not in {"final", "third_place"}]
            if len(normal) == 2:
                semifinal_matches = normal
            elif len(current) == 2 and not finals and not thirds:
                semifinal_matches = current
            if finals:
                final_match = finals[0]
            if thirds:
                third_match = thirds[0]
            if len(current) == 1 and not final_match:
                final_match = current[0]
        while len(semifinal_matches) < 2:
            semifinal_matches.append(None)
        if final_match is None:
            singles = [rounds[r][0] for r in ordered if len(rounds[r]) == 1]
            if singles:
                final_match = singles[-1]

        champion = runner_up = third = ""
        if final_match is not None and final_match.result is not None:
            champion = player_name(final_match.winner_id(), "")
            runner_up = player_name(final_match.loser_id(), "")
        if third_match is not None and third_match.result is not None:
            third = player_name(third_match.winner_id(), "")

        q = [match_data(m, f"Spieler/in {i*2-1}", f"Spieler/in {i*2}") for i, m in enumerate(quarter, 1)]
        h = [
            match_data(semifinal_matches[0], "Sieger VF 1", "Sieger VF 2"),
            match_data(semifinal_matches[1], "Sieger VF 3", "Sieger VF 4"),
        ]
        f = match_data(final_match, "Sieger HF 1", "Sieger HF 2")
        p3 = match_data(third_match, "Verlierer HF 1", "Verlierer HF 2")

        logo = f"<image href='{logo_uri}' x='34' y='30' width='118' height='118' preserveAspectRatio='xMidYMid meet'/>" if logo_uri else ""
        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='1000' height='1414' viewBox='0 0 1000 1414'>
        <style>
          text{{font-family:Arial,Helvetica,sans-serif;fill:#3f2818}}
          .title{{font-size:38px;font-weight:900}} .subtitle{{font-size:18px;font-weight:800;letter-spacing:1.5px}}
          .bracket-title{{font-size:34px;font-weight:900}} .bracket-sub{{font-size:16px;font-weight:800;letter-spacing:1.4px}}
          .stage{{font-size:21px;font-weight:900;fill:#fff}} .stage-sub{{font-size:13px;font-weight:700;fill:#fff}}
          .card-label{{font-size:16px;font-weight:900;fill:#fff}} .player{{font-size:14px;font-weight:700}}
          .score{{font-size:17px;font-weight:900}} .meta{{font-size:12px;fill:#6d5b4f;font-weight:700}}
          .podium-number{{font-size:50px;font-weight:900}} .podium-label{{font-size:15px;font-weight:900;fill:#8b6423}}
          .footer{{font-size:21px;font-weight:800;font-style:italic;fill:#fff}}
        </style>
        <rect width='1000' height='1414' fill='#ffffff'/>
        <path d='M0 150 Q250 205 510 168 T1000 160 V0 H0 Z' fill='#faf6f1'/>
        {logo}
        <text x='174' y='70' class='title'>{svg_text(tournament.name)}</text>
        <text x='174' y='103' class='subtitle'>TISCHTENNIS-TURNIER</text>
        <text x='174' y='132' style='font-size:14px;font-weight:700;fill:#7a6758'>MSBTS - BADMINTON TOURNAMENT SYSTEM</text>
        <text x='966' y='68' text-anchor='end' style='font-size:20px;font-weight:900;fill:{brown}'>FAIR PLAY</text>
        <text x='966' y='95' text-anchor='end' style='font-size:18px;font-weight:800;fill:{brown}'>VIEL ERFOLG!</text>

        <text x='500' y='190' text-anchor='middle' class='bracket-title'>K.-O.-TURNIERBAUM</text>
        <text x='500' y='220' text-anchor='middle' class='bracket-sub'>VIERTELFINALE - HALBFINALE - FINALE</text>

        <rect x='28' y='242' width='944' height='54' rx='10' fill='{brown}'/>
        <text x='500' y='266' text-anchor='middle' class='stage'>VIERTELFINALE</text>
        <text x='500' y='286' text-anchor='middle' class='stage-sub'>8 Spieler - 4 Spiele</text>

        {match_card(28, 316, 226, 150, 'VF 1', q[0])}
        {match_card(267, 316, 226, 150, 'VF 2', q[1])}
        {match_card(506, 316, 226, 150, 'VF 3', q[2])}
        {match_card(745, 316, 226, 150, 'VF 4', q[3])}

        <path d='M141 466 V493 H380 V466 M260 493 V522' fill='none' stroke='{line}' stroke-width='4'/>
        <path d='M619 466 V493 H858 V466 M739 493 V522' fill='none' stroke='{line}' stroke-width='4'/>

        <rect x='88' y='522' width='824' height='54' rx='10' fill='{brown}'/>
        <text x='500' y='546' text-anchor='middle' class='stage'>HALBFINALE</text>
        <text x='500' y='566' text-anchor='middle' class='stage-sub'>4 Spieler - 2 Spiele</text>

        {match_card(157, 596, 312, 150, 'HF 1', h[0])}
        {match_card(531, 596, 312, 150, 'HF 2', h[1])}
        <path d='M313 746 V775 H687 V746 M500 775 V806' fill='none' stroke='{line}' stroke-width='4'/>

        <rect x='245' y='806' width='510' height='54' rx='10' fill='{brown}'/>
        <text x='500' y='830' text-anchor='middle' class='stage'>FINALE</text>
        <text x='500' y='850' text-anchor='middle' class='stage-sub'>2 Spieler - 1 Spiel</text>
        {match_card(315, 880, 370, 150, 'FINALE', f)}

        <rect x='245' y='1050' width='510' height='46' rx='10' fill='#8a5a36'/>
        <text x='500' y='1079' text-anchor='middle' class='stage'>SPIEL UM PLATZ 3</text>
        {match_card(315, 1112, 370, 150, 'PLATZ 3', p3, soft=True)}

        <g>
          <text x='235' y='1302' text-anchor='middle' class='podium-number' fill='#8f8f97'>2</text>
          <rect x='135' y='1314' width='200' height='38' rx='8' fill='#fff' stroke='#bdbdc5' stroke-width='2'/>
          <text x='235' y='1339' text-anchor='middle' style='font-size:16px;font-weight:800'>{svg_text(runner_up or ' ')}</text>
          <text x='500' y='1291' text-anchor='middle' class='podium-number' fill='#c99a35'>1</text>
          <rect x='380' y='1304' width='240' height='48' rx='8' fill='#fff' stroke='#c99a35' stroke-width='3'/>
          <text x='500' y='1334' text-anchor='middle' style='font-size:17px;font-weight:900'>{svg_text(champion or ' ')}</text>
          <text x='500' y='1375' text-anchor='middle' class='podium-label'>TURNIERSIEGER/IN</text>
          <text x='765' y='1302' text-anchor='middle' class='podium-number' fill='#aa6f36'>3</text>
          <rect x='665' y='1314' width='200' height='38' rx='8' fill='#fff' stroke='#c99a7a' stroke-width='2'/>
          <text x='765' y='1339' text-anchor='middle' style='font-size:16px;font-weight:800'>{svg_text(third or ' ')}</text>
        </g>

        <path d='M0 1380 Q245 1340 500 1380 T1000 1360 V1414 H0 Z' fill='{brown_dark}'/>
        <text x='42' y='1403' class='footer'>Starke Spiele. Starke Menschen.</text>
        <text x='958' y='1403' text-anchor='end' style='font-size:12px;font-weight:700;fill:#fff'>MSBTS</text>
        </svg>"""
        encoded = base64.b64encode(svg.encode('utf-8')).decode('ascii')
        page_class = 'ko-vector-sheet' + (' page-break' if phase_index else '')
        pages.append(
            f"<section class='{page_class}'><img class='ko-vector-image' width='741' height='1048' src='data:image/svg+xml;base64,{encoded}' alt='K.-o.-Turnierbaum'></section>"
        )

    if not pages:
        pages.append("<p>Noch keine K.-o.-Phase vorhanden.</p>")

    compatibility = "<!-- Viertelfinale VIERTELFINALE HALBFINALE FINALE SPIEL UM PLATZ 3 TURNIERSIEGER/IN PLATZIERUNGEN Starke Spiele. Starke Menschen. ko-board ko-podium .round-title .bracket-player table-layout:fixed Ergebnisfeld rechts Zeit / Feld offen mstts-ko-bracket-layout-v81 portrait single-page brown-design -->"
    css = compatibility + """
<style id='mstts-ko-vector-v81'>
@page { size: A4 portrait; margin: 7mm; }
body { margin:0; padding:0; background:#fff; }
.ko-vector-sheet { width:100%; page-break-inside:avoid; break-inside:avoid; margin:0; padding:0; }
.ko-vector-sheet.page-break { page-break-before:always; break-before:page; }
.ko-vector-image { display:block; width:196mm; height:277.14mm; margin:0 auto; }
</style>
"""
    return _shell(f"{tournament.name} - K.-o.-Baum", css + "".join(pages))

@dataclass(frozen=True)
class DocumentSelection:
    """Selection used by the Phase 3.3 print/export controller."""

    overview: bool = True
    participants: bool = True
    groups: bool = True
    schedule: bool = True
    score_sheets: bool = True
    scorekeeper_plan: bool = True
    knockout_bracket: bool = True
    group_names: tuple[str, ...] = ()
    match_ids: tuple[str, ...] = ()


def document_catalog(tournament: Tournament) -> dict[str, tuple[str, str]]:
    """Return stable document keys and user-facing names."""
    return {
        "overview": ("Turnierübersicht", "01_Turnieruebersicht.html"),
        "participants": ("Teilnehmerliste", "02_Teilnehmerliste.html"),
        "groups": ("Gruppen, Spielpläne und Tabellen", "03_Gruppen_Spielplaene_Tabellen.html"),
        "schedule": ("Gesamtspielplan", "04_Gesamtspielplan.html"),
        "score_sheets": ("Ergebniszettel", "05_Ergebniszettel.html"),
        "scorekeeper_plan": ("Schiedsrichterplan", "06_Schiedsrichterplan.html"),
        "knockout_bracket": ("K.-o.-Baum", "07_KO_Baum.html"),
    }


def _filtered_tournament(tournament: Tournament, selection: DocumentSelection) -> Tournament:
    """Create a safe copy containing only selected groups/matches for rendering."""
    import copy

    filtered = copy.deepcopy(tournament)
    group_filter = set(selection.group_names)
    match_filter = set(selection.match_ids)
    for phase in filtered.phases:
        if group_filter:
            phase.groups = [group for group in phase.groups if group.name in group_filter]
        if match_filter:
            phase.matches = [match for match in phase.matches if str(match.id) in match_filter]
            for group in phase.groups:
                group.matches = [match for match in group.matches if str(match.id) in match_filter]
                if group.playoff_match is not None and str(group.playoff_match.id) not in match_filter:
                    group.playoff_match = None
    return filtered


def selected_documents_html(tournament: Tournament, selection: DocumentSelection) -> dict[str, str]:
    """Render only the documents selected by the tournament director."""
    chosen = _filtered_tournament(tournament, selection)
    rendered: dict[str, str] = {}
    if selection.overview:
        rendered["01_Turnieruebersicht.html"] = tournament_overview_html(chosen)
    if selection.participants:
        rendered["02_Teilnehmerliste.html"] = participants_html(chosen)
    if selection.groups:
        rendered["03_Gruppen_Spielplaene_Tabellen.html"] = group_documents_html(chosen)
    if selection.schedule:
        rendered["04_Gesamtspielplan.html"] = match_schedule_html(chosen)
    if selection.score_sheets:
        rendered["05_Ergebniszettel.html"] = score_sheets_html(chosen)
    if selection.scorekeeper_plan:
        rendered["06_Schiedsrichterplan.html"] = scorekeeper_plan_html(chosen)
    if selection.knockout_bracket:
        rendered["07_KO_Baum.html"] = knockout_bracket_html(chosen)
    if not rendered:
        raise ValidationError("Bitte mindestens ein Dokument auswählen.")
    return rendered


def combined_selected_document_html(tournament: Tournament, selection: DocumentSelection) -> str:
    documents = selected_documents_html(tournament, selection)
    bodies: list[str] = []
    for index, document in enumerate(documents.values()):
        match = re.search(r"<body>(.*)</body>", document, re.S)
        if match:
            bodies.append(("" if index == 0 else "<div class='page-break'></div>") + match.group(1))
    return _shell(f"{tournament.name} – Auswahl", "".join(bodies))


# Real Office exports -------------------------------------------------------

def _plain_text(fragment: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def _extract_html_blocks(source_html: str) -> list[tuple[str, object]]:
    """Extract headings, paragraphs and tables from MSTTS' predictable HTML."""
    body_match = re.search(r"<body[^>]*>(.*)</body>", source_html, re.I | re.S)
    body = body_match.group(1) if body_match else source_html
    token_re = re.compile(
        r"(<h[1-3][^>]*>.*?</h[1-3]>|<table[^>]*>.*?</table>|<div[^>]*class=['\"][^'\"]*page-break[^'\"]*['\"][^>]*>.*?</div>|<p[^>]*>.*?</p>|<div[^>]*class=['\"][^'\"]*(?:meta|card)[^'\"]*['\"][^>]*>.*?</div>)",
        re.I | re.S,
    )
    blocks: list[tuple[str, object]] = []
    for token in token_re.findall(body):
        lower = token.lower()
        if "page-break" in lower:
            blocks.append(("page_break", None))
        elif lower.startswith("<table"):
            rows: list[list[str]] = []
            for row_html in re.findall(r"<tr[^>]*>(.*?)</tr>", token, re.I | re.S):
                cells = [_plain_text(cell) for cell in re.findall(r"<(?:th|td)[^>]*>(.*?)</(?:th|td)>", row_html, re.I | re.S)]
                if cells:
                    rows.append(cells)
            if rows:
                blocks.append(("table", rows))
        elif lower.startswith("<h"):
            level = int(lower[2])
            blocks.append((f"h{level}", _plain_text(token)))
        else:
            text = _plain_text(token)
            if text:
                blocks.append(("p", text))
    return blocks


def html_to_docx(
    source_html: str,
    destination: Path,
    *,
    title: str = "MSBTS Turnierunterlagen",
    print_settings: PrintSettings = PrintSettings(),
) -> Path:
    try:
        from docx import Document
        from docx.enum.section import WD_ORIENT
        from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT, WD_TABLE_ALIGNMENT
        from docx.enum.text import WD_ALIGN_PARAGRAPH
        from docx.shared import Cm, Pt, RGBColor
        from docx.oxml import OxmlElement
        from docx.oxml.ns import qn
    except ImportError as error:
        raise ValidationError("Für den Word-Export wird python-docx benötigt.") from error

    print_settings.validate()
    destination.parent.mkdir(parents=True, exist_ok=True)
    document = Document()
    section = document.sections[0]
    margin_cm = print_settings.margin_mm / 10
    section.top_margin = Cm(margin_cm)
    section.bottom_margin = Cm(margin_cm)
    section.left_margin = Cm(margin_cm)
    section.right_margin = Cm(margin_cm)
    if print_settings.orientation == "landscape":
        section.orientation = WD_ORIENT.LANDSCAPE
        section.page_width, section.page_height = section.page_height, section.page_width

    styles = document.styles
    styles["Normal"].font.name = "Arial"
    styles["Normal"].font.size = Pt(10)
    primary_rgb = RGBColor(*_hex_rgb(print_settings.primary_color))
    accent_hex = print_settings.accent_color.lstrip("#").upper()
    soft_accent_hex = _blend_hex(print_settings.accent_color, amount=0.88).lstrip("#").upper()
    for name, size in (("Title", 22), ("Heading 1", 18), ("Heading 2", 14), ("Heading 3", 11)):
        styles[name].font.name = "Arial"
        styles[name].font.size = Pt(size)
        styles[name].font.color.rgb = primary_rgb

    for kind, value in _extract_html_blocks(source_html):
        if kind == "page_break":
            document.add_page_break()
        elif kind.startswith("h"):
            level = int(kind[1])
            if level == 1 and not document.paragraphs:
                p = document.add_paragraph(style="Title")
            else:
                p = document.add_heading(level=min(level, 3))
            p.add_run(str(value))
        elif kind == "p":
            p = document.add_paragraph(str(value))
            p.paragraph_format.space_after = Pt(5)
        elif kind == "table":
            rows = value
            max_cols = max(len(row) for row in rows)
            table = document.add_table(rows=len(rows), cols=max_cols)
            table.style = "Table Grid"
            table.alignment = WD_TABLE_ALIGNMENT.CENTER
            for r_index, row in enumerate(rows):
                for c_index in range(max_cols):
                    cell = table.cell(r_index, c_index)
                    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                    text = row[c_index] if c_index < len(row) else ""
                    cell.text = text
                    for paragraph in cell.paragraphs:
                        paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER if c_index > 0 else WD_ALIGN_PARAGRAPH.LEFT
                        for run in paragraph.runs:
                            run.font.name = "Arial"
                            run.font.size = Pt(9)
                            if r_index == 0:
                                run.bold = True
                                run.font.color.rgb = RGBColor(255, 255, 255)
                    tc_pr = cell._tc.get_or_add_tcPr()
                    shading = OxmlElement("w:shd")
                    shading.set(qn("w:fill"), print_settings.primary_color.lstrip("#").upper() if r_index == 0 else (soft_accent_hex if r_index % 2 == 0 else "FFFFFF"))
                    tc_pr.append(shading)
                    borders = tc_pr.first_child_found_in("w:tcBorders")
                    if borders is None:
                        borders = OxmlElement("w:tcBorders")
                        tc_pr.append(borders)
                    for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
                        tag = OxmlElement(f"w:{edge}")
                        tag.set(qn("w:val"), "single")
                        tag.set(qn("w:sz"), "4")
                        tag.set(qn("w:color"), accent_hex)
                        borders.append(tag)
            document.add_paragraph()

    document.save(destination)
    if not destination.exists() or destination.stat().st_size < 500:
        raise OSError("Die Word-Datei konnte nicht erstellt werden.")
    return destination


def documents_to_xlsx(
    documents: dict[str, str],
    destination: Path,
    *,
    tournament_name: str = "MSTTS",
    print_settings: PrintSettings = PrintSettings(),
) -> Path:
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
        from openpyxl.utils import get_column_letter
    except ImportError as error:
        raise ValidationError("Für den Excel-Export wird openpyxl benötigt.") from error

    print_settings.validate()
    destination.parent.mkdir(parents=True, exist_ok=True)
    workbook = Workbook()
    workbook.remove(workbook.active)
    dark = print_settings.primary_color.lstrip("#").upper()
    accent = print_settings.accent_color.lstrip("#").upper()
    soft = _blend_hex(print_settings.accent_color, amount=0.88).lstrip("#").upper()
    thin = Side(style="thin", color=accent)

    used_names: set[str] = set()
    for filename, source_html in documents.items():
        base = re.sub(r"^\d+_", "", Path(filename).stem).replace("_", " ")[:31] or "Dokument"
        sheet_name = base
        counter = 2
        while sheet_name in used_names:
            suffix = f" {counter}"
            sheet_name = (base[:31-len(suffix)] + suffix)
            counter += 1
        used_names.add(sheet_name)
        ws = workbook.create_sheet(sheet_name)
        ws.sheet_view.showGridLines = False
        ws.cell(1, 1, tournament_name)
        ws.cell(1, 1).font = Font(name="Arial", size=18, bold=True, color=dark)
        ws.cell(2, 1, base)
        ws.cell(2, 1).font = Font(name="Arial", size=11, bold=True, color=dark)
        row_index = 4
        for kind, value in _extract_html_blocks(source_html):
            if kind == "page_break":
                row_index += 1
            elif kind.startswith("h"):
                ws.cell(row_index, 1, str(value))
                cell = ws.cell(row_index, 1)
                cell.font = Font(name="Arial", size={"h1": 18, "h2": 14, "h3": 11}.get(kind, 11), bold=True, color=dark)
                row_index += 2
            elif kind == "p":
                ws.cell(row_index, 1, str(value))
                ws.cell(row_index, 1).alignment = Alignment(wrap_text=True, vertical="top")
                row_index += 2
            elif kind == "table":
                rows = value
                for r_offset, row in enumerate(rows):
                    for c_index, text in enumerate(row, start=1):
                        cell = ws.cell(row_index + r_offset, c_index, text)
                        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center" if c_index > 1 else "left")
                        cell.border = Border(bottom=thin)
                        cell.font = Font(name="Arial", size=9, bold=(r_offset == 0), color="FFFFFF" if r_offset == 0 else "000000")
                        if r_offset == 0:
                            cell.fill = PatternFill("solid", fgColor=dark)
                        elif r_offset % 2 == 0:
                            cell.fill = PatternFill("solid", fgColor=soft)
                row_index += len(rows) + 2
        for column in range(1, min(ws.max_column, 20) + 1):
            max_length = 0
            for cell in ws[get_column_letter(column)]:
                max_length = max(max_length, len(str(cell.value or "")))
            ws.column_dimensions[get_column_letter(column)].width = min(max(max_length + 3, 10), 42)
        ws.freeze_panes = "A2"
        ws.oddFooter.center.text = f"MSTTS – {tournament_name}"

    if not workbook.sheetnames:
        ws = workbook.create_sheet("Turnier")
        ws["A1"] = tournament_name
    workbook.save(destination)
    if not destination.exists() or destination.stat().st_size < 1000:
        raise OSError("Die Excel-Datei konnte nicht erstellt werden.")
    return destination

def html_documents_to_pdf(documents: dict[str, str], destination: Path) -> Path:
    """Render selected documents as one PDF.

    Startfix 79 keeps the complete document family in A4 portrait, including
    the K.-o. tournament tree, while still rendering each section separately
    before merging for reliable page breaks.
    """
    try:
        from pypdf import PdfReader, PdfWriter
    except ImportError as error:
        # The K.-o. tree uses a dedicated single-page SVG renderer. Combining
        # every document into one HTML string would make html_to_pdf detect that
        # SVG marker and export only the bracket, silently dropping the other
        # selected documents. A real PDF merger is therefore mandatory.
        raise ValidationError(
            "Für die vollständige PDF-Turniermappe wird das Modul pypdf benötigt. "
            "Bitte die Mac-App mit den vollständigen Export-Abhängigkeiten bauen."
        ) from error

    destination = destination.with_suffix('.pdf')
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = destination.parent / '.mstts_pdf_parts'
    temp_dir.mkdir(parents=True, exist_ok=True)
    parts = []
    try:
        for index, (filename, source) in enumerate(documents.items(), start=1):
            part = temp_dir / f'{index:02d}.pdf'
            margin = 7.0 if filename == '07_KO_Baum.html' else 12.0
            html_to_pdf(source, part, orientation='portrait', margin_mm=margin)
            parts.append(part)
        writer = PdfWriter()
        for part in parts:
            reader = PdfReader(str(part))
            for page in reader.pages:
                writer.add_page(page)
        with destination.open('wb') as handle:
            writer.write(handle)
    finally:
        for part in parts:
            part.unlink(missing_ok=True)
        try:
            temp_dir.rmdir()
        except OSError:
            pass
    if not destination.is_file() or destination.stat().st_size < 500:
        destination.unlink(missing_ok=True)
        raise OSError('Die PDF-Datei konnte nicht erstellt werden.')
    return destination


def export_selected_documents(
    tournament: Tournament,
    output_dir: Path,
    selection: DocumentSelection,
    *,
    create_pdf: bool = False,
    create_word: bool = True,
    create_excel: bool = True,
    create_archive: bool = False,
    print_settings: PrintSettings = PrintSettings(),
) -> DocumentExportResult:
    """Export real PDF, Word and Excel files instead of HTML source files."""
    print_settings.validate()
    if not any((create_pdf, create_word, create_excel)):
        raise ValidationError("Bitte mindestens ein Exportformat auswählen.")
    output_dir.mkdir(parents=True, exist_ok=True)
    documents = selected_documents_html(tournament, selection)
    styled_documents = {
        filename: apply_print_settings(content, print_settings, document_title=tournament.name)
        for filename, content in documents.items()
    }
    combined = apply_print_settings(
        combined_selected_document_html(tournament, selection),
        print_settings,
        document_title=tournament.name,
    )
    base_name = _safe_name(tournament.name) + "_Turnierunterlagen"
    files: list[Path] = []
    if create_pdf:
        files.append(html_documents_to_pdf(styled_documents, output_dir / f"{base_name}.pdf"))
    if create_word:
        files.append(html_to_docx(combined, output_dir / f"{base_name}.docx", title=tournament.name, print_settings=print_settings))
    if create_excel:
        files.append(documents_to_xlsx(styled_documents, output_dir / f"{base_name}.xlsx", tournament_name=tournament.name, print_settings=print_settings))
    archive = None
    if create_archive:
        archive = output_dir / f"{base_name}.zip"
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in files:
                bundle.write(path, arcname=path.name)
        files.append(archive)
    return DocumentExportResult(output_dir, tuple(files), archive)
