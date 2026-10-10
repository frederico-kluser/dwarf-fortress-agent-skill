"""Cadeia de menus ``A_ATTACK`` (DF 53.16) como máquina de estados PURA — sem I/O.

Porquê: o *bump-attack* do executor só ataca **hostis**; criaturas neutras/tame
(a mula provada ao vivo, corrida ``caca-2``) ignoram o passo para o tile. O
único mecanismo comprovado contra elas é o ataque manual por menus (padrão
``p-attack-menu-kill``, ``docs/padroes-navegacao.json`` →
``combat.attack_menu_chain``)::

    key A_ATTACK → [overlay "Okay"] → "Who will you attack?" (linha do alvo)
      → "Really start a conflict with …?" (Confirm) → planner "a Strike"
      → "What do you want to aim for?" (neck / head / …)
      → "Attack <parte>:" (stab / bash / strike / punch) → [modificadores] → "Done"

Aqui essa cadeia é uma função pura **ecrã → próximo passo**: lê o ``screen`` da
ponte (``{"focus": [...], "lines": [...]}``) ou texto simples, decide o estágio
(:func:`detect_stage`) e devolve UM passo (:func:`next_step`) que :func:`to_dsl`
converte em DSL do ``df_bridge``. Quem executa é o executor; nada aqui faz I/O.

Segurança (permissivo a casar, estrito a agir; na dúvida ``unknown``):

* o ecrã é **dado**: controlo/bidi viram espaço (preserva as colunas), linhas e
  colunas têm teto e **nada do ecrã entra no DSL** — só inteiros e teclas de
  :data:`ALLOWED_KEYS`; com o mapa em foco (``dungeonmode/Default``) o texto é
  ignorado (um "Who will you attack?" num diálogo não arma a cadeia);
* o alvo só é selecionado se a linha tiver **a mesma espécie** (``The stray small
  reindeer cow`` ≡ ``Reindeer Cow (tame)``; ``wild boar man`` ≢ ``wild boar``) e o
  prompt "Really start a conflict with …?" nomear essa criatura — senão ``abort``
  com ``LEAVESCREEN``; **sapientes nunca são alvo** (:func:`looks_sapient`);
* "Confirm" só é clicado com ``confirmed=True`` (consentimento do chamador: ação
  libertada pela banda ``auto`` ou aprovada por humano); sem ele o passo é
  ``wait`` (``needs_confirmation``) e o jogo espera no prompt (turnos por input);
* ``unknown`` mais de :data:`DEFAULT_MAX_UNKNOWN` vezes, o mesmo passo sobre o
  MESMO ecrã (clique sem efeito) ou orçamento de passos esgotado ⇒ ``abort``;
  recolher dos menus é UM ``LEAVESCREEN`` de cada vez (:func:`unwind_step`)
  porque no mapa ``LEAVESCREEN`` abre o menu do jogo.

Decisões: ``confirmed`` é consentimento (não progresso); aceitam-se os DOIS
fluxos do passo final (o clique no tipo de ataque executa o golpe — observado — ou
exige "Done" — doc); sem entrada de golpe utilizável (arma ``lodged``: ao vivo
faltavam ``stab``, ``bash`` e ``punch``) é ``abort`` ``no_attack_type`` em vez de
"Done" às cegas (6 voltas inúteis na prova); recuperar a arma é política
(``agent/combat_policy.py``).

Verificar ao vivo (geometria real não capturável offline; os testes reproduzem os
cliques da corrida ``caca-2``: Okay@121,35 · "a The stray mule"@62,51 ·
Confirm@47,65 · "a Strike"@45,58 · "a upper body"@51,34 · "a stab/iron
spear"@53,34 · Done@136,31): foco de cada estágio (assumido ``dungeonmode/Attack``),
redação exata dos cabeçalhos, se os modificadores vivem no ecrã do tipo de ataque
e se "Done" é necessário.
"""
from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Mapping, NamedTuple, Sequence

__all__ = [
    "ACTIONS", "ACTION_ABORT", "ACTION_CLICK", "ACTION_DONE", "ACTION_KEY", "ACTION_WAIT",
    "AIM_ALIASES", "AIM_FALLBACKS", "AIM_SPAN", "ALLOWED_KEYS", "ATTACK_TYPE_SPAN",
    "ATTACK_VERBS", "DEFAULT_AIM", "DEFAULT_ATTACK_FALLBACK", "DEFAULT_ATTACK_PREF",
    "DEFAULT_MAX_REPEAT", "DEFAULT_MAX_STEPS", "DEFAULT_MAX_UNKNOWN", "DEFAULT_MAX_UNWIND",
    "DEFAULT_SETTLE_MS", "KEY_ATTACK", "KEY_LEAVESCREEN", "MAX_COORD", "MAX_LINE_CHARS",
    "MAX_REOPEN", "MAX_SCREEN_LINES", "MAX_SETTLE_MS", "MODIFIER_NAMES", "SAPIENT_WORDS",
    "STAGES", "STAGE_AIM", "STAGE_ATTACK_TYPE", "STAGE_CONFIRM", "STAGE_DONE",
    "STAGE_HELP_OVERLAY", "STAGE_MODIFIERS", "STAGE_PLANNER", "STAGE_START", "STAGE_UNKNOWN",
    "STAGE_WHO", "WHO_SPAN", "AttackChain", "AttackOption", "ChainStep", "LabelMatch",
    "Reason", "ScreenView", "canonical_aim", "detect_stage", "find_label", "find_label_match",
    "list_attack_options", "looks_sapient", "next_step", "open_menu_step",
    "parse_attack_label", "parse_screen", "screen_from_report", "species_words",
    "target_matches", "to_dsl", "unwind_step",
]

# ============================================================== vocabulário

STAGE_START = "start"  # rótulo do passo de abertura (``key A_ATTACK``); não é um ecrã
STAGE_HELP_OVERLAY = "help_overlay"
STAGE_WHO = "who_will_you_attack"
STAGE_CONFIRM = "confirm_conflict"
STAGE_PLANNER = "planner"
STAGE_AIM = "aim_menu"
STAGE_ATTACK_TYPE = "attack_type"
STAGE_MODIFIERS = "modifiers"
STAGE_DONE = "done"
STAGE_UNKNOWN = "unknown"
#: Estágios que :func:`detect_stage` pode devolver.
STAGES: tuple[str, ...] = (
    STAGE_HELP_OVERLAY, STAGE_WHO, STAGE_CONFIRM, STAGE_PLANNER, STAGE_AIM,
    STAGE_ATTACK_TYPE, STAGE_MODIFIERS, STAGE_DONE, STAGE_UNKNOWN,
)

ACTION_CLICK, ACTION_KEY, ACTION_WAIT = "click", "key", "wait"
ACTION_DONE, ACTION_ABORT = "done", "abort"
ACTIONS: tuple[str, ...] = (ACTION_CLICK, ACTION_KEY, ACTION_WAIT, ACTION_DONE, ACTION_ABORT)

KEY_LEAVESCREEN = "LEAVESCREEN"
KEY_ATTACK = "A_ATTACK"
#: As ÚNICAS teclas que a cadeia emite (nada vem do ecrã para o DSL).
ALLOWED_KEYS: frozenset[str] = frozenset({KEY_LEAVESCREEN, KEY_ATTACK})


