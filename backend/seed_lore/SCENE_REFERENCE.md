# Piombo Engine — Riferimento campi scena

## Struttura generale

Una scena ha **4 beat in sequenza**. I beat 2 e 3 sono opzionali.

```
arrival  →  exploration (opt.)  →  event (opt.)  →  decision
```

Ogni beat ha un loop diverso:
- **arrival**: nessun input player, LLM descrive, si preme Enter
- **exploration**: il player scrive liberamente (budget limitato), poi avanti
- **event**: nessun input player, LLM rende l'evento scriptato, si preme Enter
- **decision**: il player sceglie tra opzioni numerate

---

## Chi legge cosa

| Campo | SceneRunner | LLM | Player |
|---|---|---|---|
| `id`, `title`, `location_id` | ✅ | ❌ | ❌ |
| `entry_conditions` | ✅ gating | ❌ | ❌ |
| `on_enter` effects | ✅ applica | ❌ | ❌ |
| `arrival.director_note` | ❌ | ✅ come istruzione PRIVATA | ❌ |
| `arrival.npcs_present` | ❌ | ✅ lista NPC nella scena | ❌ |
| `arrival.objects_present` | ❌ | ✅ lista oggetti visibili | ❌ |
| `exploration.budget` | ✅ conta azioni | ❌ | ✅ vede il contatore |
| `examinable.name` | ✅ matching input | ❌ | ✅ è quello che scrive |
| `examinable.description_hint` | ❌ | ✅ PRIVATA | ❌ |
| `examinable.reveal_flag` | ✅ setta flag | ❌ | ❌ |
| `examinable.requires_flag` | ✅ filtro visibilità | ❌ | ❌ |
| `dialogable_npcs.id` | ✅ matching input | ✅ sa chi sta parlando | ❌ |
| `dialogable_npcs.topic_hints` | ❌ | ✅ cosa l'NPC può dire | ❌ |
| `dialogable_npcs.forbidden_topics` | ❌ | ✅ cosa NON rivelare | ❌ |
| `event.director_note` | ❌ | ✅ istruzione PRIVATA | ❌ |
| `event.facts` | ❌ | ✅ verità da rendere tutte | ❌ |
| `event.sets_flag` | ✅ setta flag | ❌ | ❌ |
| `event.trigger` | ✅ quando far scattare | ❌ | ❌ |
| `decision.choices[].text` | ❌ | ❌ | ✅ testo della scelta |
| `decision.choices[].requires_flag` | ✅ filtro disponibilità | ❌ | ❌ |
| `decision.choices[].requires_stat` | ✅ filtro disponibilità | ❌ | ❌ |
| `decision.choices[].effects` | ✅ applica stat/flag | ❌ | ❌ |
| `decision.choices[].next_scene_id` | ✅ navigazione | ❌ | ❌ |
| `decision.choices[].resolution_hint` | ❌ | ✅ istruzione PRIVATA | ❌ |

---

## Campi nel dettaglio

### Livello scena

```json
"id": "snake_case_univoco"
```
Identificatore interno. Usato da `next_scene_id` per le transizioni.
Non mostrato al player.

```json
"title": "Titolo interno"
```
Usato nei log e nel test CLI. Non mostrato al player.

```json
"location_id": "pavia"
```
Deve corrispondere a un nodo Neo4j. Usato da `build_core_context` per
recuperare la descrizione della location da includere nel prompt LLM.

---

### entry_conditions (opzionale)

```json
"entry_conditions": {
  "flags_required": ["inoculazione_avvenuta"],
  "flags_absent": ["already_escaped"],
  "stats": { "health": { "min": 20 }, "suspicion": { "max": 70 } }
}
```
Controlate dal SceneRunner prima di transitare in questa scena.
Se non soddisfatte → errore runtime (la scena non viene raggiunta).
Ometti se la scena è sempre raggiungibile nel flusso lineare.

---

### on_enter (opzionale)

```json
"on_enter": {
  "stats": { "suspicion": 5 },
  "flags_set": ["entered_zona_rossa"],
  "flags_clear": []
}
```
Effetti applicati **immediatamente** all'ingresso, prima dell'arrival beat.
`stats` è un delta: `5` = +5, `-10` = -10.

---

### arrival

```json
"arrival": {
  "director_note": "...",
  "npcs_present": ["davide_rullo"],
  "objects_present": ["obj_tesi_davide", "obj_tv_notizie"]
}
```

**`director_note`** — Istruzione privata per l'LLM. Non compare mai nel testo.
Scrivi: tono, cosa enfatizzare, cosa NON dire ancora. È il tuo strumento
principale per controllare la qualità dell'arrivo.

**`npcs_present`** — Passa all'LLM come "NPC presenti in scena". Non sono
cliccabili qui — sono solo informazione di contesto per la prosa.
Gli NPC dialogabili vanno in `exploration.dialogable_npcs`.

**`objects_present`** — Passa all'LLM come "oggetti visibili". Anche questi
sono solo contesto narrativo nell'arrival. Per renderli interattivi
devono comparire anche in `exploration.examinable`.

---

### exploration (opzionale)

```json
"exploration": {
  "budget": 2,
  "examinable": [...],
  "dialogable_npcs": [...]
}
```

**`budget`** — Numero massimo di azioni libere del player. Quando si
esaurisce il gioco avanza automaticamente all'event (o alla decision).
Azioni "flavor" (input senza match) non consumano budget.

#### examinable

```json
{
  "id": "obj_tv_notizie",
  "name": "il telegiornale",
  "lore_entity_id": null,
  "description_hint": "Cosa deve comunicare l'LLM...",
  "reveal_flag": "viste_notizie_pandemia",
  "requires_flag": null
}
```

