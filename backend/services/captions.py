from __future__ import annotations
import re
import pysubs2
from typing import Callable, List, Optional
from models import Segment, WordTiming


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# Punctuation that should never appear in the displayed caption (we keep it
# only as a signal for line-break detection, then strip it).
_DISPLAY_PUNCT_RE = re.compile(r"[^\w\s'’\-]", re.UNICODE)
# Characters that mark the end of a phrase / a natural line break.
_BREAK_PUNCT = set(",.;:!?…—–")
# Pause (seconds) between consecutive words that also forces a line break.
_PAUSE_GAP = 0.10
# Hard cap on words per caption line.
_MAX_WORDS_PER_LINE = 3
# Minimum time a single caption stays on screen (ms).
_MIN_DISPLAY_MS = 250


def _sanitize_libass(text: str) -> str:
    """Strip control chars and escape characters libass treats as syntax."""
    if not text:
        return ""
    text = _CONTROL_RE.sub("", text)
    text = text.replace("{", "❴").replace("}", "❵").replace("\\", "⧵")
    return text.strip()


def _strip_display_punct(text: str) -> str:
    """Remove punctuation, collapse whitespace, uppercase."""
    text = _DISPLAY_PUNCT_RE.sub("", text)
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text


def _group_words(
    words: list[WordTiming],
    max_words_per_line: int = _MAX_WORDS_PER_LINE,
    break_on_punct: bool = True,
    break_on_pause_s: float = _PAUSE_GAP,
) -> list[list[WordTiming]]:
    """Split a segment's words into caption lines.

    Break rules (any one triggers a new line):
      • Current word ends with sentence/phrase punctuation
        (only when `break_on_punct` is True).
      • Gap to the next word exceeds `break_on_pause_s` seconds
        (disabled when ≤0).
      • Current line already holds `max_words_per_line` words.

    All thresholds are user-tunable via the caption_style dict — see
    `_grouping_opts_from_style`. Defaults preserve the historical
    "max 3 words, break on , . ; : ! ? — pause ≥100 ms" behavior.
    """
    max_words = max(1, int(max_words_per_line))
    pause_thresh = float(break_on_pause_s) if break_on_pause_s and break_on_pause_s > 0 else None

    groups: list[list[WordTiming]] = []
    cur: list[WordTiming] = []
    for i, w in enumerate(words):
        cur.append(w)
        is_last = i == len(words) - 1
        ends_phrase = bool(break_on_punct) and bool(w.word) and w.word[-1] in _BREAK_PUNCT
        gap_too_big = (
            pause_thresh is not None
            and not is_last
            and (words[i + 1].start - w.end) >= pause_thresh
        )
        full = len(cur) >= max_words
        if ends_phrase or gap_too_big or full or is_last:
            groups.append(cur)
            cur = []
    return groups


def _grouping_opts_from_style(caption_style: Optional[dict]) -> tuple[int, int, bool, float]:
    """Pull the grouping tuneables out of a caption_style dict, applying
    sane defaults + bounds. Returns (max_words_per_line, max_lines_per_caption,
    break_on_punct, break_on_pause_s).

    `max_words_per_line` is no longer user-tuneable from the UI — the caption
    box width drives wrapping. Default to a very high number so width-wrap is
    the only horizontal constraint. Legacy manifests that still carry an
    explicit cap are honoured.
    """
    cs = caption_style or {}
    try:
        max_words = int(cs.get("max_words_per_line", 99))
    except (TypeError, ValueError):
        max_words = 99
    max_words = max(1, min(99, max_words))
    try:
        max_lines = int(cs.get("max_lines_per_caption", 2))
    except (TypeError, ValueError):
        max_lines = 2
    max_lines = max(1, min(3, max_lines))
    break_on_punct = bool(cs.get("break_on_punct", True))
    try:
        pause_ms = float(cs.get("break_on_pause_ms", int(_PAUSE_GAP * 1000)))
    except (TypeError, ValueError):
        pause_ms = _PAUSE_GAP * 1000
    pause_ms = max(0.0, min(2000.0, pause_ms))
    return max_words, max_lines, break_on_punct, pause_ms / 1000.0


