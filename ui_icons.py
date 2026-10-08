"""Inline SVG icon set — one stroke style everywhere (24px grid, 1.75 stroke).

Templates call icon("name"); the markup inherits color through currentColor.
"""
from markupsafe import Markup

_CAL = '<rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 10h18"/>'

ICON_PATHS = {
    "dashboard":  '<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/>'
                  '<rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>',
    "home":       '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M10 21v-6h4v6"/>',
    "chart":      '<path d="M3 21h18"/><rect x="5" y="11" width="3" height="7" rx=".5"/>'
                  '<rect x="10.5" y="6" width="3" height="12" rx=".5"/><rect x="16" y="14" width="3" height="4" rx=".5"/>',
    "trend":      '<path d="M3 17l6-6 4 4 8-8"/><path d="M15 7h6v6"/>',
    "list":       '<path d="M9 6h12M9 12h12M9 18h12"/><path d="M4.5 6h.01M4.5 12h.01M4.5 18h.01"/>',
    "lock":       '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "calendar":   _CAL,
    "schedule":   _CAL + '<path d="M7.5 14h3M13.5 14h3M7.5 17.5h3"/>',
    "branch":     '<path d="M6 21c4-4 6-9 6-18"/><path d="M12 8c2.5-2.5 5.5-3 8-2-.8 3-4 5-8 4"/>'
                  '<path d="M10.5 14c-2.5-2-5.5-2.5-7-1.5 1 2.8 4 4 7 3"/>',
    "document":   '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5M9 13h6M9 17h6"/>',
    "cap":        '<path d="M2 9l10-5 10 5-10 5z"/><path d="M6 11v5c0 1.5 3 3 6 3s6-1.5 6-3v-5"/><path d="M22 9v6"/>',
    "chat":       '<path d="M21 12a8 8 0 0 1-11.6 7.1L4 20.5l1.4-4.6A8 8 0 1 1 21 12z"/>',
    "megaphone":  '<path d="M3 10v4a1 1 0 0 0 1 1h3l7 4V5L7 9H4a1 1 0 0 0-1 1z"/><path d="M18 8.5a5 5 0 0 1 0 7"/>',
    "person":     '<circle cx="12" cy="8" r="4"/><path d="M4 21a8 8 0 0 1 16 0"/>',
    "scale":      '<path d="M12 3v18M7 21h10M5 7h14"/><path d="M5 7l-3 7a3 3 0 0 0 6 0z"/><path d="M19 7l-3 7a3 3 0 0 0 6 0z"/>',
    "clipboard":  '<path d="M9 4H7a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2h-2"/>'
                  '<rect x="9" y="2.5" width="6" height="3.5" rx="1"/><path d="M9 12h6M9 16h4"/>',
    "building":   '<rect x="4" y="3" width="11" height="18" rx="1"/><path d="M15 9h4a1 1 0 0 1 1 1v11h-5"/>'
                  '<path d="M8 7h3M8 11h3M8 15h3M2 21h20"/>',
    "people":     '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20a6.5 6.5 0 0 1 13 0"/>'
                  '<path d="M16 4.5a3.5 3.5 0 0 1 0 7"/><path d="M18 14a6 6 0 0 1 3.5 6"/>',
    "clock":      '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "user-plus":  '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M19 8v6M16 11h6"/>',
    "star":       '<path d="M12 3l2.8 5.7 6.2.9-4.5 4.4 1 6.2L12 17.3l-5.5 2.9 1-6.2L3 9.6l6.2-.9z"/>',
    "wallet":     '<path d="M20 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2v-2"/>'
                  '<path d="M21 7h-6a3 3 0 0 0 0 6h6z"/>',
    "bank":       '<path d="M3 10l9-6 9 6"/><path d="M5 10v8M9.5 10v8M14.5 10v8M19 10v8M3 21h18"/>',
    "card":       '<rect x="2.5" y="5" width="19" height="14" rx="2"/><path d="M2.5 10h19M6 15h4"/>',
    "gear":       '<circle cx="12" cy="12" r="3"/>'
                  '<path d="M12 2.5l1.6 2.6 3-.6.6 3 2.6 1.6-1.3 2.9 1.3 2.9-2.6 1.6-.6 3-3-.6L12 21.5l-1.6-2.6-3 .6-.6-3'
                  '-2.6-1.6L5.5 12 4.2 9.1l2.6-1.6.6-3 3 .6z"/>',
    "monitor":    '<rect x="2.5" y="4" width="19" height="13" rx="2"/><path d="M8 21h8M12 17v4"/>',
    "trash":      '<path d="M3 6h18M8 6V4h8v2M6 6l1 15h10l1-15M10 11v6M14 11v6"/>',
    "folder":     '<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>',
    "logout":     '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/>',
    "bell":       '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    "moon":       '<path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z"/>',
    "sun":        '<circle cx="12" cy="12" r="4"/>'
                  '<path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
    "pencil":     '<path d="M17 3l4 4L8 20H4v-4z"/>',
    "flag":       '<path d="M5 21V4M5 4h11l-2 4 2 4H5"/>',
    "pause":      '<path d="M9 5v14M15 5v14"/>',
    "paperclip":  '<path d="M21 11.5l-8.6 8.6a5 5 0 0 1-7.1-7.1l8.6-8.6a3.5 3.5 0 0 1 5 5l-8.6 8.6a2 2 0 0 1-2.8-2.8l7.9-7.9"/>',
    "smile":      '<circle cx="12" cy="12" r="9"/><path d="M8.5 14.5a4.5 4.5 0 0 0 7 0M9 9.5h.01M15 9.5h.01"/>',
    "gift":       '<rect x="3" y="8" width="18" height="4" rx="1"/><path d="M12 8v13M5 12v9h14v-9"/>'
                  '<path d="M12 8C10 4 6.5 4.5 7 7c.3 1.2 2.5 1 5 1zM12 8c2-4 5.5-3.5 5-1-.3 1.2-2.5 1-5 1z"/>',
    "award":      '<circle cx="12" cy="9" r="6"/><path d="M8.5 14 7 22l5-3 5 3-1.5-8"/>',
    "pin":        '<path d="M12 17v5M9 3h6l-1 5 3 3v2H7v-2l3-3z"/>',
    "inbox":      '<path d="M3 13h5l1.5 3h5l1.5-3h5"/><path d="M5.5 5h13L21 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-6z"/>',
    "check":      '<circle cx="12" cy="12" r="9"/><path d="M8 12.5l2.7 2.7L16 9.5"/>',
    "bolt":       '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    "search":     '<circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/>',
    "alert":      '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
    "undo":       '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "drive":      '<path d="M22 12H2M5.5 5h13L22 12v6a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-6z"/><path d="M6 16h.01M10 16h.01"/>',
    "book":       '<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M4 19.5A2.5 2.5 0 0 0 6.5 22H20v-5"/>',
    "circle":     '<circle cx="12" cy="12" r="8"/>',
}


def icon(name: str, size: int = 18, cls: str = "") -> Markup:
    paths = ICON_PATHS.get(name, ICON_PATHS["circle"])
    class_attr = f' class="{cls}"' if cls else ""
    return Markup(
        f'<svg{class_attr} viewBox="0 0 24 24" width="{size}" height="{size}" fill="none" stroke="currentColor" '
        f'stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false">'
        f'{paths}</svg>'
    )
