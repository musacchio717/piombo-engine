# ADR-004: Scelta modello LLM per Narrator Agent

**Status**: Accepted  
**Data**: 2026-05-21  
**Decider**: Alessandro Musacchio  

## Context

Il Narrator Agent richiede un LLM locale che soddisfi questi requisiti:
- Output XML strutturato affidabile (`<think>`, `<action>`, `<stat_change>`, `<response>`)
- Qualità narrativa italiana adeguata per un gioco testuale
- Latenza accettabile su RTX 4070 12GB VRAM
- Fit in memoria con il resto dello stack (Neo4j + Qdrant + bge-m3)

## Decision

**Mistral Nemo 12B Q4_K_M** (`mistral-nemo:12b-instruct-2407-q4_K_M`) via Ollama.

## Rationale

Benchmark empirico su 4 scenari (8 azioni totali), hardware RTX 4070 12GB:

| Metrica | Qwen3-8B | Nemo-12B |
|---|---|---|
| XML valido al 1° tentativo | 0% | 100% |
| Latenza media (ms) | ~9300 | ~5300 |
| Qualità narrativa IT (1-5) | 3 | 5 |
| Errori di parsing | 15 | 0 |

Nemo vince su tutti i parametri rilevanti. Il vantaggio critico è l'**aderenza al formato XML**: Qwen3-8B non produce mai il tag `<think>` nonostante i few-shot examples, causando 15 errori di parsing e un fallback nel scenario 4. Nemo produce output strutturati corretti al primo tentativo in tutti gli 8 casi testati.

La latenza media di Nemo (~5.3s) è inferiore a Qwen3 (~9.3s) nonostante le dimensioni maggiori — effetto del tokenizer Tekken più efficiente per l'italiano.

VRAM: Nemo occupa ~7.5GB vs ~5.5GB di Qwen3. Con 12GB disponibili e ~2GB di overhead (Neo4j + Qdrant + embeddings), il margine è sufficiente per context fino a 8K token.

## Consequences

**Pro**:
- Zero errori di parsing → consistency checker più efficace
- Latenza inferiore nonostante modello più grande
- Qualità narrativa italiana notevolmente superiore
- 128K context window nativa (limitata a 8K dal config per VRAM)

**Contro**:
- +2GB VRAM rispetto a Qwen3-8B
- Context window reale limitata a ~8-16K su 12GB VRAM

## Alternatives considered

**Qwen3-8B Q4_K_M**: scartato per aderenza al formato XML inaffidabile (0% al primo tentativo) e qualità narrativa italiana inferiore (3/5). Rimane installato come fallback di dev.

## References

- `docs/benchmarks.md` — risultati completi benchmark
- Zheng et al., 2023 — *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*
- Mistral AI, 2024 — *Mistral NeMo* technical report
