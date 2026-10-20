"""Parse a multi-language .docx script.

The script is expected to contain bold headers naming a language
(e.g. **EN**, **DE**, **GR**) followed by the translated dialogue
for that language. This module extracts a `{lang_code: text}` mapping.
"""
from __future__ import annotations
import re
from typing import Dict
from docx import Document


# Map common header abbreviations -> ISO language code used by the app.
HEADER_TO_LANG: Dict[str, str] = {
    "EN": "en", "ENG": "en", "ENGLISH": "en",
    "DE": "de", "GER": "de", "GERMAN": "de",
    "FR": "fr", "FRA": "fr", "FRENCH": "fr",
    "ES": "es", "SPA": "es", "SPANISH": "es",
    "IT": "it", "ITA": "it", "ITALIAN": "it",
    "PT": "pt", "POR": "pt", "PORTUGUESE": "pt",
    "NL": "nl", "DUT": "nl", "DUTCH": "nl",
    "PL": "pl", "POL": "pl", "POLISH": "pl",
    "RO": "ro", "ROM": "ro", "ROMANIAN": "ro",
    "BG": "bg", "BUL": "bg", "BULGARIAN": "bg",
    "EL": "el", "GR": "el", "GRE": "el", "GREEK": "el",
    "HR": "hr", "CRO": "hr", "CROATIAN": "hr",
    "HU": "hu", "HUN": "hu", "HUNGARIAN": "hu",
    "LV": "lv", "LAT": "lv", "LATVIAN": "lv",
    "LT": "lt", "LIT": "lt", "LITHUANIAN": "lt",
    "RS": "sr", "SR": "sr", "SRB": "sr", "SERBIAN": "sr",
    "SI": "sl", "SL": "sl", "SLO": "sl", "SLOVENIAN": "sl",
    "CZ": "cs", "CS": "cs", "CZE": "cs", "CZECH": "cs",
    "SK": "sk", "SVK": "sk", "SLOVAK": "sk",
    "DK": "da", "DA": "da", "DAN": "da", "DANISH": "da",
    "SE": "sv", "SV": "sv", "SWE": "sv", "SWEDISH": "sv",
}

# Native / endonym names — also accepted as headers (e.g. "Deutsch", "Ελληνικά").
# Stored uppercased so the lookup goes through _normalize() like everything else.
NATIVE_NAMES: Dict[str, str] = {
    "DEUTSCH": "de",
    "FRANÇAIS": "fr", "FRANCAIS": "fr",
    "ESPAÑOL": "es", "ESPANOL": "es", "CASTELLANO": "es",
    "ITALIANO": "it",
    "PORTUGUÊS": "pt", "PORTUGUES": "pt",
    "NEDERLANDS": "nl", "VLAAMS": "nl",
    "POLSKI": "pl",
    "ROMÂNĂ": "ro", "ROMANA": "ro",
    "БЪЛГАРСКИ": "bg",
    "ΕΛΛΗΝΙΚΆ": "el", "ΕΛΛΗΝΙΚΑ": "el",
    "HRVATSKI": "hr",
    "MAGYAR": "hu",
    "LATVIEŠU": "lv", "LATVIESU": "lv",
    "LIETUVIŲ": "lt", "LIETUVIU": "lt",
    "СРПСКИ": "sr", "SRPSKI": "sr",
    "SLOVENŠČINA": "sl", "SLOVENSCINA": "sl",
    "ČEŠTINA": "cs", "CESTINA": "cs",
    "SLOVENČINA": "sk", "SLOVENCINA": "sk",
    "DANSK": "da",
    "SVENSKA": "sv",
}

# Match flag emojis (regional indicator pairs) and common pictographs.
_FLAG_RE = re.compile(
    r"[\U0001F1E6-\U0001F1FF]{1,2}"  # regional indicators (country flags)
    r"|\U0001F3F4[\U000E0060-\U000E007F]+"  # tag-sequence flags (e.g. England)
    r"|[\U0001F300-\U0001FAFF\u2600-\u27BF]"  # misc symbols/pictographs
)


