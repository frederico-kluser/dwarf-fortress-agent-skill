# Auditoria técnica — dwarf-fortress-agent-skill

**Data:** 2026-06-11
**Escopo:** sistema de Agent Skills gerado da wiki do Dwarf Fortress (13 skills flat + 1 dispatcher, retrieval via ripgrep, sem RAG/embeddings).
**Método:** pesquisa de fontes primárias 2025-2026 (P1-P6) executada antes da inspeção; inspeção do repositório real (V1-V9) com amostragem e execução de scripts. Toda crítica tem evidência: citação de fonte **ou** path + trecho do repo. Afirmações não comprováveis estão marcadas como **NÃO VERIFICADO**.

---

## 1. Sumário executivo

O **esqueleto arquitetural está certo** — estrutura flat de 13 skills (dentro do orçamento de discovery do Claude Code), progressive disclosure com `references/`, e retrieval lexical com ripgrep são todos escolhas alinhadas com as melhores práticas atuais da Anthropic e da spec agentskills.io. O problema é a **qualidade dos dados e a camada de busca**, onde há falhas graves e, em parte, bloqueantes: a maior skill (`df-modding`, 51% do corpus) é **95% stubs vazios**; **todas as tabelas de token** (o coração de uma skill de modding) foram destruídas e viraram o literal `[TABLE]`; **~485 de 489** artigos de criatura perderam o stat block (infobox); **~1.173 páginas** foram descartadas, incluindo conteúdo fundamental (Mining, Adamantine, Water, Magma); e o `search.sh` — única ponte do agente para o conteúdo — **retorna zero resultados** para queries multi-palavra ("how to make steel") e para qualquer query em português, justamente o público-alvo. Além disso, o rebuild **não é reproduzível** a partir de um clone limpo. **Conclusão: o sistema NÃO está pronto para uso real sem as correções do TOP 5.**

---

## 2. Vereditos das decisões (D1-D10)

| # | Decisão | Veredito | Evidência (repo) | Fonte (pesquisa) |
|---|---|---|---|---|
| D1 | Não usar RAG/embeddings; ripgrep puro | **ACEITÁVEL** (decisão) / **SUBÓTIMA** (implementação) | `search.sh` casa a query como frase contígua; `"how to make steel"` → 0 hits (execução) | Boris Cherny (Claude Code) via Latent Space: agentic search "outperformed everything. By a lot"; docs oficiais usam `grep -i` sobre `reference/`. Mas Nuss-and-Bolts e Milvus documentam falhas do grep puro (sem stemming, sinônimo, ranking, cross-língua). Upgrade leve = SQLite FTS5/BM25. |
| D2 | Estrutura flat, 13 skills | **ÓTIMA** | 14 `SKILL.md` flat, nomes únicos, descriptions 212-351 chars | Budget de listing do Claude Code trunca ~15-25 skills num modelo 200K; docs oficiais desenham para "100+ available Skills"; superpowers (obra) ships ~14; "Pattern 2: domain-specific organization" é exatamente esta forma. |
| D3 | Descriptions em PT sobre conteúdo EN | **SUBÓTIMA** | 100% das descriptions em português; jargão DF é inglês ("aquifer", "strange mood", "tantrum spiral") | Sem regra oficial de idioma, mas o trigger é o modelo lendo keywords no system prompt. SmartScope e vercel-labs/agent-browser#95: incluir as palavras que o usuário realmente digita. Spec: "Should include specific keywords that help agents identify relevant tasks." |
| D4 | Índice de 30 entradas ordenado por TAMANHO | **ERRADA** | `ref_entries.sort(key=lambda x: -x[2])` (`build_skills.py:249`); `df-criaturas/SKILL.md` indexa "Dwarf Fortress RPG", webcomics, dev interviews; troll/cat/dragon/hydra invisíveis | Tamanho ≠ relevância. Hedgineer: índice/descrição "keyword-first" pela utilidade ao usuário, não pelo volume. |
| D5 | Truncar a 48 KB em vez de dividir | **ERRADA** | 14 guias longos cortados ~50%; aviso linka para a web (`build_skills.py:163-173`) | Anthropic: "Bundle comprehensive resources… no context penalty until accessed"; "split its content into separate files"; "if you choose to truncate responses, be sure to steer agents with helpful instructions"; TOC em refs >100 linhas. |
| D6 | First-match (1 página → 1 skill) | **SUBÓTIMA** | iteração sobre `set` (`build_skills.py:61-64`) → desempate por hash, não por prioridade; fallback de keyword: Bloodstone/Blood amaranth → df-combate ("blood"), FAQ → df-adventure ("quest"), Dwarf Fortress → df-criaturas ("dwarf") | Pattern 2 admite multi-domínio via `references/`; desempate deveria ser determinístico e declarado. |
| D7 | Dump nov/2023 (v50 incompleto + merge v0.47) vs crawl vivo | **SUBÓTIMA** | 1.102 das 1.173 páginas descartadas tinham 0 categorias (perda na migração v50); Mining, Adamantine, Water, Magma ausentes | Anthropic: evitar conteúdo time-sensitive sem datar. wikiteam3 permite crawl fresco da wiki viva; spec recomenda versionar via `metadata` + git. |
| D8 | Merge: preferir a versão MAIOR em bytes | **SUBÓTIMA** (frequência **NÃO VERIFICADO**) | `if prev is None or rec["bytes"] > prev["bytes"]` (`build_skills.py:39`) | Tamanho ≠ atualidade; pode escolher mecânica v0.47 sobre a v50 correta. Frequência real do erro não foi medida (exigiria diff página a página). |
| D9 | Dispatcher raiz mantido com discovery flat | **SUBÓTIMA / redundante** | `name: dwarf-fortress`, description "Use SEMPRE que o jogador perguntar qualquer coisa" (`build_dispatchers.py:68`) — compete como peer flat das 13 específicas | Discovery é flat e recursivo; um catch-all guloso arrisca over-trigger (curto-circuitando a skill específica correta) e/ou gasta budget de listing sem agregar roteamento real. |
| D10 | Nenhum eval de triggering automatizado | **SUBÓTIMA** (lacuna) | nenhum eval no repo; a V1 fez 10 testes de *search*, não de *trigger* de description | skill-creator: ≥20 queries, cada uma 3x, incluindo near-miss negativos; "triggering accuracy lives or dies in the description field". Ferramentas: `skills-ref validate`, skill-bench, pulser. |

