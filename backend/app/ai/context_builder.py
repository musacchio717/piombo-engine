"""
context_builder.py — Genera il core context statico per il Narrator Agent.

Il core context è un blocco di testo fisso inserito in ogni prompt,
indipendentemente dal retrieval dinamico. Contiene:
  1. Contesto generale della storia (hardcoded)
  2. Scheda del protagonista (da KG)
  3. Gruppo di Pavia (da KG)
  4. Location corrente (da KG)

Va rigenerato solo al cambio di location o composizione del gruppo.
"""

from __future__ import annotations
from app.ai.graph.lore_graph import LoreGraph

# ---------------------------------------------------------------------------
# Testo fisso della storia — non viene dal KG
# ---------------------------------------------------------------------------
_STORY_CONTEXT = """Italia, marzo 2020. La pandemia Covid è esplosa in modo molto più grave della realtà storica: gli ospedali sono collassati, lo stato centrale ha perso il controllo di intere regioni, milizie private e gruppi armati controllano i checkpoint sulle strade statali.

Alessandro Rullo ha appena perso il lavoro da Trenitalia ed è andato alla stazione di Pavia a svuotare il suo armadietto. Un ricercatore in fuga del Policlinico San Matteo lo ha fermato e gli ha iniettato qualcosa nel collo prima di essere trascinato via o fuggire — non è chiaro. Ultime parole: "Non farti aprire." Alessandro non sa cosa ha in corpo. Non sa che potrebbe essere un vaccino sperimentale. L'obiettivo è tornare a Reggio Calabria dalla famiglia.

REGOLA NARRATIVA FONDAMENTALE: tu sei il narratore, non il giocatore. Non decidere cosa fa o pensa Alessandro — descrivi solo cosa vede, sente e percepisce. Le sue azioni le decide il giocatore."""


def build_core_context(lore_graph: LoreGraph, current_location_id: str) -> str:
    """
    Costruisce il core context combinando testo fisso + dati dal KG.
    Chiamare una volta per sessione (o al cambio location).
    """
    sections: list[str] = []

    # 1. Contesto generale
    sections.append(f"## Contesto della storia\n{_STORY_CONTEXT}")

    # 2. Scheda protagonista
    ale = lore_graph.get_node("alessandro_rullo")
    if ale:
        core = _parse_json_field(ale.get("core", "{}"))
        state = _parse_json_field(ale.get("state", "{}"))
        stats = state.get("stats", {})
        traits = ", ".join(core.get("personality_traits", []))
        values = ", ".join(core.get("core_values", []))
        aversions = ", ".join(core.get("core_aversions", []))
        bio = core.get("biography", "")
        speech = core.get("speech_style", "")
        section = (
            "## Protagonista: Alessandro Rullo\n"
            f"{bio}\n"
            f"Tratti: {traits}\n"
            f"Valori: {values} | Avversioni: {aversions}\n"
            f"Stile di parlata: {speech}\n"
            f"Stato attuale — Salute: {stats.get('health', 100)} | "
            f"Sospetto: {stats.get('suspicion', 0)} | "
            f"Reputazione: {stats.get('reputation', 0)}"
        )
        sections.append(section)

    # 3. Gruppo di Pavia
    group_members = _get_group_members(lore_graph)
    if group_members:
        lines = ["## Gruppo di Pavia (personaggi noti)"]
        for m in group_members:
            if m["id"] == "alessandro_rullo":
                continue
            core = _parse_json_field(m.get("core", "{}"))
            state = _parse_json_field(m.get("state", "{}"))
            name = core.get("name", m["id"])
            occ = core.get("occupation", "")
            origin = core.get("origin", "")
            traits = ", ".join(core.get("personality_traits", [])[:2])
            loc = state.get("current_location", "sconosciuta")
            rel = m.get("relation_dynamic", "")
            lines.append(
                f"- {name} ({occ}, da {origin}): {traits}. "
                f"Posizione: {loc}. {rel}"
            )
        sections.append("\n".join(lines))

    # 4. Location corrente
    loc_node = lore_graph.get_node(current_location_id)
    if loc_node:
        core = _parse_json_field(loc_node.get("core", "{}"))
        state = _parse_json_field(loc_node.get("state", "{}"))
        name = core.get("name", current_location_id)
        narrative_role = core.get("narrative_role", "")
        cultural = core.get("cultural_notes", "")
        danger = state.get("danger_level", "?")
        pandemic = state.get("pandemic_status", "")
        controlled = state.get("controlled_by", "")
        section = (
            f"## Location corrente: {name}\n"
            f"{narrative_role}\n"
            f"Note culturali: {cultural}\n"
            f"Livello pericolo: {danger}/10 | Pandemia: {pandemic} | "
            f"Controllo: {controlled}"
        )
        sections.append(section)

    return "\n\n".join(sections)


def _get_group_members(lore_graph: LoreGraph) -> list[dict]:
    """Recupera i membri del gruppo_pavia con dati base."""
    try:
        with lore_graph._driver.session() as session:
            result = session.run(
                "MATCH (c)-[:MEMBER_OF]->(f {id: 'gruppo_pavia'}) "
                "RETURN c.id AS id, c.core AS core, c.state AS state"
            )
            return [dict(r) for r in result]
    except Exception:
        return []


def _parse_json_field(value) -> dict:
    """Parsa un campo JSON stringa dal KG. Fail-safe."""
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    import json
    try:
        return json.loads(value)
    except Exception:
        return {}