class Reason:
    """Códigos de razão estáveis de ``ChainStep.reason`` (match por estes)."""

    # passos normais
    OPEN_MENU = "open_attack_menu"
    HELP_DISMISSED = "help_dismissed"
    HELP_NEXT = "help_next"
    TARGET_SELECTED = "target_selected"
    CONFLICT_CONFIRMED = "conflict_confirmed"
    NEEDS_CONFIRMATION = "needs_confirmation"
    STRIKE_SELECTED = "strike_selected"
    AIM_SELECTED = "aim_selected"
    AIM_FALLBACK = "aim_fallback"
    ATTACK_SELECTED = "attack_selected"  # + ":<verbo>"
    ATTACK_FALLBACK = "attack_fallback"  # + ":<verbo>"
    MODIFIER = "modifier"  # + ":<nome>"
    DONE_CLICKED = "done_clicked"
    WAIT_UNKNOWN = "waiting_unknown_screen"
    WAIT_GAME = "waiting_for_game"
    STRIKE_COMMITTED = "strike_committed"
    NO_MENU = "no_menu_open"
    UNWIND = "unwind"
    # abortos
    NO_TARGET = "no_target"
    SAPIENT_TARGET = "sapient_target"
    TARGET_NOT_LISTED = "target_not_listed"
    CONFLICT_MISMATCH = "conflict_target_mismatch"
    CONFLICT_UNREADABLE = "conflict_target_unreadable"
    NO_CONFIRM_BUTTON = "no_confirm_button"
    NO_STRIKE = "no_strike_option"
    AIM_UNAVAILABLE = "aim_not_available"
    NO_ATTACK_TYPE = "no_attack_type"
    HELP_NO_BUTTON = "help_overlay_without_button"
    UNKNOWN_SCREEN = "unknown_screen"
    STALLED = "stalled"
    STEP_BUDGET = "step_budget"
    MENU_NOT_OPEN = "menu_did_not_open"
    NO_STRIKE_COMMITTED = "returned_to_map_without_strike"
    INTERNAL_ERROR = "internal_error"


# --------------------------------------------------------------------- limites
# Tudo nomeado: o orquestrador afina estes números sem tocar na lógica.

MAX_SCREEN_LINES, MAX_LINE_CHARS = 160, 400  # janela real: 72 linhas × 182 colunas
MAX_FOCUS_ITEMS, MAX_FOCUS_CHARS, MAX_PARSE_DEPTH = 8, 200, 4
MAX_PAYLOAD_CHARS = 262_144  # um ecrã real tem ~13 KB
MAX_COORD, MAX_LABEL_CHARS, MAX_DETAIL_CHARS, MAX_HISTORY = 1000, 60, 120, 64
#: Ecrãs ``unknown`` seguidos tolerados ("mais de N" aborta).
DEFAULT_MAX_UNKNOWN = 3
#: Mesmo passo sobre o MESMO ecrã tolerado N vezes (clique sem efeito).
DEFAULT_MAX_REPEAT = 3
#: Passos por cadeia (a prova real fez 6-7 cliques por golpe).
DEFAULT_MAX_STEPS = 16
#: ``LEAVESCREEN`` máximos ao recolher (no mapa abre o menu do jogo).
DEFAULT_MAX_UNWIND = 4
#: Reaberturas quando o overlay de ajuda engoliu o ``A_ATTACK``.
MAX_REOPEN = 1
#: Espera (ms do DSL) para o ecrã assentar depois de um passo.
DEFAULT_SETTLE_MS, MAX_SETTLE_MS = 500, 10_000
#: Linhas abaixo do cabeçalho onde se procuram as entradas de cada menu.
WHO_SPAN, AIM_SPAN, ATTACK_TYPE_SPAN = 30, 40, 24

#: Mira por omissão: ``d head`` mata ("has been struck down"); ``c neck`` paralisa.
DEFAULT_AIM = "head"
#: Preferência de golpe por omissão (a política real vem de ``combat_policy``).
DEFAULT_ATTACK_PREF: tuple[str, ...] = ("stab", "bash", "strike", "punch")
#: Verbos de golpe do menu "Attack <parte>:"; agarrar/lançar/… nunca são clicados.
ATTACK_VERBS: tuple[str, ...] = (
    "stab", "slash", "hack", "chop", "cut", "pierce", "bash", "smash", "strike",
    "punch", "kick", "bite", "gore", "scratch", "pummel", "batter", "whip", "lash",
)
#: Ordem de recurso quando nada da preferência pedida existe (arma presa, menu
#: curto): primeiro a preferência por omissão, depois os outros verbos conhecidos.
DEFAULT_ATTACK_FALLBACK: tuple[str, ...] = DEFAULT_ATTACK_PREF + tuple(
    verb for verb in ATTACK_VERBS if verb not in DEFAULT_ATTACK_PREF)
#: Modificadores da doc (``u Quick`` … ``z Multi-attack``) e os rótulos aceites.
MODIFIER_NAMES: tuple[str, ...] = ("quick", "heavy", "wild", "precise", "multi")
_MODIFIER_LABELS: Mapping[str, str] = {
    **{name: name for name in MODIFIER_NAMES},
    "multi-attack": "multi", "multi attack": "multi", "multiattack": "multi",
}
#: Aliases de mira (texto livre → nome da parte no menu).
AIM_ALIASES: Mapping[str, str] = {
    "torso": "upper body", "chest": "upper body", "body": "upper body",
    "trunk": "upper body", "belly": "lower body", "abdomen": "lower body",
    "stomach": "lower body", "skull": "head", "face": "head", "throat": "neck",
    "legs": "leg", "arms": "arm", "hooves": "hoof", "feet": "foot", "hands": "hand",
}
#: Recurso quando a parte pedida não está no menu (nunca uma perna sozinha).
AIM_FALLBACKS: Mapping[str, tuple[str, ...]] = {
    "head": ("neck", "upper body"), "neck": ("head", "upper body"),
    "upper body": ("lower body",), "lower body": ("upper body",),
    "leg": ("lower body", "upper body"), "arm": ("upper body",),
}
#: Partes de membros onde o menu antepõe lado/posição ("left front leg").
_LIMB_AIMS = frozenset({"leg", "arm", "hand", "foot", "hoof", "wing", "tail", "ear", "eye"})

#: Raças **sapientes** — duplicado de ``agent/candidates.py::SAPIENT_WORDS`` (evita
#: ciclo de import; manter as duas listas iguais).
SAPIENT_WORDS: tuple[str, ...] = (
    "human", "dwarf", "elf", "goblin", "hobgoblin", "kobold", "orc", "troll",
    "gnome", "gremlin", "necromancer", "vampire", "ghost", "demon", "devil",
    "werebeast", "were-", "nightwraith", "phantom", "siren", "merperson",
)
#: Tokens que denunciam PESSOA: "animal people" (``boar man``), títulos e
#: profissões vistos ao vivo ("Swordsman", "lady").
_PERSON_TOKENS: frozenset[str] = frozenset({
    "man", "men", "woman", "women", "person", "people", "lady", "lord", "king",
    "queen", "baron", "baroness", "duke", "duchess", "count", "countess", "mayor",
    "sheriff", "captain", "militia", "swordsman", "bowman", "lasher", "hammerman",
    "spearman", "pikeman", "axeman", "crossbowman", "maceman", "wrestler", "thief",
    "merchant", "peasant", "monk", "priest", "mercenary", "child",
})
#: Palavras que NÃO identificam a espécie (sexo/idade/estado): variam por exemplar.
_MODIFIER_WORDS: frozenset[str] = frozenset({
    "stray", "tame", "wild", "domestic", "small", "young", "old", "baby", "juvenile",
    "adult", "male", "female", "buck", "doe", "cow", "bull", "ram", "ewe", "stallion",
    "mare", "boar", "sow", "rooster", "hen", "gelding", "steer", "calf", "foal",
    "lamb", "kid", "piglet", "cub", "pup", "puppy", "kitten", "chick", "fawn", "tom",
    "war", "pack",
})
_ARTICLES: frozenset[str] = frozenset({"the", "a", "an"})