---

## 3. Achados da inspeção (V1-V9)

### V1 — Frontmatter
Estruturalmente OK: 14 `SKILL.md`, todos com `name` válido (lowercase + hífen, ≤64 chars, sem colisão) e `description` entre 212 e 351 chars (≤1024 da spec), todas YAML-safe (0 `:` não-quotado — a correção principal sobre a V1 funcionou). **Lacunas:** nenhum SKILL.md tem `metadata` (faltam versão e data do dump, recomendados para conteúdo derivado/datado) nem `license`. Todas as descriptions são **somente em português** (ver D3).

### V2 — Qualidade de conversão: **perda sistêmica de conteúdo**
Wikitext bruto está majoritariamente limpo (0 ocorrências de `{{`, `<ref>`, entidades HTML no corpus), mas essa limpeza foi obtida **deletando o conteúdo dos templates**, não expandindo-os. Causa raiz em `.work/build_skills.py:152`:
```python
md = re.sub(r"\{\{[^{}]*\}\}", "", md)   # apaga TODO template não-expandido pelo pandoc
```
Como o pandoc não expande templates MediaWiki, infoboxes, marcações de tecla `{{k|d}}` e referências de token `{{token|...}}` são silenciosamente apagados. Danos concretos:

- **124 arquivos com `[TABLE]` literal** (pandoc não converteu tabelas complexas), incluindo o núcleo de df-modding: `df-modding/references/creature-token.md`, `weapon-token.md`, `body-token.md`, `material-definition-token.md`, `entity-token.md`, `plant-token.md`, `reaction.md`. As páginas de referência de token — razão de existir da skill de modding — estão gutadas.
- **Frases mutiladas** por deleção de `{{k|x}}`/`{{token|…}}`:
  - `df-interface/references/announcement.md`: "some (but not all) can be **oomed** to" e "not to be confused with **eports**" (`{{k|z}}oomed`, `{{k|r}}eports`).
  - `df-combate/references/battle-axe.md`: "(**esignate rees**)" (era `({{k|d}}esignate {{k|t}}rees)`).
  - `df-criaturas/references/learning.md`: "such as **, , , , or ,**" e "`[CAN_LEARN]` **and are implied by .**" — fatos-chave perdidos.
