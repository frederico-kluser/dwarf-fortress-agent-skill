# Dwarf Fortress Agent Skills

> A **flat** Agent Skills knowledge base for [Dwarf Fortress](https://www.bay12games.com/dwarves/) **v50 / Premium**, built from the official [wiki](https://dwarffortresswiki.org) via its MediaWiki API — **no RAG, no embeddings, no vector database**. Clean Markdown + progressive disclosure + a tiny SQLite FTS5 search.

[![npm](https://img.shields.io/npm/v/dwarf-fortress-agent-skill)](https://www.npmjs.com/package/dwarf-fortress-agent-skill)
[![License: GFDL & MIT](https://img.shields.io/badge/wiki_license-GFDL%20%26%20MIT-blue)](#license)
[![Skills](https://img.shields.io/badge/skills-16-orange)](#skill-categories)
[![Source](https://img.shields.io/badge/source-v50%20wiki%20(2026--06)-green)](https://dwarffortresswiki.org)

## What is this?

16 flat Agent Skills (progressive-disclosure pattern) that any LLM agent — pi, Claude Code, Cursor, Codex, or a custom OpenRouter agent — can load to answer Dwarf Fortress gameplay questions with **wiki ground-truth accuracy** instead of inventing mechanics from memory (13 wiki skills) — and to read/control/**copilot** a running game through DFHack (the [bridge](#live-bridge-dfhack), an adventure-mode copilot, and `df-central`: the mission-control skill that routes scenarios to skills and carries the play-learn-build mission; resume it any time with the XML prompt in [`PROMPT.md`](PROMPT.md)).

The agent discovers skills by their `description` in the system prompt. When a task matches, it loads the skill's `SKILL.md` (a short index), then either reads the specific article from `references/` or runs the bundled full-text search. The descriptions are **bilingual** (Portuguese framing + English game keywords) so the skills trigger whether the user writes in Portuguese or English.

### Why flat?

pi/Claude Code skill discovery is **flat and recursive** — every directory with a `SKILL.md` becomes a **peer** in the system prompt, discovered only by its `description`. There is no built-in routing; the `description` *is* the router. Consequences this repo respects:

- **Strict YAML.** Descriptions are emitted as YAML folded scalars (`>-`), so colons/quotes never break the parser (a `description:` with an unquoted `:` silently fails to load).
- **Unique names.** Name collisions keep only the first discovery, so all 16 names are unique.
- **One deliberate router, no greedy catch-all.** Knowledge questions route purely by description match. `df-central` exists for a different job — mission control for the live copilot (which skill for which scenario, how to combine them) — and its description is scoped to meta/copilot asks so it does not compete with the 13 wiki skills on content questions.

## How the content is built (and why it's trustworthy)

Earlier versions parsed a **2023 raw-wikitext XML dump** with `mwparserfromhell` + `pandoc`. That route is fundamentally lossy: offline parsers **cannot expand MediaWiki templates**, so infoboxes, token tables and `/raw` subpages came out empty or as `[TABLE]` placeholders. See [`AUDIT_REPORT.md`](AUDIT_REPORT.md) for the full findings.

This version fetches **server-rendered HTML from the live wiki's MediaWiki API** (`action=parse`), where templates are already expanded. As a result:

- **Creature infoboxes are preserved** as `**Key:** value` blocks (pet value, body size, butchering returns, etc.).
- **Token reference tables** (creature/weapon/material tokens) survive as proper GFM pipe tables — no `[TABLE]`.
- **`/raw` pages** contain the full `[CREATURE:DOG]…` token definitions as fenced code blocks.
- Content is **current v50** (namespace 0), snapshot dated `2026-06`, not a stale 2023 merge.

## Skill categories

| Skill | Mode | Covers |
|---|---|---|
| `df-criaturas` | both | creatures, animals, megabeasts, races, bestiary, creature raws |
| `df-combate` | both | weapons, armor, wounds, syndromes, military, squads, defense |
| `df-materiais` | both | metals, stone, ores, gems, alloys, steel, adamantine, properties |
| `df-saude` | both | food, drink, hospital, disease, farming, crops, brewing |
| `df-geologia` | both | biomes, layers, worldgen, plants, trees, locations, lore |
| `df-modding` | both | tokens, raws, tilesets, graphics, creature definitions |
| `df-interface` | both | keys, menus, designations, UI shortcuts (v50) |
| `df-adventure` | adventure | character creation, travel, conversation, sites, quests |
| `df-fortress-industria` | fortress | smelting, forging, workshops, production chains |
| `df-fortress-construcao` | fortress | workshops, furniture, mechanisms, stockpiles, bridges |
| `df-dwarves` | fortress | labors, skills, strange moods, nobles, justice, psychology |
| `df-comercio` | fortress | trade, caravans, depot, wealth, economy |
| `df-fortress-geral` | fortress | defense, embarks, physics (water/magma/pressure), guides, FAQs |
| `df-live-bridge` | both | **live DFHack bridge**: read/translate gamelog events, structured JSON state, send console commands to a running game |
| `df-adventure-live` | adventure | **adventure copilot**: narrates the live session (health, nearby units, threats, inventory), advises next steps, curated adventure tools |
| `df-central` | both | **mission control**: capability map, scenario→skill routing, skill combinations, the play-learn-build mission and roadmap |

Descriptions are bilingual; article content is the original English wiki text
(`df-live-bridge`'s references are hand-written from DFHack docs, not wiki extracts).

## How an agent uses it

```
1. System prompt includes the 16 descriptions. Agent sees the user's question.
2. Agent picks the relevant skill by description match (PT or EN triggers).
3. Agent loads that skill's SKILL.md (short index + search instructions).
4. Agent searches, then reads the specific references/*.md article:
     python3 .agents/skills/scripts/search.py --skill df-materiais "steel smelting"
     python3 .agents/skills/scripts/search.py --json "magma forge"
5. Agent answers in the user's language, citing the source wiki page.
```

Articles are English. When the user asks in Portuguese, the agent translates the
query to English game terms (helped by `scripts/glossary-pt-en.tsv`), searches in
English, and answers in Portuguese.

## Search (SQLite FTS5, stdlib-only)

```bash
python3 .agents/skills/scripts/search.py "how to make steel"        # all skills
python3 .agents/skills/scripts/search.py --skill df-criaturas "dragon"
python3 .agents/skills/scripts/search.py --json --limit 8 "como fazer aço"
```

- **BM25 ranking** with title boosting and context snippets.
- **Porter stemming** (`smelt`/`smelting`, `miner`/`mining`).
- **Multi-term AND** (multi-word queries work — unlike the old whole-phrase grep).
- **PT→EN expansion** via `scripts/glossary-pt-en.tsv` (~200 jargon pairs) so
  Portuguese queries match English content.
- **Graceful fallback**: AND → OR → prefix when a query returns nothing.

The index (`scripts/index.db`) is a derived artifact built from the markdown by
`scripts/build_index.py`. Search uses only the Python standard library (`sqlite3`
with FTS5) — no network, no external packages — so it is portable to any agent
runtime. A `grep -ril` fallback is documented in each SKILL.md for index-less use.

## Live bridge (DFHack)

`df-live-bridge` is the one non-wiki skill: it connects the agent to a **running**
Dwarf Fortress on Linux through [DFHack](https://github.com/DFHack/dfhack).

```bash
python3 .agents/skills/scripts/df_bridge.py status --json     # game found? readable? controllable?
python3 .agents/skills/scripts/df_bridge.py log --new --json  # events since the last check (category-tagged)
python3 .agents/skills/scripts/df_bridge.py watch --seconds 15
python3 .agents/skills/scripts/df_bridge.py state all         # structured JSON: adventurer, threats, date
python3 .agents/skills/scripts/df_bridge.py act screen        # read the visible screen text (menus)
python3 .agents/skills/scripts/df_bridge.py act move n        # act: walk, press keys (adventure)
python3 .agents/skills/scripts/df_bridge.py pause             # lua "dfhack.world.SetPauseState(true)"
python3 .agents/skills/scripts/df_bridge.py run prospect all  # any DFHack console command

bash .agents/skills/scripts/install_dfhack_linux.sh --dry-run # guided DFHack install (Steam app or tarball)
```

**READ** works without DFHack (it tails `gamelog.txt`); **WRITE** needs the game running
with DFHack — commands go through `dfhack-run` when available, with a stdlib-only TCP
client for the DFHack RPC protocol (localhost:5000) as fallback. `state` auto-installs a
bundled Lua script (`dfb-state.lua`) into the game's `dfhack-config/scripts/` and returns
JSON snapshots of the live save. Everything degrades to clear Portuguese errors and
distinct exit codes when the game is closed. The `df-adventure-live` skill builds a
narrating, advising **copilot** on top of these reads.

## Installation

### Via npm (recommended) — interactive installer

```bash
npm install -g dwarf-fortress-agent-skill
# or straight from GitHub (no registry needed):
npm install -g github:frederico-kluser/dwarf-fortress-agent-skill

cd your-project
df-skills            # interactive: pick your code agent with ↑/↓
```

The installer asks which code agent you use and sets everything up for it:

| Agent | What `df-skills` does |
|---|---|
| **Claude Code** | copies skills to `.claude/skills/` (or `~/.claude/skills/` with `--global`) |
| **pi** | copies skills to `.agents/skills/` (auto-discovered) |
| **Cursor** | copies skills + creates `.cursor/rules/dwarf-fortress-skills.mdc` router rule |
| **Codex (OpenAI)** | copies skills + appends a routed section to `AGENTS.md` (idempotent) |
| **Other / OpenRouter** | copies skills + writes a system-prompt snippet (`INSTRUCTIONS.md`) |

It rewrites the search paths inside each `SKILL.md` for the chosen target, and ends
by telling you the **initial skill** (`df-fortress-geral`) and the exact phrasing to
reference it in your agent.

Non-interactive: `df-skills --agent claude|pi|cursor|codex|generic [--global] [--force]`

### Manual (clone)

**pi / `.agents/skills` (automatic discovery):** just clone — no config needed.

**Claude Code / Cursor / Codex:**
```bash
cp -r .agents/skills/* ~/.claude/skills/        # or per-project: .claude/skills/
```

## Build pipeline (reproducible)

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt                 # requests, beautifulsoup4, lxml
# system deps: pandoc, sqlite3 (FTS5), ripgrep

python .work/crawl_api.py                        # live wiki API → .work/pages.jsonl (rendered HTML)
python .work/build_skills.py                     # HTML → clean Markdown → .agents/skills/*/references/
python .agents/skills/scripts/build_index.py     # build the FTS5 search index
```

`.work/pages.jsonl` (the rendered-HTML cache) is large and git-ignored; the crawl
regenerates it from the live wiki, so a clean clone rebuilds end-to-end.

`df-live-bridge` is hand-maintained (DFHack docs, not wiki) and intentionally absent
from `.work/taxonomy.py`; `build_skills.py` never deletes unknown skill dirs, so wiki
rebuilds preserve it — just re-run `build_index.py` after editing its references.

Pipeline modules in [`.work/`](.work):
`crawl_api.py` (API crawler) · `htmlmd.py` (HTML→Markdown) · `taxonomy.py`
(skill taxonomy + bilingual descriptions) · `build_skills.py` (assembler).

## Design decisions

- **API-rendered HTML, not a wikitext dump.** The only way to preserve infoboxes,
  token tables and `/raw` content; also keeps the content current (v50) instead of
  a 2023 snapshot. Templates are expanded server-side; offline parsers cannot do this.
- **SQLite FTS5, not RAG.** At a few thousand Markdown files, BM25 + stemming is
  sub-millisecond, deterministic, zero-infrastructure, and portable (stdlib only).
  Embeddings would add a model dependency and break portability for no benefit here.
- **Bilingual descriptions + query glossary.** Triggering and retrieval both work
  for Portuguese users over English content, without multilingual embeddings.
- **Split, don't truncate.** Articles over 48 KB are split into navigable
  `…-part-N.md` files with continuation notes — no content is silently dropped.
- **Subject-based routing.** Each page is assigned to one skill by its wiki
  categories (deterministic priority), with `/raw` pages routed to their topic
  skill (e.g. `Dog/raw` → `df-criaturas`), not dumped into modding.
- **Relevance-ranked indexes.** SKILL.md lists the top articles by inbound-link
  count (a popularity proxy); the rest are found via `search.py`.

## License

- **Wiki text** (references of the 13 wiki skills) — GFDL & MIT by Dwarf
  Fortress Wiki contributors. Every article links to its source page.
- **`df-live-bridge`** (hand-written references + bridge tooling) — MIT.
- **Build scripts & search tooling** — MIT License (see `LICENSE`).
- **Game-derived data** — © 2002–2026 Tarn Adams / Bay 12 Games.

This is an unofficial community resource, not affiliated with Bay 12 Games.
