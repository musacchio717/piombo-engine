# Piombo Engine — Benchmark LLM

**Hardware**: NVIDIA RTX 4070 12GB VRAM, WSL2 Ubuntu, Ollama  
**Stack**: FastAPI + LangGraph + Neo4j + Qdrant + bge-m3  
**Data**: 2026-05-21  

---

## Scenario 1 — Azione semplice in location conosciuta
**Input**: `"Guardo fuori dalla finestra. Vedo dei soldati in tuta protettiva per strada."`

| Metrica | Qwen3-8B | Nemo-12B |
|---|---|---|
| Latenza end-to-end (ms) | 7164 | 14269 |
| XML valido al 1° tentativo | ❌ | ✅ |
| Errori di parsing | `<think>` mancante | nessuno |
| Qualità narrativa IT (1-5) | 3 | 5 |

---

## Scenario 2 — Entità multiple dal Knowledge Graph
**Input**: `"Cerco il Tenente Marini al Checkpoint A1 Sud per chiedergli un lasciapassare."`

| Metrica | Qwen3-8B | Nemo-12B |
|---|---|---|
| Latenza end-to-end (ms) | 3234 | 3431 |
| XML valido al 1° tentativo | ❌ | ✅ |
| Entità KG citate correttamente | ✅ | ✅ |
| Errori di parsing | `<think>` mancante | nessuno |
| Qualità narrativa IT (1-5) | 3 | 5 |

---

## Scenario 3 — Dialogo in italiano colloquiale
**Input**: `"Chiedo al negoziante: 'Senti, hai qualcosa da mangiare? Pago bene.'"`

| Metrica | Qwen3-8B | Nemo-12B |
|---|---|---|
| Latenza end-to-end (ms) | 7670 | 5590 |
| XML valido al 1° tentativo | ❌ | ✅ |
| Errori grammaticali | 1 ("scomparso" → "scomparsi") | 0 |
| Errori di parsing | `<think>` mancante | nessuno |
| Qualità narrativa IT (1-5) | 3 | 5 |

**Note**: Nemo produce dialoghi più naturali e contestualmente ricchi. Qwen3 tende a ripetere il testo dell'azione del player.

---

## Scenario 4 — Sequenza 5 azioni consecutive
**Input**: sequenza di 5 azioni in ordine

| Metrica | Qwen3-8B | Nemo-12B |
|---|---|---|
| Latenza totale 5 azioni (ms) | ~57000 | ~19155 |
| Latenza media per azione (ms) | ~11400 | ~3831 |
| XML valido su 5 azioni (%) | 0% | 100% |
| Fallback attivati | 1 (azione 3) | 0 |
| Errori di parsing totali | 10 | 0 |
| Coerenza narrativa (1-5) | 3 | 5 |

**Note Qwen3**: azione 3 (`mercato_abbandonato`) ha scatenato doppio retry + fallback (24s). Location check troppo aggressivo su location non presenti nel KG.  
**Note Nemo**: coerenza narrativa mantenuta su tutta la sequenza, nessun fallback.

---

## Riepilogo comparativo

| Metrica | Qwen3-8B | Nemo-12B | Vincitore |
|---|---|---|---|
| XML valido al 1° tentativo | 0/8 (0%) | 8/8 (100%) | **Nemo** |
| Latenza media per azione (ms) | ~9300 | ~5300 | **Nemo** |
| Errori di parsing totali | 15 | 0 | **Nemo** |
| Errori grammaticali IT | 1 | 0 | **Nemo** |
| Qualità narrativa IT (1-5) | 3 | 5 | **Nemo** |
| VRAM occupata | ~5.5 GB | ~7.5 GB | Qwen3 |

---

## Decisione finale

**Modello scelto**: `mistral-nemo:12b-instruct-2407-q4_K_M`  
**Motivazione**: vince su tutti i parametri rilevanti — formato XML affidabile (100% vs 0%), latenza inferiore del 43%, qualità narrativa italiana nettamente superiore (5/5 vs 3/5), zero errori di parsing.  
**Riferimento**: ADR-004
