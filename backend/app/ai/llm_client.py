"""
llm_client.py — Interfaccia astratta LLM + implementazioni Mock e Ollama.

Pattern: dependency injection. Il Narrator Agent dipende da LLMClient,
non da Ollama direttamente. In dev si passa MockLLM, in prod OllamaLLM.
"""

from __future__ import annotations
import abc
import textwrap


class LLMClient(abc.ABC):
    """Interfaccia comune per tutti i client LLM."""

    @abc.abstractmethod
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Genera una risposta dato un system prompt e un user prompt.
        Restituisce il testo grezzo (con tag XML inclusi).
        """


class MockLLM(LLMClient):
    """
    Client LLM deterministico per test e sviluppo del flusso.
    Restituisce sempre una risposta XML valida senza chiamare nessun modello.
    Utile per testare il parsing, il flusso LangGraph e il Game Service
    senza dipendere da Ollama o dalla GPU.
    """

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        _ = system_prompt  # non usato nel mock
        return textwrap.dedent(f"""\
            <think>
            Il giocatore ha scritto: "{user_prompt[:80]}".
            Sono in modalità mock — restituisco una risposta narrativa di test.
            Non serve cercare altro contesto.
            </think>
            <action>none</action>
            <stat_change>none</stat_change>
            <response>
            [MOCK] L'aria ferma della quarantena pesa su ogni passo.
            Intorno a te, silenzio e cemento grigio. Cosa vuoi fare?
            </response>
        """)


class OllamaLLM(LLMClient):
    """
    Client LLM che chiama Ollama in locale.
    Parametri letti da config.py — switchare LLM_MODEL in .env o config.
    """

    def __init__(
        self,
        model: str | None = None,
        base_url: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        num_ctx: int | None = None,
    ) -> None:
        from app.core.config import settings
        self.model       = model       or settings.LLM_MODEL
        self.base_url    = base_url    or settings.OLLAMA_BASE_URL
        self.temperature = temperature or settings.LLM_TEMPERATURE
        self.max_tokens  = max_tokens  or settings.LLM_MAX_TOKENS
        self.num_ctx     = num_ctx     or settings.LLM_NUM_CTX

        # Import lazy: non rompe se ollama non è installato in dev
        try:
            import ollama as _ollama
            self._ollama = _ollama
        except ImportError:
            raise RuntimeError(
                "Pacchetto 'ollama' non trovato. "
                "Installa con: uv pip install ollama"
            )

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        response = self._ollama.chat(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_prompt},
            ],
            options={
                "temperature": self.temperature,
                "num_predict": self.max_tokens,
                "num_ctx":     self.num_ctx,
            },
            think=False,  # disabilita il think di Ollama, usiamo solo i tag XML personalizzati
        )
        return response["message"]["content"]