def _strip_flags(text: str) -> str:
    return _FLAG_RE.sub("", text).strip()


def _normalize(token: str) -> str:
    token = _strip_flags(token)
    return re.sub(r"[\*\[\]\(\)\:\.\-_/\\]+", "", token).strip().upper()


def _lookup(cleaned: str) -> str | None:
    if not cleaned:
        return None
    if cleaned in HEADER_TO_LANG:
        return HEADER_TO_LANG[cleaned]
    if cleaned in NATIVE_NAMES:
        return NATIVE_NAMES[cleaned]
    return None


def _header_lang(token: str) -> str | None:
    # Try the whole thing first.
    hit = _lookup(_normalize(token))
    if hit:
        return hit
    # Then try each word/parenthetical piece individually so headers like
    # "🇩🇰 Danish (Dansk)" or "EN - English" still resolve.
    stripped = _strip_flags(token)
    for piece in re.split(r"[\s()\[\]\-—:/,|]+", stripped):
        hit = _lookup(_normalize(piece))
        if hit:
            return hit
    return None


def parse_script_docx(path: str) -> Dict[str, str]:
    """Parse a free-form .docx into a {lang_code: full_text} dict.

    The document is treated as a flat stream of paragraphs. A paragraph that is
    a bold language abbreviation (e.g. "EN", "DE", "GR") opens a new section;
    everything after it — until the next such header — belongs to that
    language. Bold detection is lenient: either the whole paragraph is bold or
    its first run is bold.
    """
    doc = Document(path)
    sections: Dict[str, list[str]] = {}
    current_lang: str | None = None

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            if current_lang is not None:
                sections[current_lang].append("")
            continue

        # Header candidate: short paragraph that normalizes to a known code.
        # Bold is no longer required — the abbreviation alone is enough.
        if len(text) <= 40:
            lang_from_para = _header_lang(text)
            if lang_from_para:
                current_lang = lang_from_para
                sections.setdefault(current_lang, [])
                continue

        # Inline header: paragraph starts with an abbreviation followed by
        # the script on the same line (e.g. "EN: Hello there...").
        first_token = re.split(r"[\s:\-—]+", text, maxsplit=1)[0]
        lang = _header_lang(first_token)
        if lang:
            current_lang = lang
            sections.setdefault(current_lang, [])
            remainder = text[len(first_token):].strip(" :-—\t")
            if remainder:
                sections[current_lang].append(remainder)
            continue

        if current_lang is None:
            continue
        sections[current_lang].append(text)

    # Collapse, drop trailing blanks
    result: Dict[str, str] = {}
    for lang, lines in sections.items():
        joined = "\n".join(lines).strip()
        if joined:
            result[lang] = joined
    return result


def parse_script_text(text: str) -> Dict[str, str]:
    """Parse pasted plain text into a {lang_code: full_text} dict.

    Same logic as parse_script_docx but operating on raw text lines instead of
    Word paragraphs.
    """
    sections: Dict[str, list[str]] = {}
    current_lang: str | None = None

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if current_lang is not None:
                sections[current_lang].append("")
            continue

        # Header candidate: short line that normalizes to a known code.
        if len(line) <= 40:
            lang_from_line = _header_lang(line)
            if lang_from_line:
                current_lang = lang_from_line
                sections.setdefault(current_lang, [])
                continue

        # Inline header: line starts with an abbreviation followed by text.
        first_token = re.split(r"[\s:\-—]+", line, maxsplit=1)[0]
        lang = _header_lang(first_token)
        if lang:
            current_lang = lang
            sections.setdefault(current_lang, [])
            remainder = line[len(first_token):].strip(" :-—\t")
            if remainder:
                sections[current_lang].append(remainder)
            continue

        if current_lang is None:
            continue
        sections[current_lang].append(line)

    result: Dict[str, str] = {}
    for lang, lines in sections.items():
        joined = "\n".join(lines).strip()
        if joined:
            result[lang] = joined
    return result