- **Infoboxes perdidas:** só **3 de 489** arquivos de df-criaturas mencionam "Pet value" (e são páginas-lista). ~485 artigos perderam o stat block inteiro (tile, body size, max age, butcher returns, flags). Idêntico para armas (`battle-axe.md` sem dados de tamanho/contact-area) e tabelas de valor de pedra/gema.
- **Artigos esvaziados:** 18 arquivos de df-criaturas têm <300 bytes; `df-criaturas/references/giant-walrus.md` é, na íntegra, só título + link de fonte.

### V3 — Taxonomia: df-modding é um junk drawer
**1.376 dos 1.452** arquivos de df-modding terminam em `-raw.md`, varridos pela primeira regra de `assign_skill` (`build_skills.py:59`):
```python
if title.endswith("/raw") or title.endswith(" raw"):
    return "df-modding"
```
Amostra de títulos: `sunstone-raw`, `giant-bobcat-raw`, `dragon-raw`, `tin-raw`, `quinoa-raw` — são subpáginas de raw de criatura/pedra/planta, não artigos de modding. Pior: **1.379 dos 1.452** têm <300 bytes (a transclusão de raw via template nunca expandiu), p.ex. `df-modding/references/tin-raw.md` = título + link. **95% da maior skill é peso morto** que polui o `search.sh`, enquanto os ~73 artigos reais de modding estão gutados por `[TABLE]` (V2).

O índice de `df-criaturas/SKILL.md` (30 de 489, ~6%) é ordenado por tamanho e gasta 5+ slots em páginas meta/comunidade (Dwarf Fortress RPG, webcomics, developer interviews, Kobold language) enquanto alvos comuns de query (troll, giant, cat, dog, hydra, minotaur, roc, bronze colossus) ficam invisíveis.

### V4 — Páginas descartadas (~1.173)
Reconstruído de `.work/page_cats.json` diffado contra os arquivos: 1.173 páginas canônicas sem arquivo; **1.102 tinham 0 categorias** no dump (páginas v50 perderam tags na migração). Casualidades de alto valor confirmadas ausentes:
- **Mining** (`'Mining': []`) — talvez o artigo mais fundamental de Fortress Mode. AUSENTE.
- **Adamantine** (`'Adamantine': []`) — o material mais famoso do jogo. AUSENTE (existe só `raw-adamantine`, a pedra).
- **Water, Magma** (categoria `Physics`) — e todo o conjunto Physics (38 páginas): **Cave-in, Pressure, Fire, Density, Flow, Temperature**. A categoria `Physics` não é mapeada por nenhuma skill nem por `DROP_CATS`. Para um jogo sobre inundar fortalezas com magma, perder Water/Magma/Pressure/Cave-in é buraco crítico.
- **Well, Fishing, Tunnel, Butcher, Anatomy** — 0 categorias, ausentes.
- Inconsistência: `Language` está em `DROP_CATS`, mas `df-modding/references/elvish-language-raw.md` e `df-criaturas/references/kobold-language.md` entraram mesmo assim.
- Nota: `DROP_CATS` é **importado mas nunca usado** em `build_skills.py` (só no import, `linha 15`); o descarte acontece por não-cobertura, por isso páginas de descarte intencional (Dwarf Fortress RPG, categoria Humor) vazaram para df-criaturas via fallback de keyword.

Descartes defensáveis: Humor and stories, Community, Game development, Errors, Main Page, ahk scripts — genuinamente meta.

### V5 — First-match
Mecanismo em `build_skills.py:61-64`: itera sobre um `set` de categorias e retorna a primeira skill mapeada. Dois problemas: (a) iteração sobre `set` → quando categorias mapeiam para skills diferentes, o vencedor é ordem de hash, não prioridade declarada; (b) sem categoria, cai no fallback de substring de keyword no título. Casos de fronteira amostrados:
- **Vampire** → df-criaturas (defensável, mas categorias `No Pain/No Stun/No Exert` mapeiam para df-combate; sorte do set decidiu).
- **Werebeast** → df-criaturas via keyword "beast" (0 categorias; certo por acaso).
- **Bloodstone** (gema) e **Blood amaranth** (planta) → **df-combate** via keyword "blood" — **errado**.
- **Frequently Asked Questions** → **df-adventure** via keyword "quest" (substring de "Questions") — **errado**.
- **Dwarf Fortress** (artigo do jogo) → df-criaturas via keyword "dwarf" — **errado**.

