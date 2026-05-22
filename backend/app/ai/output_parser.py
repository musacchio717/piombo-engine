"""
output_parser.py — Parser per l'output strutturato del Narrator Agent.
"""
from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class NarratorOutput:
    think: str = ""
    action: str = "none"
    stat_change: str = "none"
    response: str = ""
    raw: str = ""
    parse_errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return bool(self.response.strip())


_TAG_RE = {
    "think":       re.compile(r"<think>(.*?)</think>",             re.DOTALL),
    "action":      re.compile(r"<action>(.*?)</action>",           re.DOTALL),
    "stat_change": re.compile(r"<stat_change>(.*?)</stat_change>", re.DOTALL),
    "response":    re.compile(r"<response>(.*?)</response>",       re.DOTALL),
}

_VALID_ACTION_BASES = {
    "none", "location_change", "use_item", "talk", "examine", "take", "give",
}


def _strip_nested_xml(text: str) -> str:
    """Rimuove tag XML annidati dentro <response> (modello che duplica l'output)."""
    cleaned = re.sub(r"<[a-z_]+>.*?</[a-z_]+>", "", text, flags=re.DOTALL)
    return cleaned.strip()


def _validate_action(raw: str) -> tuple[str, str | None]:
    """Valida <action>. Testo libero viene normalizzato a 'none'."""
    val = raw.strip()
    base = val.split(":")[0].lower()
    if base in _VALID_ACTION_BASES:
        return val.lower(), None
    return "none", f"<action> non riconosciuta: '{val[:40]}' → normalizzata a 'none'"


def parse_narrator_output(raw: str) -> NarratorOutput:
    out = NarratorOutput(raw=raw)
    errors: list[str] = []

    for field_name, pattern in _TAG_RE.items():
        match = pattern.search(raw)
        if match:
            value = match.group(1).strip()
            if field_name == "response":
                value = _strip_nested_xml(value)
                if not value:
                    out.response = raw.strip()
                    errors.append("<response> vuota dopo strip tag annidati — usato raw fallback")
                else:
                    out.response = value
            elif field_name == "action":
                validated, err = _validate_action(value)
                out.action = validated
                if err:
                    errors.append(err)
            else:
                setattr(out, field_name, value)
        else:
            if field_name == "response":
                out.response = raw.strip()
                errors.append(f"<{field_name}> tag mancante — usato raw fallback")
            else:
                errors.append(f"<{field_name}> tag mancante — usato default")

    out.parse_errors = errors
    return out