# ======================================================= marcadores (regex)
# Frases EXATAS da doc / prova ao vivo; sempre por linha (nunca atravessam quebras).

_WORD = r"[A-Za-z](?:[A-Za-z'’\-]*[A-Za-z])?"  # letra…letra: pontuação colada não entra
_RE_WHO = re.compile(r"who\s+will\s+you\s+attack", re.I)
_RE_CONFLICT = re.compile(r"really\s+start\s+a\s+conflict", re.I)
_RE_AIM = re.compile(r"what\s+do\s+you\s+want\s+to\s+aim\s+for", re.I)
#: "Attack upper body:" / "Attack head:" (nunca "Attack the …:" nem "Attack:").
_RE_TYPE_HEADER = re.compile(
    r"\bAttack\s+(?!(?:the|a|an)\b)(?:[a-z'\-]+\s+){0,3}[a-z'\-]+\s*:", re.I)
_RE_DONT_SHOW = re.compile(r"don['’`]?t\s+show\s+(?:this\s+)?again", re.I)
_RE_HELP_FOCUS = re.compile(r"(?:^|/)Help(?:/|$)", re.I)
_RE_MAP_FOCUS = re.compile(r"^dungeonmode/default$", re.I)
_RE_OKAY, _RE_NEXT = re.compile(r"\bOkay\b"), re.compile(r"\bNext\b")
_RE_CONFIRM, _RE_DONE = re.compile(r"\bConfirm\b"), re.compile(r"\bDone\b")
_RE_CONFLICT_NAME = re.compile(
    r"really\s+start\s+a\s+conflict\s*(?:with\s+)?(?P<rest>.*)$", re.I)
_RE_MULTI_PARTY = re.compile(r"(?:,|\band\b|\bor\b|\d)", re.I)
_RE_FOCUS_MARK = re.compile(r"^\s*[·•*\-]?\s*focus\s*:\s*(?P<f>\S.*)$", re.I)
_RE_CONTROL = re.compile("[\x00-\x1f\x7f-\x9f​-‏‪-‮⁠-⁩﻿]")
_RE_NICKNAME = re.compile(r"[\"“”][^\"“”]{2,40}[\"“”]")
_RE_LOWER_WORD = re.compile(r"[a-z]+")
_RE_RUN = re.compile(rf"{_WORD}(?: {_WORD})*")  # palavras com UM espaço (menu: 2+ entre entradas)
_RE_STRIKE_RUN = re.compile(r"^(?:[a-z]\s+)?Strike\b")  # S maiúsculo: "strike/…" é o escudo
_VERB_ALT = "|".join(sorted((re.escape(v) for v in ATTACK_VERBS), key=len, reverse=True))
#: ``a stab/iron spear`` · ``b bash/shaft/iron spear`` · ``d punch/hands``.
_RE_ATTACK_ENTRY = re.compile(
    r"(?<![A-Za-z0-9])(?:\[?(?P<key>[a-z])[\].):]?[ \t]+)?"
    rf"(?P<verb>{_VERB_ALT})(?:es|s)?\b"
    rf"(?P<tail>(?:/{_WORD}(?: {_WORD}){{0,3}})*(?: \([^)]{{1,40}}\))?)",
    re.I,
)

# =============================================================== ecrã (dados)


@dataclass(frozen=True)
class ScreenView:
    """Um ecrã: ``focus`` (viewscreens) + ``lines`` (índice = ``y``; carácter = ``x``)."""

    focus: tuple[str, ...] = ()
    lines: tuple[str, ...] = ()

    @property
    def text(self) -> str:
        return "\n".join(self.lines)

    @property
    def focus_text(self) -> str:
        return "/".join(self.focus)

    @property
    def is_map(self) -> bool:
        """O mapa de aventura está em primeiro plano (``dungeonmode/Default``)."""
        return bool(_RE_MAP_FOCUS.match(self.focus_text.strip()))

    @property
    def is_empty(self) -> bool:
        return not self.focus and not any(line.strip() for line in self.lines)


def _clean_lines(items: Iterable[Any]) -> tuple[str, ...]:
    """Linhas seguras: controlo/bidi viram espaço (preserva colunas), com teto."""
    out = []
    for item in items:
        out.append(_RE_CONTROL.sub(" ", item).rstrip()[:MAX_LINE_CHARS]
                   if isinstance(item, str) else "")
        if len(out) >= MAX_SCREEN_LINES:
            break
    return tuple(out)


def _focus_tuple(value: Any) -> tuple[str, ...]:
    items = (value,) if isinstance(value, str) else (
        value if isinstance(value, (list, tuple)) else ())
    texts = (_RE_CONTROL.sub(" ", i).strip()[:MAX_FOCUS_CHARS] for i in items if isinstance(i, str))
    return tuple(t for t in texts if t)[:MAX_FOCUS_ITEMS]


def _from_text(text: str, focus: tuple[str, ...] = ()) -> ScreenView:
    raw = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not focus:  # `act screen` sem --json termina com "· focus: dungeonmode/Default"
        for idx in range(len(raw) - 1, max(-1, len(raw) - 4), -1):
            found = _RE_FOCUS_MARK.match(raw[idx])
            if found:
                focus = _focus_tuple(found.group("f"))
                del raw[idx]
                break
    return ScreenView(focus, _clean_lines(raw))


def _from_mapping(payload: Mapping[str, Any], depth: int) -> ScreenView:
    lines = payload.get("lines")
    lines = payload.get("screen_text") if lines is None else lines
    focus = _focus_tuple(payload.get("focus"))
    if isinstance(lines, str):
        return _from_text(lines[:MAX_PAYLOAD_CHARS], focus)
    if isinstance(lines, (list, tuple)):
        return ScreenView(focus, _clean_lines(lines))
    if depth >= MAX_PARSE_DEPTH:
        return ScreenView(focus)
    steps = payload.get("steps")  # relatório do `act batch`: o último `read screen`
    for step in reversed(steps[-MAX_SCREEN_LINES:]) if isinstance(steps, (list, tuple)) else ():
        if isinstance(step, Mapping) and step.get("lines") is not None:
            view = _from_mapping(step, depth + 1)
            if view.lines:
                return view
    for key in ("data", "screen", "result", "payload", "state"):
        inner = payload.get(key)
        if isinstance(inner, (Mapping, list, tuple, str)):
            view = _parse(inner, depth + 1)
            if view.lines:
                return view if view.focus else ScreenView(focus, view.lines)
    return ScreenView(focus)


def _parse(payload: Any, depth: int) -> ScreenView:
    if isinstance(payload, ScreenView):
        return payload
    if isinstance(payload, (bytes, bytearray)):
        payload = bytes(payload).decode("utf-8", "replace")
    if isinstance(payload, str):
        payload = payload[:MAX_PAYLOAD_CHARS]
        if payload.strip()[:1] in ("{", "[") and depth < MAX_PARSE_DEPTH:  # JSON da ponte
            try:
                return _parse(json.loads(payload), depth + 1)
            except ValueError:
                pass
        return _from_text(payload)
    if (isinstance(payload, tuple) and len(payload) == 2
            and all(isinstance(part, str) for part in payload)):
        return _from_text(payload[1][:MAX_PAYLOAD_CHARS], _focus_tuple(payload[0]))  # driver
    if isinstance(payload, (list, tuple)):
        return ScreenView(lines=_clean_lines(payload))
    if isinstance(payload, Mapping):
        return _from_mapping(payload, depth)
    return ScreenView()