Conclusão: hits por categoria geralmente são defensáveis; o **fallback de keyword é uma máquina de misassignment**, e empates entre skills são resolvidos de forma não-determinística.

### V6 — search.sh (execução em 2026-06-11)
A busca é a única ponte do agente para o conteúdo, e está **quebrada para o caso de uso central**:
- `search.sh df-materiais "steel"` → funciona (1 palavra; `steel.md:35`).
- `search.sh df-materiais "aço"` → **"Nenhum resultado"** (conteúdo é inglês).
- `search.sh all "how to make steel"` → **0 resultados**.
- `search.sh all "como fazer aço"` → **0 resultados**.
- `search.sh all "magma forge"` → funciona só porque o bigrama existe textualmente.

Causa em `build_dispatchers.py` (`rg -c -i ... "$QUERY"`): a query inteira vira **um único regex contíguo**, então só casa frases literais — sem AND entre termos, sem stemming, sem cross-língua. Queries naturais multi-palavra e qualquer pergunta em português falham. Como o público-alvo pergunta em PT e o conteúdo é EN, o fluxo principal quebra.

### V7 — Truncamentos
14 arquivos truncados em ~48 KB, e são justamente os **guias longos de maior valor**: `df-fortress-geral/references/quickstart-guide.md`, `starting-build.md`, `megaproject.md`, `minecart.md`; `df-combate/references/defense-guide.md`, `military-testing.md`, `weapon.md`; `df-geologia/references/advanced-world-generation.md`, `worldgen-examples.md`, `planepacked.md`; `df-saude/references/cheating.md`. O aviso de truncamento linka para a web — inútil, pois o agente não navega por padrão. Perde-se ~50% de cada guia sem caminho de recuperação.

### V8 — Dispatcher raiz
`name: dwarf-fortress` com description "Use SEMPRE… qualquer coisa sobre Dwarf Fortress" é, no discovery flat, um **peer** das 13 skills específicas — não um pai. Um catch-all guloso assim tende a over-trigger e curto-circuitar a skill específica certa, ou a ser ruído que consome budget de listing. Redundante.

### V9 — Reprodutibilidade
**Rebuild não roda num clone limpo.** `build_skills.py:31` lê `.work/pages.jsonl`, mas `pages.jsonl` está no `.gitignore` (`.work/*.jsonl`) e **ausente** do repositório. O XML do dump também é gitignored. Não há `requirements.txt` — as dependências (`pandoc`, `zstd`, `ripgrep`, `mwparserfromhell`, `mwxml`, `pyyaml`) só aparecem em prosa no README. Para reconstruir é preciso re-baixar o dump do Internet Archive e re-rodar `parse_dump.py` manualmente; o pipeline incremental está quebrado.

---

## 4. TOP 5 melhorias priorizadas (impacto/esforço)

1. **[ALTO impacto] Recuperar tabelas de token e infoboxes** — pare de apagar templates às cegas. Em `.work/build_skills.py`, antes de chamar o pandoc, expanda/serialize infoboxes e token-tables (converta linhas `{{ct|…}}`/templates de tabela em pipe-table GFM) em vez do `re.sub(r"\{\{[^{}]*\}\}", "", md)` da linha 152 e do pandoc emitir `[TABLE]`. Re-rodar o build. Recupera o conteúdo central de df-modding e os stat blocks de ~485 criaturas.
2. **[ALTO impacto / BAIXO esforço] Consertar `search.sh`** — em `.work/build_dispatchers.py` (string `search_sh`): tokenizar a query e exigir todos os termos (loop de `rg`/`rg -e t1 -e t2` com AND), aplicar stemming simples, e **avisar que o conteúdo é inglês** (mapa PT→EN mínimo para o jargão DF, ou instrução para o agente consultar em inglês). Re-gerar o script. Conserta o fluxo principal do público-alvo.
3. **[ALTO impacto] Desfazer o junk drawer df-modding** — em `assign_skill` (`build_skills.py:58-71`): não rotear stubs `/raw` vazios (<300 bytes) para df-modding; roteá-los à skill do tema-pai (`creature-raw`→df-criaturas, `*-stone-raw`→df-materiais) ou descartá-los. Remove ~1.379 stubs que poluem a busca.
4. **[MÉDIO] Dividir em vez de truncar** — substituir `maybe_truncate` (`build_skills.py:163`) por split em `part-1.md`/`part-2.md` com TOC e entradas no índice. Atinge os 14 guias longos e elimina a perda de ~50%.
5. **[MÉDIO] Resgatar páginas descartadas valiosas** — em `.work/taxonomy.py`: mapear a categoria `Physics` (Water/Magma/Pressure/Cave-in/Temperature) e adicionar rede de keyword/título para páginas sem categoria (Mining, Adamantine, Well, Fishing, Butcher, Anatomy). Re-rodar. Fecha buracos de cobertura fundamentais.

