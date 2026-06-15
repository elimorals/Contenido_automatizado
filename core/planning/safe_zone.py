"""9:16 safe-zone constants.

Canvas is 1080×1920 (vertical reel standard). Native TikTok / Reels UI
consumes:
  • bottom ~480 px  — caption / handle / CTA / music ticker
  • right  ~120-140 px — action bar

Burned text must stay inside the safe stage.
"""

from __future__ import annotations

CANVAS_W: int = 1080
CANVAS_H: int = 1920

# Bottom obstructed by native UI — never burn text here.
BOTTOM_UI_PX: int = 480

# Right obstructed by action bar.
RIGHT_UI_PX: int = 140

# Safe stage (the rectangle text can occupy).
SAFE_LEFT: int = 60
SAFE_RIGHT: int = CANVAS_W - RIGHT_UI_PX
SAFE_TOP: int = 200
SAFE_BOTTOM: int = CANVAS_H - BOTTOM_UI_PX
SAFE_W: int = SAFE_RIGHT - SAFE_LEFT          # ~880
SAFE_H: int = SAFE_BOTTOM - SAFE_TOP          # ~1240


# ───── Layer 1 — verbatim subtitles position ──────────────────────────
# Upper-center: subtitles sit above the face/center of any talking-head.
# Y is measured from the top of the canvas.

SUBTITLE_Y_PCT: float = 0.35    # ~672 px from top of 1920
SUBTITLE_X_PCT: float = 0.50    # center horizontal
SUBTITLE_FONT_PX: int = 80      # Montserrat Bold size used by font_metrics


# ───── Layer 2 — accent overlay position ──────────────────────────────
# Lower-third by default — opposite end from subtitles so they don't stack.
# Avoid the bottom 25% (UI zone).

ACCENT_LOWER_Y_PCT: float = 0.62   # ~1190 px from top — safely above UI
ACCENT_UPPER_Y_PCT: float = 0.22   # ~423 px from top — for hook title cards
ACCENT_X_PCT: float = 0.50
ACCENT_FONT_PX: int = 110          # 1.4× subtitle size — accent should be louder


# ───── Colors ──────────────────────────────────────────────────────────

SUBTITLE_FILL: str = "white"
SUBTITLE_STROKE: str = "black"
SUBTITLE_STROKE_PX: int = 5
SUBTITLE_HIGHLIGHT: str = "yellow"    # active-word color
ACCENT_FILL: str = "white"
ACCENT_STROKE: str = "black"
ACCENT_STROKE_PX: int = 6