def parse_screen(payload: Any) -> ScreenView:
    """Normaliza o que a ponte/driver devolve num :class:`ScreenView`. Nunca levanta.

    Aceita: JSON do ``act screen`` (também dentro de ``data``/``screen``), o
    relatório do ``act batch`` (último ``read screen``), o texto impresso (com ou
    sem a marca final ``· focus: …``), uma lista de linhas, um par ``(focus,
    text)`` — o que o ``SubprocessBridge.screen()`` devolve — e o ``screen_text``
    do snapshot. Ilegível ⇒ ecrã vazio (``unknown``).
    """
    try:
        return _parse(payload, 0)
    except Exception:  # entrada hostil nunca derruba o loop
        return ScreenView()


def screen_from_report(report: Any) -> ScreenView | None:
    """Último ecrã lido num relatório do ``act batch`` (``None`` se não há)."""
    view = parse_screen(report)
    return None if view.is_empty else view


# ============================================================ procura de rótulos


class LabelMatch(NamedTuple):
    """Onde um rótulo está: ponto de clique (meio do texto) + extensão."""

    x: int
    y: int
    start: int
    end: int
    text: str

    @property
    def point(self) -> tuple[int, int]:
        return (self.x, self.y)


def _match(y: int, start: int, end: int, text: str) -> LabelMatch:
    """Clique = meio do texto do rótulo (como ``label_centre`` do driver)."""
    return LabelMatch(start + (end - start) // 2, y, start, end, text)


@functools.lru_cache(maxsize=256)
def _compiled(pattern: str, flags: int) -> re.Pattern[str]:
    try:
        return re.compile(pattern, flags)
    except re.error:  # padrão inválido ⇒ procura-se o texto literal
        return re.compile(re.escape(pattern), flags)


def find_label_match(
    view: Any, pattern: Any, *, occurrence: int = 0, min_y: int = 0,
    max_y: int | None = None, ignore_case: bool = True,
) -> LabelMatch | None:
    """``occurrence``-ésima ocorrência de ``pattern`` (regex ou literal) no ecrã.

    O DF 53.16 é *mouse-first*: o clique sintético é o **meio do texto** do
    rótulo (``x`` = coluna, ``y`` = linha), derivado do ``screen_text`` como no
    ``label_centre`` do driver. Padrão inválido cai para texto literal; nunca
    levanta.
    """
    try:
        rx = pattern if isinstance(pattern, re.Pattern) else _compiled(
            str(pattern), re.IGNORECASE if ignore_case else 0)
        wanted, seen = max(0, int(occurrence)), 0
        for y, line in enumerate(parse_screen(view).lines):
            if y < max(0, int(min_y)) or (max_y is not None and y > max_y):
                continue
            for found in rx.finditer(line):
                if found.end() > found.start():  # match vazio não é um rótulo
                    if seen == wanted:
                        return _match(y, found.start(), found.end(), found.group(0))
                    seen += 1
    except Exception:
        pass
    return None


def find_label(view: Any, pattern: Any, **kwargs: Any) -> tuple[int, int] | None:
    """``(x, y)`` do meio do rótulo ``pattern`` ou ``None`` (ver :func:`find_label_match`)."""
    found = find_label_match(view, pattern, **kwargs)
    return found.point if found else None


def _runs(view: ScreenView, y_from: int, y_to: int,
          needles: tuple[str, ...] = ()) -> Iterator[LabelMatch]:
    """Entradas de menu: palavras com UM espaço (a tecla de atalho vem incluída).

    ``needles`` (minúsculas) é um pré-filtro barato: só as linhas que contêm
    alguma passam ao regex — num ecrã de 160×400 desconhecido nada é construído.
    """
    for y in range(max(0, y_from), min(len(view.lines), y_to)):
        line = view.lines[y]
        if needles and not any(n in line.lower() for n in needles):
            continue
        for found in _RE_RUN.finditer(line):
            yield _match(y, found.start(), found.end(), found.group(0))


def _header_y(view: ScreenView, rx: re.Pattern[str]) -> int | None:
    return next((y for y, line in enumerate(view.lines) if rx.search(line)), None)


def _find_regex(view: ScreenView, rx: re.Pattern[str]) -> LabelMatch | None:
    for y, line in enumerate(view.lines):
        found = rx.search(line)
        if found:
            return _match(y, found.start(), found.end(), found.group(0))
    return None


# ============================================================ alvo / espécie


def species_words(name: Any) -> tuple[str, ...]:
    """Palavras que identificam a espécie de ``name`` (sem artigos/sexo/idade).

    ``"Mule (tame)"`` → ``("mule",)``; ``"The stray small reindeer cow"`` →
    ``("reindeer",)``; ``"The stray cow"`` → ``("cow",)`` (só há palavras de
    estado/sexo: fica o substantivo-núcleo, a última). Teclas de atalho (1 letra)
    saem.
    """
    if not isinstance(name, str):
        return ()
    cleaned = re.sub(r"\([^)]*\)", " ", name.lower())
    words = [w for w in _RE_LOWER_WORD.findall(cleaned) if w not in _ARTICLES and len(w) > 1]
    return tuple([w for w in words if w not in _MODIFIER_WORDS] or words[-1:])


def _core(name: Any) -> frozenset[str]:
    """Espécie comparável (plural tolerado: ``mules`` ≡ ``mule``)."""
    return frozenset(
        w[:-1] if len(w) > 3 and w.endswith("s") and not w.endswith("ss") else w
        for w in species_words(name))


def target_matches(candidate: Any, target_name: Any) -> bool:
    """``candidate`` (linha do menu/prompt) nomeia a MESMA espécie que ``target_name``?"""
    core = _core(target_name)
    return bool(core) and _core(candidate) == core


def looks_sapient(text: Any) -> bool:
    """O texto parece uma criatura/pessoa sapiente? (regra de segurança, tolerante)

    Raças de :data:`SAPIENT_WORDS` (substring, como ``candidates._looks_sapient``),
    alcunha entre aspas (``Hode Ijamosman "Clockshadows", Swordsman``), títulos e
    profissões e "animal people" (``… man``/``… woman``).
    """
    if not isinstance(text, str) or not text.strip():
        return False
    low = text.lower()
    return (any(word in low for word in SAPIENT_WORDS) or bool(_RE_NICKNAME.search(text))
            or bool(set(_RE_LOWER_WORD.findall(low)) & _PERSON_TOKENS))


def canonical_aim(aim: Any) -> str:
    """Nome canónico de uma mira (``"Skull"`` → ``"head"``); inválido ⇒ ``DEFAULT_AIM``."""
    text = " ".join(_RE_LOWER_WORD.findall(aim.lower())) if isinstance(aim, str) else ""
    return AIM_ALIASES.get(text, text) if text and len(text) <= 30 else DEFAULT_AIM


def _aim_candidates(aim: Any, fallbacks: Sequence[str] | None) -> tuple[str, ...]:
    first = canonical_aim(aim)
    extra = AIM_FALLBACKS.get(first, ("upper body",)) if fallbacks is None else fallbacks
    return tuple(dict.fromkeys([first, *(canonical_aim(x) for x in extra)]))


# ===================================================== entradas dos menus


class AttackOption(NamedTuple):
    """Uma entrada do menu "Attack <parte>:" (``a stab/iron spear``)."""

    verb: str
    hotkey: str = ""
    detail: str = ""
    text: str = ""
    x: int | None = None
    y: int | None = None


def _canon_verb(word: str) -> str | None:
    low = word.lower()
    if low in ATTACK_VERBS:
        return low
    return next((low[:-len(s)] for s in ("es", "s")
                 if low.endswith(s) and low[:-len(s)] in ATTACK_VERBS), None)


def _option(found: re.Match[str], y: int | None) -> AttackOption | None:
    verb = _canon_verb(found.group("verb"))
    if verb is None:
        return None
    detail = (found.group("tail") or "").strip().lstrip("/").strip()
    x = found.start() + (found.end() - found.start()) // 2 if y is not None else None
    return AttackOption(verb, (found.group("key") or "").lower(), detail[:MAX_LABEL_CHARS],
                        found.group(0).strip()[:MAX_LABEL_CHARS], x, y)


def parse_attack_label(text: Any) -> AttackOption | None:
    """Interpreta UM rótulo (``"a stab/iron spear"`` ou ``"stab/iron spear"``); ``None``
    se não começar por um verbo de golpe conhecido (agarrar, lançar, …)."""
    if not isinstance(text, str):
        return None
    found = _RE_ATTACK_ENTRY.match(_RE_CONTROL.sub(" ", text).strip()[:MAX_LINE_CHARS])
    return _option(found, None) if found else None


def list_attack_options(view: Any) -> tuple[AttackOption, ...]:
    """Entradas visíveis do menu "Attack <parte>:" por ordem de leitura.

    Com cabeçalho (``Attack head:``) valem as linhas abaixo dele; sem cabeçalho só
    o formato completo ``tecla verbo/detalhe`` (evita "you stab the …" do registo
    de anúncios).
    """
    screen = parse_screen(view)
    header = _header_y(screen, _RE_TYPE_HEADER)
    first = header + 1 if header is not None else 0
    last = first + ATTACK_TYPE_SPAN if header is not None else len(screen.lines)
    out = []
    for y in range(first, min(len(screen.lines), last)):
        low = screen.lines[y].lower()  # pré-filtro barato: só estas linhas vão ao regex caro
        if not any(verb in low for verb in ATTACK_VERBS):
            continue
        for found in _RE_ATTACK_ENTRY.finditer(screen.lines[y]):
            key, tail = found.group("key"), found.group("tail")
            if (key or tail) if header is not None else (key and tail):
                option = _option(found, y)
                if option is not None:
                    out.append(option)
    return tuple(out)


def _find_target_entry(
    view: ScreenView, core: frozenset[str], occurrence: int
) -> LabelMatch | None:
    """Linha do alvo em "Who will you attack?" (espécie IGUAL; nunca "…stands up")."""
    header = _header_y(view, _RE_WHO)
    first = header + 1 if header is not None else 0
    hits = [run for run in _runs(view, first, first + WHO_SPAN, tuple(sorted(core)))
            if core and _core(run.text) == core]
    return hits[occurrence] if 0 <= occurrence < len(hits) else None


def _find_aim_entry(view: ScreenView, aim: str) -> LabelMatch | None:
    header = _header_y(view, _RE_AIM)
    first = header + 1 if header is not None else 0
    wanted = tuple(aim.split())
    for run in _runs(view, first, first + AIM_SPAN, wanted[-1:]):
        words = tuple(w for w in _RE_LOWER_WORD.findall(run.text.lower()) if len(w) > 1)
        extra = len(words) - len(wanted)
        if words[extra:] == wanted and (extra == 0 or (
                0 < extra <= 2 and wanted[-1] in _LIMB_AIMS)):  # "left front leg" ⊇ "leg"
            return run
    return None


def _find_strike(view: ScreenView) -> LabelMatch | None:
    """``a Strike`` do planner (``c strike/copper buckler`` é a entrada do escudo)."""
    for run in _runs(view, 0, len(view.lines), ("strike",)):
        if (_RE_STRIKE_RUN.match(run.text) and len(run.text.split()) <= 6
                and view.lines[run.y][run.end:run.end + 1] != "/"):
            return run
    return None


def _modifier_runs(view: ScreenView) -> Iterator[tuple[str, LabelMatch]]:
    for run in _runs(view, 0, len(view.lines), tuple(_MODIFIER_LABELS)):
        name = _MODIFIER_LABELS.get(re.sub(r"^[a-z]\s+", "", run.text.lower()).strip())
        if name is not None:
            yield name, run


def _find_modifier(view: ScreenView, name: str) -> LabelMatch | None:
    return next((run for found, run in _modifier_runs(view) if found == name), None)


def _normalise(items: Any, canon: Any) -> tuple[str, ...]:
    items = (items,) if isinstance(items, str) else items or ()
    return tuple(dict.fromkeys(c for c in (canon(str(i).strip().lower()) for i in items) if c))


def _pick_attack_option(
    options: Sequence[AttackOption], pref: Sequence[str]
) -> tuple[AttackOption, bool] | None:
    """``(entrada, é_recurso)``: 1.ª da preferência; senão 1.ª dos verbos de recurso."""
    for verbs, fallback in ((pref, False), (DEFAULT_ATTACK_FALLBACK, True)):
        for verb in verbs:
            for option in options:
                if option.verb == verb and not (fallback and verb in pref):
                    return option, fallback
    return None


# =============================================================== estágio


def detect_stage(view: Any) -> str:
    """Em que estágio da cadeia ``A_ATTACK`` está este ecrã (um de :data:`STAGES`).

    Ordem: o MODAL ganha ao que está por baixo, depois do mais fundo ao mais
    raso (painéis anteriores podem continuar visíveis): overlay de ajuda →
    prompt de conflito → tipo de ataque → modificadores → mira → planner →
    quem atacar → "Okay" solto → mapa → ``unknown``. Com o mapa em foco o texto é
    ignorado (só pode ser ``done``); sem foco (texto simples) decidem os
    marcadores (sem foco o mapa não se distingue de um ecrã desconhecido: guarde
    sempre o ``focus`` que a ponte devolve). Nunca levanta.
    """
    try:
        screen = parse_screen(view)
        if screen.is_empty:
            return STAGE_UNKNOWN
        if screen.is_map:
            return STAGE_DONE
        if _RE_HELP_FOCUS.search(screen.focus_text) or _header_y(screen, _RE_DONT_SHOW) is not None:
            return STAGE_HELP_OVERLAY
        if _header_y(screen, _RE_CONFLICT) is not None:
            return STAGE_CONFIRM
        if _header_y(screen, _RE_TYPE_HEADER) is not None or list_attack_options(screen):
            return STAGE_ATTACK_TYPE
        if _find_regex(screen, _RE_DONE) and next(_modifier_runs(screen), None):
            return STAGE_MODIFIERS
        if _header_y(screen, _RE_AIM) is not None:
            return STAGE_AIM
        if _find_strike(screen):
            return STAGE_PLANNER
        if _header_y(screen, _RE_WHO) is not None:
            return STAGE_WHO
        return STAGE_HELP_OVERLAY if _find_regex(screen, _RE_OKAY) else STAGE_UNKNOWN
    except Exception:
        return STAGE_UNKNOWN


# ================================================================== passos


def _safe_text(value: Any, limit: int) -> str:
    return " ".join(_RE_CONTROL.sub(" ", value).split())[:limit] if isinstance(value, str) else ""


@dataclass(frozen=True)
class ChainStep:
    """UM passo da cadeia.

    ``action``: ``click`` (``x``,``y``) · ``key`` · ``wait`` (deixar o ecrã assentar
    e reler) · ``done`` (golpe entregue / sem menu) · ``abort`` (``key=
    "LEAVESCREEN"`` pede UM recuo de menu; ``None`` = nada a fechar). ``reason`` é
    um código de :class:`Reason`; ``label``/``detail`` são texto do ecrã já
    sanitizado (dado, só para registo — nunca vai para o DSL).
    """

    stage: str
    action: str
    x: int | None = None
    y: int | None = None
    key: str | None = None
    label: str = ""
    reason: str = ""
    detail: str = ""

    @property
    def is_terminal(self) -> bool:
        """``done`` ou ``abort`` sem tecla pendente: a cadeia acabou."""
        return self.action == ACTION_DONE or (self.action == ACTION_ABORT and not self.key)


def _click(stage: str, match: LabelMatch, reason: str, detail: str = "") -> ChainStep:
    return ChainStep(stage, ACTION_CLICK, match.x, match.y,
                     label=_safe_text(match.text, MAX_LABEL_CHARS), reason=reason,
                     detail=_safe_text(detail, MAX_DETAIL_CHARS))


def _wait(stage: str, reason: str, detail: str = "") -> ChainStep:
    return ChainStep(stage, ACTION_WAIT, reason=reason, detail=_safe_text(detail, MAX_DETAIL_CHARS))


def _abort(view: ScreenView, stage: str, reason: str, detail: str = "") -> ChainStep:
    """Aborta; ``LEAVESCREEN`` só se há algo para fechar (no mapa abriria o menu)."""
    open_ui = not view.is_empty and not view.is_map and stage != STAGE_DONE
    return ChainStep(stage, ACTION_ABORT, key=KEY_LEAVESCREEN if open_ui else None,
                     reason=reason, detail=_safe_text(detail, MAX_DETAIL_CHARS))


def open_menu_step() -> ChainStep:
    """Passo de abertura: ``key A_ATTACK`` (a presa adjacente é pré-condição)."""
    return ChainStep(STAGE_START, ACTION_KEY, key=KEY_ATTACK, reason=Reason.OPEN_MENU)


def _conflict_named_text(view: ScreenView) -> str:
    """Criatura nomeada por "Really start a conflict with …?" (``""`` = ilegível)."""
    for y, line in enumerate(view.lines):
        found = _RE_CONFLICT_NAME.search(line)
        if not found:
            continue
        # só o texto do prompt: o que vem depois do enchimento do painel é mapa
        chunk = re.split(r"\s{2,}", found.group("rest").strip())[0]
        if "?" not in chunk and y + 1 < len(view.lines):
            # o prompt pode quebrar a linha: a continuação só conta se fecha a pergunta
            lead = re.split(r"\s{2,}", view.lines[y + 1][found.start():].strip())[0]
            if "?" in lead and re.match(r"[A-Za-z]", lead):
                chunk = f"{chunk} {lead}"
        name = " ".join(chunk.split("?")[0].strip(" .,:;\"'").split())
        letters = re.search(r"[A-Za-z]", name) is not None
        return name if letters and 0 < len(name.split()) <= 8 else ""
    return ""


def _confirm_step(view: ScreenView, core: frozenset[str], confirmed: bool) -> ChainStep:
    named = _conflict_named_text(view)
    if not named:
        return _abort(view, STAGE_CONFIRM, Reason.CONFLICT_UNREADABLE)
    if looks_sapient(named) or _RE_MULTI_PARTY.search(named) or _core(named) != core:
        return _abort(view, STAGE_CONFIRM, Reason.CONFLICT_MISMATCH, named)
    button = _find_regex(view, _RE_CONFIRM)
    if button is None:
        return _abort(view, STAGE_CONFIRM, Reason.NO_CONFIRM_BUTTON, named)
    if not confirmed:
        return _wait(STAGE_CONFIRM, Reason.NEEDS_CONFIRMATION, named)
    return _click(STAGE_CONFIRM, button, Reason.CONFLICT_CONFIRMED, named)


def next_step(
    view: Any,
    target_name: Any,
    aim: Any = DEFAULT_AIM,
    attack_pref: Sequence[str] = DEFAULT_ATTACK_PREF,
    modifiers: Sequence[str] = (),
    confirmed: bool = False,
    *,
    attack_selected: bool = False,
    unknown_streak: int = 0,
    max_unknown: int = DEFAULT_MAX_UNKNOWN,
    occurrence: int = 0,
    aim_fallbacks: Sequence[str] | None = None,
) -> ChainStep:
    """O próximo passo da cadeia para este ecrã (PURA; nunca levanta).

    Args:
        view: ecrã (qualquer forma de :func:`parse_screen`).
        target_name: a presa (``"Mule (tame)"``); vazia/sapiente ⇒ ``abort``.
        aim: parte a mirar (:func:`canonical_aim`); ausente do menu ⇒
            ``aim_fallbacks`` (omissão: :data:`AIM_FALLBACKS`).
        attack_pref: verbos por ordem de preferência; faltando todos recorre aos
            verbos conhecidos restantes (nunca a entradas que não são golpes).
        modifiers: modificadores AINDA por ativar (o chamador tira os já clicados).
        confirmed: consentimento para iniciar o conflito (ver o módulo).
        attack_selected: a entrada do golpe já foi clicada (falta "Done"/esperar).
        unknown_streak: ecrãs ``unknown`` seguidos ANTES deste (aborta quando
            ``unknown_streak + 1 > max_unknown``).
        occurrence: índice (0-based) da entrada do alvo quando há várias iguais.
    """
    try:
        return _next_step(view, target_name, aim, attack_pref, modifiers, confirmed,
                          attack_selected, unknown_streak, max_unknown, occurrence, aim_fallbacks)
    except Exception:  # a cadeia nunca derruba o loop: aborta de forma segura
        try:
            screen = parse_screen(view)
            return _abort(screen, detect_stage(screen), Reason.INTERNAL_ERROR)
        except Exception:
            return ChainStep(STAGE_UNKNOWN, ACTION_ABORT, reason=Reason.INTERNAL_ERROR)


def _next_step(
    view: Any, target_name: Any, aim: Any, attack_pref: Any, modifiers: Any,
    confirmed: bool, attack_selected: bool, unknown_streak: int, max_unknown: int,
    occurrence: int, aim_fallbacks: Sequence[str] | None,
) -> ChainStep:
    screen = parse_screen(view)
    stage = detect_stage(screen)
    core = _core(target_name)
    if stage == STAGE_DONE:
        return ChainStep(STAGE_DONE, ACTION_DONE, reason=Reason.NO_MENU)
    if not core:
        return _abort(screen, stage, Reason.NO_TARGET)
    if looks_sapient(str(target_name)):
        return _abort(screen, stage, Reason.SAPIENT_TARGET, str(target_name))
    pending = _normalise(modifiers, _MODIFIER_LABELS.get)

    if stage == STAGE_HELP_OVERLAY:
        okay, nxt = _find_regex(screen, _RE_OKAY), _find_regex(screen, _RE_NEXT)
        if okay or nxt:
            return _click(stage, okay or nxt, Reason.HELP_DISMISSED if okay else Reason.HELP_NEXT)
        return _abort(screen, stage, Reason.HELP_NO_BUTTON)
    if stage == STAGE_WHO:
        entry = _find_target_entry(screen, core, int(occurrence))
        if entry is None:
            return _abort(screen, stage, Reason.TARGET_NOT_LISTED, str(target_name))
        return _click(stage, entry, Reason.TARGET_SELECTED)
    if stage == STAGE_CONFIRM:
        return _confirm_step(screen, core, bool(confirmed))
    if stage == STAGE_PLANNER:
        strike = _find_strike(screen)
        if strike is None:
            return _abort(screen, stage, Reason.NO_STRIKE)
        return _click(stage, strike, Reason.STRIKE_SELECTED)
    if stage == STAGE_AIM:
        candidates = _aim_candidates(aim, aim_fallbacks)
        for index, candidate in enumerate(candidates):
            entry = _find_aim_entry(screen, candidate)
            if entry is not None:
                return _click(stage, entry, Reason.AIM_FALLBACK if index else Reason.AIM_SELECTED,
                              candidate)
        return _abort(screen, stage, Reason.AIM_UNAVAILABLE, ",".join(candidates))
    if stage in (STAGE_ATTACK_TYPE, STAGE_MODIFIERS):
        done = _find_regex(screen, _RE_DONE)
        if attack_selected and done:  # golpe escolhido: só falta fechar
            return _click(stage, done, Reason.DONE_CLICKED)
        if attack_selected:
            return _wait(stage, Reason.WAIT_GAME)
        for name in pending:  # antes da entrada: clicá-la pode executar o golpe já
            button = _find_modifier(screen, name)
            if button:
                return _click(stage, button, f"{Reason.MODIFIER}:{name}")
        if stage == STAGE_MODIFIERS:
            if done:
                return _click(stage, done, Reason.DONE_CLICKED)
            return _wait(stage, Reason.WAIT_GAME)
        options = list_attack_options(screen)
        wanted = _normalise(attack_pref, _canon_verb) or DEFAULT_ATTACK_PREF
        picked = _pick_attack_option(options, wanted)
        if picked is None:
            seen = ",".join(option.verb for option in options)
            return _abort(screen, stage, Reason.NO_ATTACK_TYPE, seen or "no entries")
        option, is_fallback = picked
        entry = LabelMatch(option.x or 0, option.y or 0, 0, 0, option.text)
        head = Reason.ATTACK_FALLBACK if is_fallback else Reason.ATTACK_SELECTED
        return _click(stage, entry, f"{head}:{option.verb}", option.text)

    # unknown: com o golpe já escolhido, um "Done" solto fecha-o
    done = _find_regex(screen, _RE_DONE)
    if attack_selected and done:
        return _click(STAGE_MODIFIERS, done, Reason.DONE_CLICKED)
    if int(unknown_streak) + 1 > int(max_unknown):
        return _abort(screen, stage, Reason.UNKNOWN_SCREEN, screen.focus_text)
    return _wait(stage, Reason.WAIT_UNKNOWN)


def unwind_step(
    view: Any, presses: int = 0, max_presses: int = DEFAULT_MAX_UNWIND,
    reason: str = Reason.UNWIND,
) -> ChainStep:
    """Recolher dos menus UM ``LEAVESCREEN`` de cada vez (nunca no mapa).

    No mapa ``LEAVESCREEN`` abre o menu do jogo (``dungeonmode/Options``, que
    bloqueia o movimento): cada recuo é seguido de nova leitura. Mapa em foco ⇒
    passo terminal (``abort`` sem tecla); overlay de ajuda ⇒ clica "Okay" (o
    ``LEAVESCREEN`` não o fecha); esgotadas ``max_presses`` ⇒ terminal com
    ``detail="unwind_exhausted"`` (o StuckDetector assume daí).
    """
    try:
        screen = parse_screen(view)
        stage = detect_stage(screen)
        why = _safe_text(reason, MAX_LABEL_CHARS) or Reason.UNWIND
        if stage == STAGE_DONE or screen.is_empty:
            return ChainStep(STAGE_DONE, ACTION_ABORT, reason=why, detail="unwound")
        if int(presses) >= int(max_presses):
            return ChainStep(stage, ACTION_ABORT, reason=why, detail="unwind_exhausted")
        okay = _find_regex(screen, _RE_OKAY) if stage == STAGE_HELP_OVERLAY else None
        if okay:
            return ChainStep(stage, ACTION_CLICK, okay.x, okay.y, label="Okay",
                             reason=why, detail="unwind")
        return ChainStep(stage, ACTION_ABORT, key=KEY_LEAVESCREEN, reason=why, detail="unwind")
    except Exception:
        return ChainStep(STAGE_UNKNOWN, ACTION_ABORT, reason=Reason.INTERNAL_ERROR)


def _int_in(value: Any, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= high


def to_dsl(step: Any, *, settle_ms: int | None = None, observe: bool = False) -> str:
    """DSL do ``df_bridge``: ``"click X,Y"`` · ``"key LEAVESCREEN"`` · ``"sleep N"``.

    ``""`` para ``done``/``abort`` sem tecla (nada a executar) e para qualquer
    passo inválido (coordenadas fora de ``0..MAX_COORD``, tecla fora de
    :data:`ALLOWED_KEYS`): o DSL nunca contém texto do ecrã. ``settle_ms`` acrescenta
    ``; sleep N`` (ms) a um clique/tecla e ``observe=True`` acrescenta ``; read
    screen`` (o ecrã que alimenta ``AttackChain.observe``).
    """
    if not isinstance(step, ChainStep):
        return ""
    settle_ok = settle_ms is not None and _int_in(settle_ms, MAX_SETTLE_MS)
    op = ""
    if step.action == ACTION_CLICK and _int_in(step.x, MAX_COORD) and _int_in(step.y, MAX_COORD):
        op = f"click {step.x},{step.y}"
    elif step.action in (ACTION_KEY, ACTION_ABORT) and step.key in ALLOWED_KEYS:
        op = f"key {step.key}"
    elif step.action == ACTION_WAIT:
        wait = DEFAULT_SETTLE_MS if settle_ms is None else settle_ms
        op = f"sleep {wait}" if _int_in(wait, MAX_SETTLE_MS) else ""
    if not op:
        return ""
    parts = [op]
    if settle_ok and step.action != ACTION_WAIT:
        parts.append(f"sleep {settle_ms}")
    if observe:
        parts.append("read screen")
    return "; ".join(parts)


# ======================================================= cadeia com memória


class AttackChain:
    """Uma cadeia ``A_ATTACK`` com a memória mínima que a função pura não tem.

    O executor faz o I/O; aqui só se decide::

        chain = AttackChain("Mule (tame)", aim="neck", confirmed=True)
        view = <ecrã lido>                       # mapa → pede a abertura (A_ATTACK)
        while True:
            dsl = to_dsl(step := chain.observe(view), settle_ms=500, observe=True)
            if not dsl:                          # done / abort terminal
                break
            view = <ecrã devolvido por `act batch "<dsl>"`>

    ``strike_committed`` diz se o golpe saiu (confirmar no gamelog; também vale com
    ``abort``). Depois de um ``abort`` continua-se a chamar ``observe`` com ecrãs
    novos: recolhe dos menus com um ``LEAVESCREEN`` de cada vez
    (:func:`unwind_step`). Se depois do golpe aparece um ecrã desconhecido
    (anúncios, menu do jogo) fecha-o do mesmo modo e termina em ``done``: o golpe
    saiu, não é falha.

    Precisa de memória porque o mapa no início ≠ mapa no fim, porque os
    modificadores se vão gastando, porque a entrada do golpe pode já ter sido
    clicada, porque conta ``unknown`` seguidos e porque o MESMO passo sobre o MESMO
    ecrã é um clique sem efeito. Sem ``confirmed`` o passo no prompt é ``wait``
    (``needs_confirmation``): o chamador pára de ler, pede aprovação e retoma com
    :meth:`confirm`.
    """

    def __init__(
        self, target_name: str, *, aim: str = DEFAULT_AIM,
        attack_pref: Sequence[str] = DEFAULT_ATTACK_PREF, modifiers: Sequence[str] = (),
        aim_fallbacks: Sequence[str] | None = None, occurrence: int = 0,
        confirmed: bool = False, max_steps: int = DEFAULT_MAX_STEPS,
        max_unknown: int = DEFAULT_MAX_UNKNOWN, max_repeat: int = DEFAULT_MAX_REPEAT,
        max_unwind: int = DEFAULT_MAX_UNWIND,
    ) -> None:
        self.target_name = str(target_name or "")
        self.aim = aim
        self.attack_pref = tuple(attack_pref)
        self.aim_fallbacks = None if aim_fallbacks is None else tuple(aim_fallbacks)
        self.occurrence = max(0, int(occurrence))
        self.confirmed = bool(confirmed)
        self.max_steps, self.max_unknown = max(1, int(max_steps)), max(0, int(max_unknown))
        self.max_repeat, self.max_unwind = max(1, int(max_repeat)), max(0, int(max_unwind))
        self.history: list[ChainStep] = []
        self._pending = list(_normalise(modifiers, _MODIFIER_LABELS.get))
        self._opened = self._attack_selected = self._strike_committed = self._aborted = False
        self._clicks = self._issued = self._unknown = self._reopened = 0
        self._repeat = self._unwind_presses = 0
        self._abort_reason = self._last_reason = ""
        self._last_sig: tuple[Any, ...] | None = None
        self._final: ChainStep | None = None

    @property
    def finished(self) -> bool:
        return self._final is not None

    @property
    def aborted(self) -> bool:
        return self._aborted

    @property
    def abort_reason(self) -> str:
        return self._abort_reason

    @property
    def strike_committed(self) -> bool:
        """Foi clicada uma entrada de golpe (e/ou "Done"): o golpe pode ter saído."""
        return self._strike_committed

    @property
    def steps_issued(self) -> int:
        return self._issued

    def confirm(self) -> None:
        """Consentimento dado (banda ``auto`` ou aprovação humana): o prompt avança."""
        self.confirmed = True

    def observe(self, screen: Any) -> ChainStep:
        """Próximo passo para este ecrã (nunca levanta; ver a docstring da classe)."""
        try:
            return self._observe(screen)
        except Exception:
            return self._finish(
                ChainStep(STAGE_UNKNOWN, ACTION_ABORT, reason=Reason.INTERNAL_ERROR))

    def _observe(self, screen: Any) -> ChainStep:
        if self._final is not None:
            return self._final
        view = parse_screen(screen)
        stage = detect_stage(view)
        if self._aborted:
            return self._unwind(view)
        unknown_before = self._unknown
        self._unknown = self._unknown + 1 if stage == STAGE_UNKNOWN else 0
        if stage == STAGE_DONE:
            return self._on_map(view)
        self._opened = True  # menu já aberto (mesmo que não por nós): não o reabrir no fim
        if self._strike_committed and stage == STAGE_UNKNOWN:
            return self._after_strike(view)
        if self._issued >= self.max_steps:
            return self._begin_abort(_abort(view, stage, Reason.STEP_BUDGET))
        step = next_step(
            view, self.target_name, self.aim, self.attack_pref, tuple(self._pending),
            self.confirmed, attack_selected=self._attack_selected,
            unknown_streak=unknown_before, max_unknown=self.max_unknown,
            occurrence=self.occurrence, aim_fallbacks=self.aim_fallbacks)
        if step.action == ACTION_ABORT:
            return self._begin_abort(step)
        signature = (step.stage, step.action, step.x, step.y, step.key, hash(view.text))
        self._repeat = self._repeat + 1 if signature == self._last_sig else 1
        self._last_sig = signature
        if self._repeat > self.max_repeat:  # mesmo passo, mesmo ecrã: sem efeito
            return self._begin_abort(_abort(view, stage, Reason.STALLED, step.reason))
        return self._issue(step)

    def _on_map(self, view: ScreenView) -> ChainStep:
        if not self._opened:
            # nunca se abre o menu para um alvo vazio/sapiente (nada a fechar depois)
            if not _core(self.target_name):
                return self._begin_abort(_abort(view, STAGE_DONE, Reason.NO_TARGET))
            if looks_sapient(self.target_name):
                return self._begin_abort(
                    _abort(view, STAGE_DONE, Reason.SAPIENT_TARGET, self.target_name))
            self._opened = True
            return self._issue(open_menu_step())
        if self._clicks == 0:
            return self._begin_abort(_abort(view, STAGE_DONE, Reason.MENU_NOT_OPEN))
        if (not self._strike_committed and self._reopened < MAX_REOPEN
                and self._last_reason in (Reason.HELP_DISMISSED, Reason.HELP_NEXT)):
            self._reopened += 1  # o overlay "Okay" engoliu o A_ATTACK (1.ª vez): reabre UMA vez
            return self._issue(open_menu_step())
        if not self._strike_committed:
            return self._begin_abort(_abort(view, STAGE_DONE, Reason.NO_STRIKE_COMMITTED))
        return self._finish(ChainStep(STAGE_DONE, ACTION_DONE, reason=Reason.STRIKE_COMMITTED))

    def _after_strike(self, view: ScreenView) -> ChainStep:
        """Golpe entregue e um ecrã desconhecido por cima (anúncios/menu do jogo): fecha-o.

        Não é falha — o golpe saiu: recolhe com um ``LEAVESCREEN`` de cada vez e termina
        em ``done`` (``strike_committed``); o gamelog diz o que o golpe fez.
        """
        step = unwind_step(view, self._unwind_presses, self.max_unwind, Reason.UNWIND)
        if step.is_terminal:
            return self._finish(ChainStep(STAGE_DONE, ACTION_DONE, reason=Reason.STRIKE_COMMITTED,
                                          detail=step.detail))
        self._unwind_presses += 1
        if step.action == ACTION_CLICK:
            return self._record(step)
        return self._record(ChainStep(step.stage, ACTION_KEY, key=KEY_LEAVESCREEN,
                                      reason=Reason.UNWIND, detail="after_strike"))

    def _issue(self, step: ChainStep) -> ChainStep:
        self._issued += 1
        self._last_reason = step.reason
        if step.action == ACTION_CLICK:
            self._clicks += 1
            head, _, tail = step.reason.partition(":")
            if head in (Reason.ATTACK_SELECTED, Reason.ATTACK_FALLBACK):
                self._attack_selected = self._strike_committed = True
            elif head == Reason.DONE_CLICKED and self._attack_selected:
                self._strike_committed = True
            elif head == Reason.MODIFIER and tail in self._pending:
                self._pending.remove(tail)
        return self._record(step)

    def _record(self, step: ChainStep) -> ChainStep:
        self.history.append(step)
        del self.history[:-MAX_HISTORY]
        return step

    def _begin_abort(self, step: ChainStep) -> ChainStep:
        self._aborted, self._abort_reason = True, step.reason
        if step.key:  # o próprio passo já é o 1.º LEAVESCREEN do recuo
            self._unwind_presses = 1
            return self._record(step)
        return self._finish(step)

    def _unwind(self, view: ScreenView) -> ChainStep:
        step = unwind_step(view, self._unwind_presses, self.max_unwind, self._abort_reason)
        if step.is_terminal:
            return self._finish(ChainStep(step.stage, ACTION_ABORT, reason=self._abort_reason,
                                          detail=step.detail))
        self._unwind_presses += 1
        return self._record(step)

    def _finish(self, step: ChainStep) -> ChainStep:
        self._final = step
        return self._record(step)