def _hex_to_ass_color(hex_str: str, alpha: int = 0) -> pysubs2.Color:
    """Convert "#RRGGBB" / "RRGGBB" to a pysubs2.Color (alpha 0 = opaque)."""
    if not hex_str:
        return pysubs2.Color(255, 255, 255, alpha)
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return pysubs2.Color(255, 255, 255, alpha)
    try:
        r = int(h[0:2], 16)
        g = int(h[2:4], 16)
        b = int(h[4:6], 16)
    except ValueError:
        return pysubs2.Color(255, 255, 255, alpha)
    return pysubs2.Color(r, g, b, alpha)


def _hex_to_bgr(hex_str: str) -> str:
    """#RRGGBB → 'BBGGRR' uppercase, for inline ASS color override tags."""
    if not hex_str:
        return "FFFFFF"
    h = hex_str.lstrip("#")
    if len(h) != 6:
        return "FFFFFF"
    try:
        r = h[0:2]; g = h[2:4]; b = h[4:6]
        return (b + g + r).upper()
    except Exception:
        return "FFFFFF"


def _build_styled_subs(
    video_width: int,
    video_height: int,
    font_name: str,
    caption_box: Optional[dict],
    caption_style: Optional[dict],
) -> tuple[
    pysubs2.SSAFile,
    Callable[[int, int, List[str]], List[pysubs2.SSAEvent]],
    int,
    int,
]:
    """Construct an SSAFile pre-populated with the Default style and return
    a `make_events(start_ms, end_ms, lines)` callable for emitting events.

    The callable handles all per-line layout:
      • Each line of `lines` becomes its own Dialogue, positioned at a Y
        offset computed from `line_spacing × font_size`. This gives the
        user true line-height control instead of relying on the font's
        natural leading (which ASS exposes no direct knob for).
      • Letter spacing is applied via the inline `\\fsp` tag and the
        Style's `Spacing` field, both derived from `letter_spacing`
        (em-units of font size).
      • When the user asks for a soft drop shadow (`shadow_blur > 0`),
        each line emits a pair of events — Layer 0 with the offset+
        blurred shadow, Layer 1 with the sharp text on top — so the
        blur softens only the shadow, not the glyphs.

    Both `generate_ass_captions` (real render) and `generate_preview_ass`
    (live preview) build events through this helper — that's what keeps
    the preview byte-equivalent to the burned video.
    """
    box = caption_box or {}
    cx_pct = float(box.get("x", 50))
    cy_pct = float(box.get("y", 85))
    scale_pct = max(5.0, float(box.get("scale", 60)))
    rotation_deg = float(box.get("rotation", 0))

    cx = int(round(cx_pct / 100.0 * video_width))
    cy = int(round(cy_pct / 100.0 * video_height))
    box_width_px = scale_pct / 100.0 * video_width

    # Base font size used only for the WRAP calculation (how many chars
    # fit per line at this nominal size). Per-event rendering then sizes
    # each caption individually so the longest line in that caption fills
    # the box width — Captions-AI behaviour. See `make_events` below.
    #
    # The wrap base targets ~13 chars/line as a fallback for very wide
    # boxes. In practice the line-builder breaks much earlier on
    # punctuation + pauses, so most lines end up 1-3 short words and the
    # per-event auto-fit blows the font up to fill the box.
    TARGET_CHARS_PER_LINE = 13
    font_size = max(16, int(round(box_width_px * 0.96 / (TARGET_CHARS_PER_LINE * 0.62))))

    subs = pysubs2.SSAFile()
    subs.info["PlayResX"] = str(video_width)
    subs.info["PlayResY"] = str(video_height)
    # WrapStyle 2 = no automatic wrapping (we wrap ourselves).
    subs.info["WrapStyle"] = "2"
    subs.info["ScaledBorderAndShadow"] = "yes"

    # User-tunable style overrides (all optional). Defaults preserve the
    # original look (Rubik bold, white text, no stroke, soft drop shadow).
    cs = caption_style or {}
    style_font = cs.get("font_name") or font_name
    primary_hex = cs.get("primary_color") or "#FFFFFF"
    outline_hex = cs.get("outline_color") or "#000000"
    shadow_hex = cs.get("shadow_color") or "#000000"
    outline_w = float(cs.get("outline_width", 0.0))
    default_shadow = max(1.5, font_size * 0.035)
    shadow_w = float(cs.get("shadow", default_shadow))
    bold = bool(cs.get("bold", True))
    # Shadow opacity: 0..1 from user, ASS uses 0..255 inverted (0 opaque).
    shadow_alpha = int(round(max(0.0, min(1.0, float(cs.get("shadow_alpha", 0.88)))) * 255))
    shadow_alpha = 255 - shadow_alpha
    # Shadow softness (blur): 0..10. Applied per-event via \blur.
    shadow_blur = max(0.0, min(20.0, float(cs.get("shadow_blur", 0.0))))
    # Letter spacing in em-units of the font size (-0.1 to 0.5). Negative
    # tightens, positive opens. Resolution-independent: we multiply by
    # font_size to get the ASS pixel value.
    letter_spacing_em = max(-0.1, min(0.5, float(cs.get("letter_spacing", 0.0))))
    letter_spacing_px = letter_spacing_em * font_size
    # Line spacing as a multiplier of font_size. Default 1.2 ≈ a typical
    # book-style leading. Range 0.8 (tight) to 2.0 (airy).
    line_spacing_mul = max(0.8, min(2.0, float(cs.get("line_spacing", 1.2))))
    line_height_px = font_size * line_spacing_mul
    # Optional glow effect: simulated by widening the outline in the glow
    # color and applying a strong blur. Independent of the user-set stroke.
    glow_enabled = bool(cs.get("glow", False))
    glow_hex = cs.get("glow_color") or "#FFFFFF"
    glow_strength = max(0.0, min(20.0, float(cs.get("glow_strength", 0.0))))
    if glow_enabled and glow_strength > 0:
        # Override outline so it draws the glow halo. The blur is bumped up
        # so the halo feathers out instead of looking like a hard stroke.
        outline_hex = glow_hex
        outline_w = max(outline_w, glow_strength)
        shadow_blur = max(shadow_blur, glow_strength * 1.2)

    # Pop entrance animation toggle. Default ON to preserve current behavior;
    # the UI exposes this as a per-caption-style switch.
    pop_animation = bool(cs.get("pop_animation", True))

    # Soft shadow gets layered (see Layer 0 / Layer 1 below). When that's
    # active, the Style's built-in shadow is zeroed — the offset+blur live
    # on the layer-0 event instead.
    use_layered_shadow = shadow_blur > 0 and shadow_w > 0

    style = pysubs2.SSAStyle(
        fontname=style_font,
        fontsize=font_size,
        primarycolor=_hex_to_ass_color(primary_hex),
        secondarycolor=_hex_to_ass_color(primary_hex),
        outlinecolor=_hex_to_ass_color(outline_hex),
        backcolor=_hex_to_ass_color(shadow_hex, alpha=shadow_alpha),
        bold=bold,
        outline=outline_w,
        shadow=0 if use_layered_shadow else shadow_w,
        spacing=letter_spacing_px,
        borderstyle=1,             # 1 = outline + shadow (no opaque box)
        alignment=5,               # middle-center → \pos refers to text centre
        marginl=0, marginr=0, marginv=0,
    )
    subs.styles["Default"] = style

    # ASS rotation is counter-clockwise; CSS / preview is clockwise. Negate.
    ass_rotation = -rotation_deg
    rot_tag = "\\frz" + format(ass_rotation, ".2f")
    # \fsp duplicates the Style's `Spacing` for renderers that ignore it;
    # libass honours both, the second wins.
    fsp_tag = ("\\fsp" + format(letter_spacing_px, ".2f")) if abs(letter_spacing_px) >= 0.01 else ""
    shadow_bgr = _hex_to_bgr(shadow_hex)
    alpha_hex = format(shadow_alpha, "02X")

    # Estimate the widest text (in characters) that fits inside the box at the
    # chosen font size. Rubik Bold uppercase averages ~0.62 × fontsize per char.
    AVG_CHAR_WIDTH = font_size * 0.62 + max(0.0, letter_spacing_px)
    # Subtract a small inner padding so glyphs don't kiss the box edge.
    usable_width = max(1.0, box_width_px * 0.96)
    max_chars_per_line = max(1, int(usable_width // AVG_CHAR_WIDTH))

    # Minimal "pop" entrance: each line fades + scales from 92% to 100% over
    # POP_MS. When a caption has multiple lines, each subsequent line is
    # delayed by STAGGER_MS so they appear one after the other instead of
    # snapping in together. Kept short on purpose — captions should feel
    # responsive, not animated.
    POP_MS = 90
    STAGGER_MS = 70
    POP_START_SCALE = 92

    # Per-caption font ceiling — captions never blow up larger than 12 %
    # of the video's short side. Stops single-character captions from
    # producing absurd font sizes when the auto-fit math sees a tiny
    # `longest_chars`.
    FONT_CEILING_PX = max(40, int(round(min(video_width, video_height) * 0.12)))

    def make_events(start_ms: int, end_ms: int, lines: List[str]) -> List[pysubs2.SSAEvent]:
        """Emit shadow+text event pairs for each visible line, stacked
        vertically around (cx, cy). Each caption is rendered at an
        individually-sized font: the longest line is auto-fit to the
        caption box width so text always hugs the borders, regardless of
        whether the content is "IN" or "TRANSLATION"."""
        clean: list[str] = [_sanitize_libass(ln) for ln in lines if ln and ln.strip()]
        if not clean:
            return []
        n = len(clean)

        # Auto-fit font to fill the box width, based on the longest line in
        # THIS caption. `usable_width` and the per-char ratio match the wrap
        # calculation above; `letter_spacing_px` is included so tight/wide
        # tracking doesn't break the fit.
        longest_chars = max(len(ln) for ln in clean) or 1
        per_char_at_unit = 0.62 + max(0.0, letter_spacing_em)
        fit_font = int(round(usable_width / (longest_chars * per_char_at_unit)))
        event_font = max(font_size, min(FONT_CEILING_PX, fit_font))
        # Recompute line height from the dynamic font so multi-line stacks
        # stay properly centred and don't collide.
        event_line_height = event_font * line_spacing_mul
        first_offset = -(n - 1) * event_line_height / 2.0
        # Shadow offset scales with the dynamic font for visual consistency.
        event_shadow_w = shadow_w * (event_font / max(1, font_size))
        fs_tag = "\\fs" + str(event_font)

        events: List[pysubs2.SSAEvent] = []
        max_stagger = max(0, end_ms - start_ms - POP_MS) if pop_animation else 0
        for i, line in enumerate(clean):
            ly = int(round(cy + first_offset + i * event_line_height))
            # Per-line start offset, clamped so the last line can still finish
            # its pop before the caption disappears. When pop_animation is OFF
            # all lines share the caption's start (no stagger, no fad/pop).
            line_start = (
                start_ms + min(i * STAGGER_MS, max_stagger)
                if pop_animation else start_ms
            )
            fad_tag = "\\fad(" + str(POP_MS) + ",0)" if pop_animation else ""
            pop_tag = (
                "\\fscx" + str(POP_START_SCALE) + "\\fscy" + str(POP_START_SCALE)
                + "\\t(0," + str(POP_MS) + ",\\fscx100\\fscy100)"
            ) if pop_animation else ""
            if use_layered_shadow:
                sx = int(round(cx + event_shadow_w))
                sy = int(round(ly + event_shadow_w))
                shadow_text = (
                    "{\\q2\\an5"
                    + fs_tag
                    + "\\pos(" + str(sx) + "," + str(sy) + ")"
                    + rot_tag
                    + "\\bord0\\shad0"
                    + "\\blur" + format(shadow_blur, ".2f")
                    + fsp_tag
                    + fad_tag
                    + pop_tag
                    + "\\1c&H" + shadow_bgr + "&\\3c&H" + shadow_bgr + "&"
                    + "\\1a&H" + alpha_hex + "&\\3a&H" + alpha_hex + "&"
                    + "}"
                    + line
                )
                events.append(pysubs2.SSAEvent(
                    layer=0, start=line_start, end=end_ms,
                    text=shadow_text, style="Default",
                ))
            fg_text = (
                "{\\q2\\an5"
                + fs_tag
                + "\\pos(" + str(cx) + "," + str(ly) + ")"
                + rot_tag
                + fsp_tag
                + fad_tag
                + pop_tag
                + "}"
                + line
            )
            events.append(pysubs2.SSAEvent(
                layer=1 if use_layered_shadow else 0,
                start=line_start, end=end_ms,
                text=fg_text, style="Default",
            ))
        return events

    return subs, make_events, font_size, max_chars_per_line


def generate_ass_captions(
    segments: list[Segment],
    output_path: str,
    video_width: int = 1920,
    video_height: int = 1080,
    font_name: str = "Rubik",
    caption_box: Optional[dict] = None,
    caption_style: Optional[dict] = None,
) -> str:
    """Generate ASS subtitle file.

    Behaviour:
      • Font: Rubik (bold).
      • All caps display, punctuation hidden.
      • One caption line at a time (page-break per line).
      • Lines split on punctuation OR pauses, max 5 words per line.
      • Position / scale / rotation come from `caption_box`:
            { x: 0..100 (%), y: 0..100 (%), scale: 0..100 (% of width),
              rotation: degrees }
        When omitted, captions sit centered near the bottom.
    """
    subs, make_events, font_size, max_chars_per_line = _build_styled_subs(
        video_width, video_height, font_name, caption_box, caption_style
    )
    final_events = _build_caption_line_events(segments, max_chars_per_line, caption_style)
    for start_ms, end_ms, text in final_events:
        # The Captions-AI packer joins per-caption visual lines with
        # `\N`. Split before handing to make_events — it re-sanitises
        # each line and would otherwise turn `\N` into the REVERSE
        # SOLIDUS OPERATOR character `⧵` between every word.
        lines = [
            ln for ln in text.replace("\\N", "\n").split("\n") if ln.strip()
        ] or [text]
        subs.events.extend(make_events(start_ms, end_ms, lines))

    subs.save(output_path)
    return output_path


def _split_group_to_fit(
    group: list[WordTiming], max_chars_per_line: int
) -> list[list[WordTiming]]:
    """Greedy word-wrap so each sub-group's display text ≤ max_chars_per_line.

    Never scales the font down — overflowing groups become multiple
    sequential captions instead.
    """
    out: list[list[WordTiming]] = []
    cur: list[WordTiming] = []
    cur_len = 0
    for w in group:
        display = _strip_display_punct(w.word)
        if not display:
            # Punctuation-only token: keep it attached to current chunk timing-wise.
            cur.append(w)
            continue
        add_len = len(display) + (1 if cur_len else 0)
        if cur and cur_len + add_len > max_chars_per_line:
            out.append(cur)
            cur = [w]
            cur_len = len(display)
        else:
            cur.append(w)
            cur_len += add_len
    if cur:
        out.append(cur)
    return out


def _build_caption_line_events(
    segments: list[Segment],
    max_chars_per_line: int,
    caption_style: Optional[dict] = None,
    apply_width_wrap: bool = True,
) -> list[tuple[int, int, str]]:
    """Greedy word-packer producing caption events with embedded
    `\\N` line breaks. Mirrors the Captions-AI mental model:

      • Words flow into line 1 of the current caption.
      • When line 1 is full (by width OR `max_words_per_line`), the
        next word drops to line 2.
      • When the last line is full, the caption is emitted and a
        new one starts.
      • Punctuation (when `break_on_punct`) and pauses larger than
        `break_on_pause_ms` force the current caption to close.

    Each caption is therefore a single event whose text may contain
    one or more `\\N` separators, rendered as stacked lines by the
    per-line layout in `make_events`. `apply_width_wrap=False` keeps
    everything on one line regardless of width — handy for power
    users who want literal max-words honour.
    """
    max_words, max_lines, break_on_punct, pause_thresh = _grouping_opts_from_style(caption_style)

    def _line_text(line_words: list[WordTiming]) -> str:
        t = _strip_display_punct(" ".join(w.word for w in line_words))
        return _sanitize_libass(t)

    raw_lines: list[tuple[int, int, str]] = []
    for seg in segments:
        words = seg.words or []
        if not words:
            continue
        lines: list[list[WordTiming]] = []
        cur_line: list[WordTiming] = []
        cur_line_chars = 0

        def flush_line() -> None:
            nonlocal cur_line, cur_line_chars
            if cur_line:
                lines.append(cur_line)
                cur_line = []
                cur_line_chars = 0

        def emit_caption() -> None:
            nonlocal lines, cur_line, cur_line_chars
            flush_line()
            line_texts = [t for t in (_line_text(ln) for ln in lines) if t]
            if line_texts:
                flat = [w for ln in lines for w in ln]
                start_ms = int(flat[0].start * 1000)
                end_ms = int(flat[-1].end * 1000)
                if end_ms - start_ms < _MIN_DISPLAY_MS:
                    end_ms = start_ms + _MIN_DISPLAY_MS
                raw_lines.append((start_ms, end_ms, "\\N".join(line_texts)))
            lines = []

        for i, w in enumerate(words):
            display = _strip_display_punct(w.word)
            chars = len(display)
            # Width wrap: would adding this word overflow the line?
            # Skip the check when width-wrap is disabled (power-user mode).
            if (
                apply_width_wrap
                and cur_line
                and cur_line_chars + chars + 1 > max_chars_per_line
            ):
                flush_line()
                if len(lines) >= max_lines:
                    emit_caption()
            # Line word-cap: would adding push past max_words on this line?
            if cur_line and len(cur_line) >= max_words:
                flush_line()
                if len(lines) >= max_lines:
                    emit_caption()

            cur_line.append(w)
            cur_line_chars += chars + (1 if len(cur_line) > 1 else 0)

            # Hard breaks: punctuation or large pause close the caption
            # immediately, even if the line isn't full.
            ends_phrase = (
                break_on_punct and bool(w.word) and w.word[-1] in _BREAK_PUNCT
            )
            is_last = i == len(words) - 1
            gap_too_big = (
                pause_thresh > 0
                and not is_last
                and (words[i + 1].start - w.end) >= pause_thresh
            )
            if ends_phrase or gap_too_big:
                emit_caption()

        emit_caption()  # flush at segment boundary

    # Caption non-overlap guarantee. Invariant: at any instant, AT MOST
    # ONE caption is on screen. Two captions whose [start,end] windows
    # touch is fine; any overlap (even 1 ms) is forbidden.
    raw_lines.sort(key=lambda x: x[0])

    # Pass 1: push starts forward so no caption begins before the previous
    # one ends.
    pushed: list[tuple[int, int, str]] = []
    for start_ms, end_ms, text in raw_lines:
        if pushed:
            prev_end = pushed[-1][1]
            if start_ms < prev_end:
                start_ms = prev_end
        if end_ms < start_ms:
            end_ms = start_ms
        pushed.append((start_ms, end_ms, text))

    # Pass 2: clamp every caption's end to the NEXT caption's start, so
    # captions touch but never overlap. Drop any caption shorter than the
    # min display floor.
    final_events: list[tuple[int, int, str]] = []
    for idx, (start_ms, end_ms, text) in enumerate(pushed):
        next_start = pushed[idx + 1][0] if idx + 1 < len(pushed) else None
        if next_start is not None:
            end_ms = next_start
        if end_ms - start_ms < _MIN_DISPLAY_MS:
            continue
        final_events.append((start_ms, end_ms, text))

    # Pass 3: extend the LAST surviving caption out to a sensible end
    # using the natural end from `pushed` (otherwise it would just stop
    # at the dropped successor's start).
    if final_events and pushed:
        natural_last_end = max(e for _, e, _ in pushed)
        ls, le, lt = final_events[-1]
        if natural_last_end > le:
            final_events[-1] = (ls, natural_last_end, lt)

    # Defensive: enforce no overlap one more time.
    for i in range(1, len(final_events)):
        prev_end = final_events[i - 1][1]
        cur_start = final_events[i][0]
        if cur_start < prev_end:
            ps, _pe, pt = final_events[i - 1]
            final_events[i - 1] = (ps, cur_start, pt)

    return final_events


def generate_edited_ass(
    captions: list[dict],
    video_width: int,
    video_height: int,
    font_name: str = "Rubik",
    caption_box: Optional[dict] = None,
    caption_style: Optional[dict] = None,
) -> str:
    """Build an ASS string from explicit, post-edit caption events.

    Each event is a dict with `start`, `end` (seconds, floats) and `text`.
    Lines may carry literal `\\N` or actual newlines — we split before
    layout so each line gets its own correct Y offset.

    Used by both the live editor preview (via /api/preview-ass) and the
    /api/render path. Single source of truth for "given a list of caption
    events and style, what does the burned/overlaid track look like".
    """
    subs, make_events, _, _ = _build_styled_subs(
        video_width, video_height, font_name, caption_box, caption_style
    )
    for ev in captions or []:
        text = (ev.get("text") or "").strip()
        if not text:
            continue
        try:
            start_ms = int(round(float(ev["start"]) * 1000))
            end_ms = int(round(float(ev["end"]) * 1000))
        except (KeyError, TypeError, ValueError):
            continue
        if end_ms <= start_ms:
            continue
        lines = [
            ln for ln in text.replace("\\N", "\n").split("\n") if ln.strip()
        ] or [text]
        subs.events.extend(make_events(start_ms, end_ms, lines))
    return subs.to_string("ass")


def derive_caption_events(
    segments: list[Segment],
    video_width: int = 1920,
    video_height: int = 1080,
    font_name: str = "Rubik",
    caption_box: Optional[dict] = None,
    caption_style: Optional[dict] = None,
    apply_width_wrap: bool = True,
) -> list[dict]:
    """Public helper: returns the same line-level caption events
    `generate_ass_captions` would emit, as a list of
    `{"start": float_seconds, "end": float_seconds, "text": str}`.
    Used by the post-translation editor to populate the caption track
    without parsing the ASS file.
    """
    _, _, _, max_chars_per_line = _build_styled_subs(
        video_width, video_height, font_name, caption_box, caption_style
    )
    events = _build_caption_line_events(
        segments, max_chars_per_line, caption_style, apply_width_wrap=apply_width_wrap,
    )
    return [
        {"start": s / 1000.0, "end": e / 1000.0, "text": t}
        for (s, e, t) in events
    ]


def generate_preview_ass(
    text: str,
    video_width: int,
    video_height: int,
    caption_box: Optional[dict] = None,
    caption_style: Optional[dict] = None,
    font_name: str = "Rubik",
) -> str:
    """Build an ASS string for live preview rendering by JASSUB/libass.

    The preview shows a single, always-on caption styled identically to
    the final-render path. Wraps the input text against the same
    max_chars_per_line the real renderer uses, so the user sees the same
    line breaks they'll get in the burned video.
    """
    subs, make_events, _font_size, max_chars_per_line = _build_styled_subs(
        video_width, video_height, font_name, caption_box, caption_style
    )

    display = _strip_display_punct(text or "SAMPLE CAPTION")
    if not display:
        display = "SAMPLE CAPTION"

    # Wrap to chars/line using the real renderer's constraint, but NEVER
    # split a single word — overflow grows the line instead. (No timing
    # to fall back to multiple events here, just one always-on stack.)
    words = display.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = (cur + " " + w).strip() if cur else w
        if cur and len(candidate) > max_chars_per_line:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    if not lines:
        lines = [display]

    # Always-on events (~10 hours) so the preview stays visible regardless
    # of where the video element seeks.
    subs.events.extend(make_events(0, 10 * 60 * 60 * 1000, lines))

    return subs.to_string("ass")
