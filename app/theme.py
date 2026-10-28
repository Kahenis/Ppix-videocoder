"""Couleurs communes (thème PPIX)."""
ACCENT = "#E5A00D"
ACCENT_HOVER = "#C48A0B"
BG = "#161616"
PANEL = "#1E1E1E"
CARD = "#262626"
TEXT = "#F2F2F2"
MUTED = "#A0A0A0"
OK = "#3DDC84"
OK_DARK = "#2E7D4F"
WARN = "#FFB020"
ERR = "#f87171"
INFO = "#60a5fa"
DANGER = "#5C1A1A"
DANGER_HOVER = "#8A3618"

STATUS_LABELS = {
    "pending": "en attente",
    "running": "en cours",
    "done": "à valider",
    "simulated": "simulé",
    "replaced": "remplacé",
    "kept": "gardé (2 fichiers)",
    "discarded": "écarté",
    "error": "erreur",
    "cancelled": "annulé",
}
STATUS_COLORS = {
    "pending": MUTED, "running": INFO, "done": ACCENT, "simulated": MUTED,
    "replaced": OK, "kept": OK, "discarded": WARN, "error": ERR, "cancelled": WARN,
}