**Itens menores recomendados:** descriptions **bilíngues** (PT + keywords EN); **remover** o dispatcher raiz (ou torná-lo não-disparável); adicionar `metadata: { source-dump: "2023-11", version }` ao frontmatter; ordenar o índice por relevância (não por tamanho); commitar `pages.jsonl` **ou** publicar `requirements.txt` + passos de rebuild; adicionar eval de triggering (skill-bench / harness custom) com queries EN **e** PT; rodar `skills-ref validate` em CI.

---

## 5. Riscos aceitos conscientemente

- **Manter ripgrep** (em vez de FTS5/embeddings) é **aceitável** — desde que o `search.sh` seja consertado (item 2). O consenso 2025-2026 favorece grep-in-loop, e o corpus (~2,8k docs) é pequeno. FTS5/BM25 fica como upgrade opcional se evals mostrarem misses.
- **13 skills flat:** ótimo, não mexer (D2).
- **Um modo (fortress/adventure) por skill** com o modo indicado na description: aceitável.
- **Heurística "maior em bytes" no merge (D8):** subótima mas de baixo risco prático — aceitável no curto prazo como dívida técnica documentada; a frequência real do erro permanece **NÃO VERIFICADA**.

---

## 6. Apêndice — URLs consultadas (acesso 2026-06-11)

**Spec e docs oficiais**
- https://agentskills.io/specification
- https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills
- https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
- https://github.com/anthropics/skills (skill-creator/SKILL.md, scripts/quick_validate.py, issue #338)
- https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- https://www.anthropic.com/engineering/writing-tools-for-agents
- https://code.claude.com/docs/en/skills

**Granularidade, catálogo e budget de skills**
- https://github.com/obra/superpowers
- https://blog.fsck.com/2025/10/09/superpowers/
- https://www.hedgineer.io/content/claude-skills-knowledge-layer/
- https://claudefa.st/blog/guide/mechanics/skill-listing-budget
- https://gist.github.com/alexey-pelykh/faa3c304f731d6a962efc5fa2a43abe1
- https://github.com/anthropics/claude-code/issues/56710
- https://github.com/anthropics/claude-code/issues/57941
- https://github.com/anthropics/claude-code/issues/13343

**Idioma da description / triggering**
- https://smartscope.blog/en/blog/agent-skills-description-guide/
- https://github.com/vercel-labs/agent-browser/issues/95
- https://www.theadpharm.com/insights/claude-code-skills-opus-4-7-playbook

**Lexical vs embeddings / retrieval**
- https://vadim.blog/claude-code-no-indexing/
- https://rust-trends.com/posts/ripgrep-claude-code/
- https://www.nuss-and-bolts.com/p/on-the-lost-nuance-of-grep-vs-semantic
- https://milvus.io/blog/why-im-against-claude-codes-grep-only-retrieval-it-just-burns-too-many-tokens.md
- https://yage.ai/share/why-coding-agents-still-use-grep-en-20260327.html
- https://sqlite.org/fts5.html
- https://docs.openclaw.ai/concepts/memory-builtin
- https://towardsdatascience.com/memweave-zero-infra-ai-agent-memory-with-markdown-and-sqlite-no-vector-database-required/
- https://github.com/quickwit-oss/tantivy
- https://rag-engine.cloud/blog/hybrid-search-bm25-plus-vectors

**Manutenção / CI / validação / evals**
- https://github.blog/changelog/2026-04-16-manage-agent-skills-with-github-cli/
- https://dev.to/thestack_ai/testing-claude-code-skills-in-ci-pulser-eval-github-action-3na9
- https://github.com/marketplace/actions/skill-eval
- https://blog.mgechev.com/2026/02/26/skill-eval/