**`name`** — Quello che il player deve scrivere per triggerare l'oggetto.
Il matching è substring: se il player scrive "guardo il telegiornale",
trova "il telegiornale". Scrivi nomi naturali, non id tecnici.

**`description_hint`** — Istruzione PRIVATA per l'LLM. Descrive cosa
mostrare. Non è il testo finale — è la traccia per l'LLM.

**`reveal_flag`** — Se presente, viene settato a `true` quando il player
esamina l'oggetto. Utile per sbloccare dialoghi o scelte successive.

**`requires_flag`** — Se presente, l'oggetto è invisibile finché quel flag
non è `true`. Usato per oggetti che si sbloccano dopo certi eventi.

**`lore_entity_id`** — ID nodo Neo4j collegato a questo oggetto. Se
valorizzato, il retrieval usa questo nodo come seed. Null = nessun
retrieval specifico per questo oggetto.

#### dialogable_npcs

```json
{
  "id": "davide_rullo",
  "topic_hints": ["cosa è successo alla stazione", "il viaggio verso Reggio"],
  "forbidden_topics": ["il vaccino", "le intenzioni del ricercatore"]
}
```

**`id`** — Deve contenere (come substring) quello che il player scrive.
Es: il player scrive "Davide" → matcha "davide_rullo" perché
il codice cerca il primo token (`davide`).

**`topic_hints`** — Passati all'LLM come "l'NPC può parlare di questi
argomenti". Guidano la risposta senza bloccarla rigidamente.

**`forbidden_topics`** — Passati all'LLM come "l'NPC NON rivela mai".
Usali per proteggere info che devono emergere in scene successive.

---

### event (opzionale)

```json
"event": {
  "trigger": "after_exploration",
  "director_note": "...",
  "facts": ["Fatto 1", "Fatto 2", "Fatto 3"],
  "npc_id": "davide_rullo",
  "sets_flag": "davide_viene_con_noi"
}
```

**`trigger`** — `"after_exploration"` (default, scatta dopo il budget) o
`"immediate"` (scatta subito dopo l'arrival, salta l'exploration).

**`director_note`** — Istruzione PRIVATA per l'LLM. Tono e stile della
scena evento. Complementare ai facts.

**`facts`** — Lista di verità che L'LLM deve rendere TUTTE nella prosa,
nell'ordine che vuole. Scrivi fatti, non dialoghi. Esempio corretto:
`"Davide inizia a prepararsi senza dirlo esplicitamente"`.
Esempio sbagliato: `"Davide dice: 'dobbiamo partire'"`.

**`npc_id`** — NPC protagonista dell'evento. Usato come info all'LLM
(il retrieval per l'event è disabilitato).

**`sets_flag`** — Flag settato quando l'evento scatta. Avviene dopo
che l'LLM ha generato la prosa, non prima.

---

### decision

```json
"decision": {
  "prompt_hint": "Testo opzionale di contesto per il player",
  "choices": [...]
}
```

**`prompt_hint`** — Mostrato al player sopra le scelte. Opzionale.

#### choices

```json
{
  "id": "scappa_subito",
  "text": "Prendi le tue cose e esci dalla stazione",
  "requires_flag": "has_pavia_papers",
  "requires_stat": { "health": { "min": 30 } },
  "effects": {
    "stats": { "suspicion": 10, "health": -5 },
    "flags_set": ["usato_lasciapassare"],
    "flags_clear": [],
    "inventory_add": [],
    "inventory_remove": ["razioni_emergenza"]
  },
  "next_scene_id": "checkpoint_milano",
  "resolution_hint": "Istruzione PRIVATA per l'LLM su come rendere l'esito."
}
```

**`text`** — Testo mostrato al player nella lista numerata.

**`requires_flag`** — Se presente, la scelta appare solo se quel flag
è `true`. Usato per scelte che richiedono oggetti o eventi precedenti.

**`requires_stat`** — Supporta due formati:
- Range stat: `{ "health": { "min": 30 } }`
- Inventario: `{ "inventory_contains": "razioni_emergenza" }`

**`effects.stats`** — Delta sulle statistiche. Applicati subito alla scelta.

**`effects.flags_set` / `flags_clear`** — Flag da settare/resettare.

**`effects.inventory_add` / `inventory_remove`** — Oggetti da
aggiungere/rimuovere. Gli id devono corrispondere a quelli in `objects.json`.

**`next_scene_id`** — Scena successiva. Deve corrispondere all'`id` di una
scena caricata. Se null, la sessione termina.

**`resolution_hint`** — Istruzione PRIVATA per l'LLM per la rendering
dell'esito. Scrivi il risultato emotivo/narrativo, non il dialogo.
Es: `"Marini guarda i documenti a lungo. Troppo a lungo."`.

---

## Regola dei campi PRIVATI

I campi marcati PRIVATA nel prompt (`director_note`, `description_hint`,
`resolution_hint`, `forbidden_topics`, `topic_hints`, `facts`) arrivano
all'LLM come istruzioni o vincoli — mai nel testo finale che legge il player.

Come usarli bene:
- **Scrivi fatti, non dialoghi.** L'LLM costruisce il dialogo da solo.
- **Scrivi cosa NON fare** tanto quanto cosa fare.
- **Sii specifico su tono e lunghezza** quando l'LLM tende a sbagliare.

---

## flags.json — il manifesto dei flag

Tutti i flag usati nelle scene devono essere dichiarati in
`seed_lore/flags.json` prima di essere usati.
Se un flag non è nel manifesto, il SceneRunner lancia un WARNING ma
non crasha — il flag viene comunque settato.

```json
{
  "flags": {
    "nome_flag": {
      "default": false,
      "description": "Breve descrizione di cosa rappresenta"
    }
  }
}
```