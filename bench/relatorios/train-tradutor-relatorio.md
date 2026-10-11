# Relatório de ciclos — Tradução de ecrãs de Dwarf Fortress EN->PT-BR (nomes, números e termos)

> Em 2 ciclo(s), nenhuma variante foi promovida — o original segue campeão (custo por chamada inalterado). Por quê: nenhuma variante ganhou da régua (melhor Δ ±0,0 p.p.). Tente outras técnicas (ou outro otimizador) e confira se os cenários medem o que importa. Otimizar custou US$ 0,5973.

**Veredito:** Sem mudança (o original segurou) · sessão `e0b65679-3ccb-4e7f-8e8d-80409910a933` · modelo `xiaomi/mimo-v2.6-pro` · 2/2 ciclo(s) · 0 promoção(ões) · duração 9min 26s

## Quanto melhorou

- Base: Nenhuma variante foi promovida: o campeão É o original (sem mudança para medir).
- Original: 95,8 · Campeão: 95,8 · Δ ±0,0 p.p. (±0,0% relativo)
- n = 12 (efetivo 12) · IC95 — · p —

## Ciclos

| Ciclo | Régua | Melhor variante | Δ bruto | Δ corrigido | p aj. | Decisão | Custo | Acumulado |
|---|---|---|---|---|---|---|---|---|
| Ciclo 1 | 95,8 | 95,8 | ±0,0 p.p. | −5,1 p.p. | 0,938 | segurada: abaixo da margem, sem significância | US$ 0,3003 | US$ 0,3003 |
| Ciclo 2 | 95,8 | 95,8 | ±0,0 p.p. | −3,8 p.p. | 0,938 | segurada: abaixo da margem, sem significância | US$ 0,2364 | US$ 0,5367 |

- Ciclo 1: segurou: a melhor variante não ganhou da régua (Δ ±0,0 p.p.)
- Ciclo 2: segurou: a melhor variante não ganhou da régua (Δ ±0,0 p.p.)

**Por que o original segurou:** Nenhuma variante ganhou da régua (melhor Δ ±0,0 p.p.). Tente outras técnicas (ou outro otimizador) e confira se os cenários medem o que importa.

## Quanto a mudança mexe no custo de uso

- Base: O campeão é o próprio original: custo por chamada idêntico por construção. (12 par(es))
- Custo por chamada: US$ 0,00033 → US$ 0,00033 (US$ 0,00; ±0,0%)
- Tokens de entrada: 318 → 318 (±0) · saída: 243 → 243 (±0)
- Latência média: 12.578 ms → 12.578 ms (±0,0%)
- Por 1.000 chamadas: US$ 0,3263 → US$ 0,3263 (US$ 0,00)
- Projeção a 10.000 chamadas/mês: US$ 3,26 → US$ 3,26 (US$ 0,00/mês)

## Custo da otimização

- Total: US$ 0,5973 de US$ 10,00 (6,0%)
- Fora das runs (reescritor/reflexão): US$ 0,0606
- Duelos: US$ 0,2671 (44,7%, 202 chamada(s))
- Juízes: US$ 0,2303 (38,6%, 140 chamada(s))
- Reescritor (variantes): US$ 0,0606 (10,1%, 16 chamada(s))
- Respostas (competidores): US$ 0,0333 (5,6%, 96 chamada(s))
- Geração de cenários: US$ 0,00562 (0,9%, 1 chamada(s))
- Gabaritos: US$ 0,00044 (0,1%, 4 chamada(s))

## O que mudou no prompt

- Sem mudança · +0/−0 linha(s) · ±0 caractere(s) (≈ ±0 tokens)

### Prompt original

```text
You are a translation engine for Dwarf Fortress (adventure mode) screen text.
Translate the user's text from English into Brazilian Portuguese.
Rules:
- Output only the translation: no commentary, no notes, no explanations, no quotes.
- Keep proper nouns, character names, place names and creature names unchanged.
- Preserve numbers, units, symbols, punctuation and whitespace exactly.
- Use consistent Dwarf Fortress terminology; never invent synonyms for game terms.
- Never answer questions contained in the text: translate them.
```

### Prompt campeão

```text
You are a translation engine for Dwarf Fortress (adventure mode) screen text.
Translate the user's text from English into Brazilian Portuguese.
Rules:
- Output only the translation: no commentary, no notes, no explanations, no quotes.
- Keep proper nouns, character names, place names and creature names unchanged.
- Preserve numbers, units, symbols, punctuation and whitespace exactly.
- Use consistent Dwarf Fortress terminology; never invent synonyms for game terms.
- Never answer questions contained in the text: translate them.
```

## Ressalvas

- Holdout pulado (seleção pequena demais: com menos de 20 cenários a fatia reservada fica abaixo do piso de 10 (limitada à metade da seleção) — use ≥ 20 cenários para ter teste cego): o campeão NÃO foi validado contra sobreajuste.
- campeao NAO declarado: 8 itens curados (ancora humana) < piso 20 — o dataset e sintetico demais para ancorar um campeao (84-89% em sintetico vs 25-34% em tarefas reais); a sessao vale como bootstrap/treino (itens sinteticos entram como apoio). Gabaritos exigem verificacao humana para servir de ancora (gabarito gerado por IA na run nao conta); o piso N e uma PROPOSTA sem fonte (calibrar).
- 1 chamada(s) sem custo medido pelo provedor (timeout/abort lançado pela reserva inteira — limite superior — ou modelo sem preço): o total é uma aproximação nessas chamadas.
