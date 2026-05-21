"""
semantic_checker.py — Semantic Grounding Checker (Fase 2).

Implementa il pattern LLM-as-Judge (Zheng et al., 2023) adattato per RAG:
    - Judge: Qwen2.5-3B-Instruct Q4_K_M (leggero, italiano nativo)
    - Narrator: Mistral Nemo 12B (generatore)

Il judge riceve il contesto lore retrieved + la response del narratore
e valuta se la risposta è grounded nel contesto o contiene hallucination.

Comportamento configurabile:
    - SEMANTIC_CHECK_BLOCKING=False (default): warning, non blocca il flusso
    - SEMANTIC_CHECK_BLOCKING=True: forza retry se non grounded
"""

from __future__ import annotations
import logging
import re
from dataclasses import dataclass

from app.core.config import settings

logger = logging.getLogger(__name__)

_JUDGE_SYSTEM_PROMPT = """\
Sei un valutatore di qualità per un sistema RAG narrativo.
Il tuo compito è verificare se la RISPOSTA DEL NARRATORE è coerente con il CONTESTO LORE fornito.

Una risposta è "grounded" se:
- Non inventa fatti, luoghi o personaggi assenti nel CONTESTO LORE
- Non contraddice informazioni presenti nel CONTESTO LORE
- Può usare creatività narrativa purché non violi i fatti del CONTESTO

Rispondi ESCLUSIVAMENTE con questo formato XML, zero testo aggiuntivo:
<grounded>True</grounded>
<reason>motivazione breve in italiano (max 20 parole)</reason>

Oppure:
<grounded>False</grounded>
<reason>cosa è stato inventato o contraddetto (max 20 parole)</reason>
"""

_GROUNDED_RE = re.compile(r"<grounded>(True|False)</grounded>", re.IGNORECASE)
_REASON_RE = re.compile(r"<reason>(.*?)</reason>", re.DOTALL)


@dataclass
class SemanticCheckResult:
    grounded: bool = True
    reason: str = ""
    raw: str = ""
    skipped: bool = False
    error: str = ""


class SemanticChecker:
    """
    Judge LLM-based per semantic grounding check.
    Usa Qwen2.5-3B-Instruct come modello leggero per il ruolo di judge.
    """

    def __init__(
        self,
        model: str | None = None,
        blocking: bool | None = None,
        enabled: bool | None = None,
    ) -> None:
        self.model    = model    or settings.CHECKER_MODEL
        self.blocking = blocking if blocking is not None else settings.SEMANTIC_CHECK_BLOCKING
        self.enabled  = enabled  if enabled  is not None else settings.SEMANTIC_CHECK_ENABLED

        try:
            import ollama as _ollama
            self._ollama = _ollama
        except ImportError:
            raise RuntimeError("Pacchetto 'ollama' non trovato.")

    def check(
        self,
        retrieval_context: str,
        narrator_response: str,
    ) -> SemanticCheckResult:
        """
        Chiama il judge LLM e restituisce SemanticCheckResult.
        Se disabled o contesto vuoto → skip automatico.
        """
        if not self.enabled:
            return SemanticCheckResult(skipped=True, reason="checker disabilitato")

        if not retrieval_context or not retrieval_context.strip():
            return SemanticCheckResult(skipped=True, reason="contesto retrieved vuoto — skip")

        if not narrator_response or not narrator_response.strip():
            return SemanticCheckResult(grounded=False, reason="response vuota")

        user_prompt = (
            f"## CONTESTO LORE\n{retrieval_context[:2000]}\n\n"
            f"## RISPOSTA DEL NARRATORE\n{narrator_response}"
        )

        try:
            response = self._ollama.chat(
                model=self.model,
                messages=[
                    {"role": "system", "content": _JUDGE_SYSTEM_PROMPT},
                    {"role": "user",   "content": user_prompt},
                ],
                options={
                    "temperature": 0.0,   # determinístico — task di classificazione
                    "num_predict": 128,
                    "num_ctx":     4096,
                },
            )
            raw = response["message"]["content"]
        except Exception as e:
            logger.error("semantic_checker: errore chiamata LLM: %s", e)
            return SemanticCheckResult(
                grounded=True,  # fail-open: non blocca il flusso in caso di errore
                error=str(e),
                skipped=True,
            )

        return self._parse(raw)

    def _parse(self, raw: str) -> SemanticCheckResult:
        grounded_match = _GROUNDED_RE.search(raw)
        reason_match   = _REASON_RE.search(raw)

        if not grounded_match:
            logger.warning("semantic_checker: formato XML non trovato — fail-open")
            return SemanticCheckResult(grounded=True, raw=raw, reason="parsing fallito")

        grounded = grounded_match.group(1).strip().lower() == "true"
        reason   = reason_match.group(1).strip() if reason_match else ""

        if not grounded:
            logger.warning("semantic_checker: NOT grounded — %s (blocking=%s)", reason, self.blocking)
        else:
            logger.info("semantic_checker: grounded ✓ — %s", reason)

        return SemanticCheckResult(grounded=grounded, reason=reason, raw=raw)
