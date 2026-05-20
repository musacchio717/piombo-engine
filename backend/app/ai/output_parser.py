"""
output_parser.py — Parser per l'output strutturato del Narrator Agent.

Il narratore produce XML con tag HiPRAG:
    <think>...</think>
    <action>...</action>
    <stat_change>...</stat_change>
    <response>...</response>

Questo modulo estrae i campi e li restituisce come dataclass tipizzata.
Se un tag manca, usa il fallback senza rompere il flusso.
"""

from __future__ import annotations
import re
from dataclasses import dataclass, field


@dataclass
class NarratorOutput:
    """Output strutturato del narratore dopo il parsing."""
    think: str = ""
    action: str = "none"
    stat_change: str = "none"
    response: str = ""
    raw: str = ""          # testo grezzo originale (per debug/Langfuse)
    parse_errors: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Output valido se almeno <response> è presente e non vuoto."""
        return bool(self.response.strip())


_TAG_RE = {
    "think":       re.compile(r"<think>(.*?)</think>",             re.DOTALL),
    "action":      re.compile(r"<action>(.*?)</action>",           re.DOTALL),
    "stat_change": re.compile(r"<stat_change>(.*?)</stat_change>", re.DOTALL),
    "response":    re.compile(r"<response>(.*?)</response>",       re.DOTALL),
}


def parse_narrator_output(raw: str) -> NarratorOutput:
    """
    Parsa il testo grezzo dell'LLM e restituisce NarratorOutput.

    Robusto: non solleva eccezioni se un tag manca — lo registra
    in parse_errors e usa il fallback.
    """
    out = NarratorOutput(raw=raw)
    errors: list[str] = []

    for field_name, pattern in _TAG_RE.items():
        match = pattern.search(raw)
        if match:
            setattr(out, field_name, match.group(1).strip())
        else:
            if field_name == "response":
                # <response> è obbligatorio — fallback = tutto il testo grezzo
                out.response = raw.strip()
                errors.append(f"<{field_name}> tag mancante — usato raw fallback")
            else:
                errors.append(f"<{field_name}> tag mancante — usato default")

    out.parse_errors = errors
    return out
