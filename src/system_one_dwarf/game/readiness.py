"""Prontidão de combate do aventureiro: retrato numérico + regras de decisão (puro Python).

Resolve ARQUITETURA.md §8: **mudança 02** (skills de combate nas decisões de ataque/fuga)
e **mudança 11** (treinar antes de caçar). O Lua (``dfhack/dfb-state.lua``) *mede* —
``adventurer.combat``: skills EFETIVAS, atributos, dor, exaustão, sangue, armadura —; este
módulo *interpreta*. Os números alimentam o prompt do Jev e os candidatos de ação;
**nunca** executam nada (o gate de banda/aprovação continua a ser o do loop).

Contrato: API pública total (dado mau → valor neutro, nunca exceção); campo em falta fica
``None`` e nunca é inventado: sem dados os scores são ``None`` e nenhuma regra dispara.

Decisões (finais):

* Sem HP no adventure mode: "HP < 60%" usa o **sangue** (``blood_count / blood_max``) como
  proxy fiel — :data:`RETREAT_BLOOD_RATIO` = 0.60.
* A escala de skill do DF é **0-based** (Dabbling = 0; wiki ``skill.md``): Proficient = 5,
  Talented = 6. :data:`TRAIN_MIN_LEVEL` aponta para *Proficient* (a regra da mudança 11 é
  "< Proficient"). Skill desconhecida **nunca** bloqueia a caça (falha aberta para ``go``).
* Alvos sapientes nunca são "baixo risco" nem alvos de caça; o sparring tem parceiros
  sapientes, logo todo candidato de treino leva :data:`SPARRING_PARAMS`
  (``requires_approval``) — é o chamador que os aplica aos ``params``.
* Limiares de dor/exaustão são heurísticas (wiki ``wound.md``: 150+ de dor desmaia; DFHack
  ``fix/noexert-exhaustion.lua``: "Tired" aos 2000): constantes nomeadas, a calibrar.
"""
from __future__ import annotations

import functools
import logging
import math
import re
from dataclasses import dataclass, fields
from types import MappingProxyType
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = [
    "ARMOR_SLOTS_FULL", "ATTRIBUTE_REFERENCE", "BLEEDING_WOUNDS_MANY", "BLEED_PENALTY",
    "CREATURE_RISK", "CombatReadiness", "EXHAUSTION_COLLAPSE", "EXHAUSTION_HEAVY",
    "EXHAUSTION_MAX_PENALTY", "HuntPolicy", "MATCHUP_LEVELS", "MATCHUP_MARGIN",
    "MATCHUP_REQUIRED_POWER", "MAX_PROMPT_LINES", "Matchup", "PAIN_COLLAPSE", "PAIN_HEAVY",
    "PAIN_MAX_PENALTY", "POWER_WEIGHTS", "RETREAT_BLOOD_RATIO", "RISK_LEVELS", "RISK_UNKNOWN",
    "RetreatAdvice", "SAPIENT_WORDS", "SKILL_LABELS", "SKILL_LEGENDARY", "SKILL_PROFICIENT",
    "SKILL_REFERENCE_MAX", "SPARRING_PARAMS", "SPARRING_REQUIRES_APPROVAL", "STATUS_FACTOR",
    "TABLE_CLASHES", "TRAIN_FIRST_GAP", "TRAIN_MIN_LEVEL", "TargetRisk", "UNARMED_FACTOR",
    "URGENT_BLOOD_RATIO", "annotate_attack", "assess_target", "creature_risk",
    "describe_target_risk", "skill_label",
]

_LOG = logging.getLogger(__name__)

# ------------------------------------------------------------------ escala de skill
#: Nomes reais do DF por rank (0-based; wiki ``skill.md``). 16..20 = Legendary +1..+5.
SKILL_LABELS: Tuple[str, ...] = (
    "Dabbling", "Novice", "Adequate", "Competent", "Skilled", "Proficient", "Talented",
    "Adept", "Expert", "Professional", "Accomplished", "Great", "Master", "High Master",
    "Grand Master", "Legendary", "Legendary+1", "Legendary+2", "Legendary+3",
    "Legendary+4", "Legendary+5",
)
SKILL_PROFICIENT: int = 5
SKILL_LEGENDARY: int = 15

# ----------------------------------------------------------- limiares (calibráveis)
TRAIN_MIN_LEVEL: int = SKILL_PROFICIENT   # mudança 11: skill da arma abaixo → baixo risco/treino
TRAIN_FIRST_GAP: int = 3                  # ``train_first`` se faltarem >= N níveis
RETREAT_BLOOD_RATIO: float = 0.60         # mudança 02: recuar com sangue abaixo disto
URGENT_BLOOD_RATIO: float = 0.35          # abaixo disto o recuo é urgente
PAIN_HEAVY: float = 75.0                  # dor "pesada": recuar antes do colapso
PAIN_COLLAPSE: float = 150.0              # ~desmaio (depende de toughness)
EXHAUSTION_HEAVY: float = 4000.0          # 2x o "Tired" (2000) documentado pelo DFHack
EXHAUSTION_COLLAPSE: float = 6000.0
BLEEDING_WOUNDS_MANY: int = 3             # ferimentos a sangrar que justificam recuo
SKILL_REFERENCE_MAX: int = SKILL_LEGENDARY  # nível que vale 1.0 no power_score
ATTRIBUTE_REFERENCE: float = 2000.0       # 1000 = média humana/anã; 2000+ = excecional
ARMOR_SLOTS_FULL: int = 5                 # corpo, pernas, cabeça, mãos, pés
UNARMED_FACTOR: float = 0.5               # punhos pesam metade de uma arma
POWER_WEIGHTS: Mapping[str, float] = MappingProxyType({
    "weapon": 0.30, "fighter": 0.15, "dodger": 0.10, "armor_user": 0.05, "shield_user": 0.05,
    "wrestler": 0.05, "strength": 0.08, "agility": 0.06, "toughness": 0.06,
    "endurance": 0.04, "armor_cover": 0.10,
})
#: Poder efetivo (power x condição) exigido por classe de risco; ``margin`` separa níveis.
MATCHUP_REQUIRED_POWER: Mapping[str, float] = MappingProxyType({
    "harmless": 0.0, "low": 0.10, "moderate": 0.22, "high": 0.35, "deadly": 0.60})
MATCHUP_MARGIN: float = 0.10
MATCHUP_LEVELS: Tuple[str, ...] = ("favourable", "even", "risky", "deadly", "unknown")
#: Penalizações da condição, multiplicativas (wiki skill.md "Skill penalties").
PAIN_MAX_PENALTY, EXHAUSTION_MAX_PENALTY, BLEED_PENALTY = 0.7, 0.5, 0.05
STATUS_FACTOR: Mapping[str, float] = MappingProxyType({
    "stunned": 0.5, "winded": 0.85, "nausea": 0.85, "dizziness": 0.85, "webbed": 0.7})
MAX_PROMPT_LINES: int = 10                # orçamento do bloco de estado do Jev
_POWER_BANDS = ((0.2, "weak"), (0.4, "modest"), (0.6, "solid"), (0.8, "strong"), (2.0, "elite"))
_CONDITION_BANDS = ((0.35, "critical"), (0.6, "hurt"), (0.85, "fair"), (2.0, "fit"))

#: O sparring tem parceiros sapientes (nunca atacados sem aprovação humana, §6): o
#: chamador DEVE marcar o candidato de treino com estes ``params``.
SPARRING_REQUIRES_APPROVAL: bool = True
SPARRING_PARAMS: Mapping[str, Any] = MappingProxyType(
    {"requires_approval": True, "training": True, "sapient_partner": True})

_TEXT_CAP, _COUNTER_CAP, _ATTR_CAP, _LEVEL_CAP = 48, 1_000_000.0, 20000.0, 100.0


# ---------------------------------------------------------------- coerção tolerante
def _num(value: Any, hi: float = _COUNTER_CAP) -> Optional[float]:
    """Número finito em ``[0, hi]`` (bool/str/NaN/inf → ``None``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return float(min(max(value, 0.0), hi))


def _int(hi: float = _COUNTER_CAP) -> Callable[[Any], Optional[int]]:
    def parse(value: Any) -> Optional[int]:
        number = _num(value, hi)
        return None if number is None else int(number)
    return parse


def _text(value: Any, limit: int = _TEXT_CAP) -> Optional[str]:
    """Texto do jogo como DADO: sem controlos, numa linha, tamanho limitado."""
    if not isinstance(value, str):
        return None
    flat = " ".join("".join(c if c.isprintable() else " " for c in value).split())
    return flat[:limit].rstrip() or None


def _flag(value: Any) -> Optional[bool]:
    return value if isinstance(value, bool) else None


def _ratio(value: Any) -> Optional[float]:
    number = _num(value, 1.5)
    return None if number is None or value > 1.5 else min(number, 1.0)


def _unit(value: float) -> float:
    return 0.0 if value < 0.0 else 1.0 if value > 1.0 else value


def skill_label(level: Any) -> str:
    """Nome DF do nível (``5`` → ``Proficient``; ``>20`` → ``Legendary+5``); lixo → ``unknown``."""
    value = _int(_LEVEL_CAP)(level)
    return "unknown" if value is None else SKILL_LABELS[min(value, len(SKILL_LABELS) - 1)]


def _lv(level: Optional[int]) -> str:
    return "?" if level is None else f"{level} ({skill_label(level)})"


# -------------------------------------------------------------- risco da criatura
RISK_LEVELS: Tuple[str, ...] = ("harmless", "low", "moderate", "high", "deadly")
RISK_UNKNOWN = "unknown"
_RANK = {name: index for index, name in enumerate(RISK_LEVELS)}

#: Heurística tirada das raws da wiki DF (KB ``skills/df-criaturas/references/<x>-raw.md``,
#: untrusted, só citado): volume adulto ``BODY_SIZE`` (cm3; humano 70 000), ``[LARGE_PREDATOR]``,
#: veneno (``SPECIALATTACK_INJECT_EXTRACT``), ``[MEGABEAST]``/``[SEMIMEGABEAST]``; revista à mão.
#: <10k cm3 harmless · <40k low · <150k moderate · <700k high · >=700k deadly; predador
#: grande +1; veneno +1 em pequenos; gado doméstico em low/moderate. Nomes EN minúsculos,
#: casados por palavra inteira (a frase mais longa ganha; "giant X" sobe uma classe).
_RISK_NAMES: Mapping[str, str] = {
    "harmless": (
        "ant, beetle, bee, honey bee, fly, mosquito, louse, tick, roach, cave spider, jumping "
        "spider, sparrow, wren, cardinal, blue jay, oriole, magpie, crow, raven, parakeet, "
        "lovebird, parrot, falcon, owl, penguin, duck, goose, chicken, turkey, peafowl, kiwi, "
        "stork, vulture, buzzard, eagle, blackbird, swallow, rat, hamster, squirrel, "
        "chipmunk, rabbit, hare, groundhog, hedgehog, porcupine, opossum, mongoose, stoat, "
        "weasel, mink, skunk, raccoon, armadillo, sloth, gibbon, monkey, macaque, capuchin, "
        "red panda, cat, bat, carp, trout, salmon, perch, herring, guppy, cave fish, "
        "bullhead, lamprey, ray, stingray, squid, cuttlefish, octopus, lizard, skink, anole, "
        "chameleon, gecko, iguana, frog, toad, olm, turtle, tortoise"),
    "low": (
        "jackal, coyote, fox, badger, honey badger, wolverine, beaver, otter, lynx, bobcat, "
        "ocelot, dog, deer, reindeer, gazelle, impala, ibex, mountain goat, goat, sheep, pig, "
        "llama, alpaca, emu, capybara, wombat, koala, echidna, platypus, snapping turtle, "
        "swan, emperor penguin, large rat, crundle, bugbat, brown recluse spider, bark "
        "scorpion, mandrill, langur, kingsnake, siamang, pike, cod, tigerfish, gar, eel, sea "
        "lamprey, crab, creeping eye, flesh ball, cave floater, giant cave swallow, whale "
        "shark, basking shark, fire snake, kobold, gnome, gremlin"),
    "moderate": (
        "dingo, elk, muskox, mule, donkey, horse, cow, yak, camel, tapir, warthog, boar, "
        "kangaroo, ostrich, cassowary, chimpanzee, bonobo, orangutan, panda, monitor lizard, "
        "alligator snapping turtle, giant tortoise, snake, rattlesnake, copperhead snake, "
        "adder, king cobra, black mamba, bushmaster, gila monster, spider, scorpion, giant "
        "desert scorpion, pond grabber, seal, naked mole dog, drunian, green devourer, fire "
        "imp, elk bird, human, dwarf, elf, gorlak, satyr, merperson, siren"),
    "high": (
        "wolf, ice wolf, hyena, leopard, jaguar, cougar, puma, panther, cheetah, bear, "
        "alligator, crocodile, anaconda, python, giant cave spider, giant bat, giant cave "
        "toad, giant olm, helmet snake, troglodyte, moose, water buffalo, giraffe, gorilla, "
        "walrus, elephant seal, leopard seal, shark, harpy, grimeling, strangler, molemarian, "
        "foul blendec, reacher, manera, beak dog, unicorn, griffon, centaur, goblin, "
        "hobgoblin, orc, zombie, skeleton, mummy, wraith, ghost, werebeast, vampire, "
        "necromancer"),
    "deadly": (
        "dragon, cave dragon, hydra, roc, bronze colossus, colossus, titan, forgotten beast, "
        "demon, cyclops, ettin, minotaur, bogeyman, ogre, blind cave ogre, troll, sasquatch, "
        "yeti, jabberer, nightwing, sea serpent, sea monster, voracious cave crawler, "
        "rutherer, draltha, elephant, rhinoceros, rhino, hippo, hippopotamus, lion, tiger, "
        "grizzly bear, polar bear, saltwater crocodile, cave crocodile, great white shark, "
        "orca, whale, chimera"),
}


def _build_table() -> Tuple[Dict[str, str], Tuple[str, ...]]:
    table: Dict[str, str] = {}
    clashes: List[str] = []
    for level in RISK_LEVELS:
        for raw in _RISK_NAMES.get(level, "").split(","):
            name = " ".join(raw.lower().split())
            if name in table and table[name] != level:
                clashes.append(name)                 # erro de dados: o teste apanha
            if name:                                 # em conflito vale o mais perigoso
                table[name] = max(level, table.get(name, level), key=_RANK.__getitem__)
    return table, tuple(sorted(set(clashes)))


_TABLE, TABLE_CLASHES = _build_table()
CREATURE_RISK: Mapping[str, str] = MappingProxyType(_TABLE)

#: Raças **sapientes** — espelha ``agent.candidates.SAPIENT_WORDS`` (``game`` não depende
#: de ``agent``). Casa palavra inteira (ou plural em ``s``); ``were-`` casa por prefixo.
SAPIENT_WORDS: Tuple[str, ...] = (
    "human", "dwarf", "elf", "goblin", "hobgoblin", "kobold", "orc", "troll",
    "gnome", "gremlin", "necromancer", "vampire", "ghost", "demon", "devil",
    "werebeast", "were-", "nightwraith", "phantom", "siren", "merperson",
)
_SAPIENT_EXACT = frozenset(w for w in SAPIENT_WORDS if not w.endswith("-")) | {"dwarves", "elves"}
_SAPIENT_PREFIXES = tuple(w[:-1] for w in SAPIENT_WORDS if w.endswith("-"))
_PERSON_WORDS = frozenset({"man", "men", "woman", "women"})        # "animal people"
_NOISE = frozenset({"the", "a", "an", "of", "stray", "wild", "tame", "domestic", "war",
                    "hunting", "pet", "trained", "young", "adult", "baby", "old"})
_GIANT_WORDS = frozenset({"giant", "gigantic"})
_MAX_PHRASE = max(len(name.split()) for name in _TABLE)


def _tokens(value: Any) -> List[str]:
    if not isinstance(value, str):
        return []
    return re.findall(r"[a-z]+", value[:_TEXT_CAP * 4].lower())[:12]


def _is_sapient(tokens: List[str]) -> bool:
    for token in tokens:
        if token in _SAPIENT_EXACT or token.rstrip("s") in _SAPIENT_EXACT:
            return True
        if any(token.startswith(prefix) for prefix in _SAPIENT_PREFIXES):
            return True
    return len(tokens) >= 2 and any(token in _PERSON_WORDS for token in tokens)


def _lookup(tokens: List[str]) -> Optional[str]:
    """Classe da frase mais longa conhecida (empate: a mais perigosa)."""
    if [t for t in tokens if t not in _NOISE] in (["giant"], ["giants"]):
        return "deadly"                              # a espécie "giant" (semi-megabeast)
    giant = any(t in _GIANT_WORDS for t in tokens)
    for size in range(min(len(tokens), _MAX_PHRASE), 0, -1):
        phrases = (" ".join(tokens[i:i + size]) for i in range(len(tokens) - size + 1))
        hits = [(p, _TABLE[p]) for p in phrases if p in _TABLE]
        if hits:
            phrase, found = max(hits, key=lambda hit: _RANK[hit[1]])
            if giant and found != "deadly" and not _GIANT_WORDS.intersection(phrase.split()):
                found = RISK_LEVELS[_RANK[found] + 1]     # "giant X": uma classe acima
            return found
    return None


def creature_risk(name_or_race: Any, *, kind: Optional[str] = None) -> str:
    """Classe de risco (``harmless|low|moderate|high|deadly|unknown``) por nome/raça EN.

    ``unknown`` sem dados (jamais "harmless" por palpite). Sapientes (``kind == "sapient"``,
    raças sapientes ou "animal people") nunca ficam abaixo de ``moderate``. Nunca levanta.
    """
    try:
        tokens = _tokens(name_or_race)
        level = _lookup(tokens) if tokens else None
        if kind == "sapient" or _is_sapient(tokens):
            return level if level and _RANK[level] >= _RANK["moderate"] else "moderate"
        return level or RISK_UNKNOWN
    except Exception:  # noqa: BLE001 - total por contrato
        _LOG.debug("creature_risk failed for %r", name_or_race, exc_info=True)
        return RISK_UNKNOWN


@dataclass(frozen=True)
class TargetRisk:
    """Risco de um alvo: classe, perigo grande, sapiente, nome e porquês (EN)."""

    level: str = RISK_UNKNOWN
    great_danger: bool = False
    sapient: bool = False
    name: Optional[str] = None
    reasons: Tuple[str, ...] = ()


def _field(unit: Any, key: str) -> Any:
    return unit.get(key) if isinstance(unit, Mapping) else getattr(unit, key, None)


def assess_target(unit: Any) -> TargetRisk:
    """Avalia ``VisibleUnit``/dict/nome: raça, depois nome; ``great_danger`` → ``deadly``."""
    try:
        if isinstance(unit, str):
            name, race, kind, great = _text(unit), None, None, False
        else:
            name, race = _text(_field(unit, "name")), _text(_field(unit, "race"))
            raw_kind = _field(unit, "kind")
            kind = raw_kind.lower() if isinstance(raw_kind, str) else None
            great = _field(unit, "great_danger") is True
        label = name or race
        sapient = kind == "sapient" or _is_sapient(_tokens(race) + _tokens(name))
        level = creature_risk(race, kind=kind)
        if level == RISK_UNKNOWN:
            # o nome é texto do jogo (a tradução do nome traz palavras de bichos): com raça
            # presente mas desconhecida, o nome só pode SUBIR o risco, nunca baixá-lo
            by_name = creature_risk(name, kind=kind)
            if race is None or _RANK.get(by_name, -1) >= _RANK["moderate"]:
                level = by_name
        reasons: List[str] = []
        if great:
            level = "deadly"
            reasons.append("great danger creature (megabeast/titan/forgotten beast/demon)")
        if sapient:
            reasons.append("sapient: never low risk, needs explicit human approval")
        if level == RISK_UNKNOWN:
            reasons.append(f"no risk data for '{label or 'unnamed creature'}'")
        return TargetRisk(level, great, sapient, label, tuple(reasons))
    except Exception:  # noqa: BLE001
        _LOG.debug("assess_target failed", exc_info=True)
        return TargetRisk(reasons=("target unreadable",))


# ----------------------------------------------------------------------- resultados
@dataclass(frozen=True)
class Matchup:
    """Confronto nós x alvo: ``favourable|even|risky|deadly|unknown`` + porquês (EN)."""

    level: str = "unknown"
    reasons: Tuple[str, ...] = ()
    enemy_risk: str = RISK_UNKNOWN
    power: Optional[float] = None


@dataclass(frozen=True)
class RetreatAdvice:
    """Conselho de recuo; ``urgent`` = sangue crítico ou dor de desmaio."""

    retreat: bool = False
    reasons: Tuple[str, ...] = ()
    urgent: bool = False
    blood_ratio: Optional[float] = None


@dataclass(frozen=True)
class HuntPolicy:
    """Política de caça (mudança 11): ``go`` (sem restrição) | ``prefer_low_risk`` (alvos de
    risco <= low primeiro) | ``train_first`` (sparring: parceiro sapiente → aprovação)."""

    decision: str = "go"
    reason: str = ""
    min_level: int = TRAIN_MIN_LEVEL
    skill_level: Optional[int] = None
    prey_risk: Optional[str] = None
    requires_approval: bool = False


def _total(default: Callable[[], Any]) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Método público nunca estoura para dentro do loop: erro → ``default()`` + log debug."""
    def decorate(fn: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return fn(*args, **kwargs)
            except Exception:  # noqa: BLE001
                _LOG.debug("readiness.%s failed", fn.__name__, exc_info=True)
                return default()
        return wrapper
    return decorate


_LEVEL = _int(_LEVEL_CAP)
#: (campo, secção do bloco ``combat``, chaves aceites, parser) — o contrato do Lua.
_SCHEMA: Tuple[Tuple[str, str, Tuple[str, ...], Callable[[Any], Any]], ...] = (
    ("weapon_name", "weapon", ("name",), _text),
    ("weapon_skill_token", "weapon", ("skill_token",),
     lambda v: v if isinstance(v, str) and re.fullmatch(r"[A-Z][A-Z_]{1,31}", v) else None),
    ("weapon_skill_noun", "weapon", ("skill_noun",), lambda v: _text(v, 32)),
    ("weapon_skill", "weapon", ("skill",), _LEVEL),
    ("weapon_ranged", "weapon", ("ranged",), _flag),
    ("armed", "weapon", ("armed",), _flag),
    *((n, "skills", (n,) + a, _LEVEL) for n, a in (
        ("fighter", ()), ("dodger", ()), ("shield_user", ("shield",)),
        ("armor_user", ("armor",)), ("wrestler", ()), ("striker", ()), ("kicker", ()))),
    *((n, "attributes", (n,), _int(_ATTR_CAP)) for n in (
        "strength", "agility", "toughness", "endurance", "spatial_sense",
        "kinesthetic_sense", "willpower")),
    *((n, "condition", (n,), _num) for n in (
        "pain", "exhaustion", "winded", "stunned", "nausea", "dizziness", "webbed")),
    ("blood_ratio", "condition", ("blood_ratio",), _ratio),
    ("wound_count", "condition", ("wound_count",), _int(1000.0)),
    ("bleeding_wounds", "condition", ("bleeding",),       # contagem (ou booleano legado)
     lambda v: int(v) if isinstance(v, bool) else _int(1000.0)(v)),
    ("armor_pieces", "armor", ("pieces",), _int(100.0)),
    ("armor_slots", "armor", ("slots",), _int(float(ARMOR_SLOTS_FULL))),
    ("shield_equipped", "armor", ("shield",), _flag),
)


@dataclass(frozen=True)
class CombatReadiness:
    """Retrato de prontidão (``adventurer.combat`` do Lua). Tudo opcional; nada inventado."""

    # arma equipada: nível EFETIVO da skill dela (desarmado → skill de soco)
    weapon_name: Optional[str] = None
    weapon_skill_token: Optional[str] = None    # token DF: SPEAR, SWORD, GRASP_STRIKE…
    weapon_skill_noun: Optional[str] = None     # "Spearman"
    weapon_skill: Optional[int] = None          # nível EFETIVO (0..20+)
    weapon_ranged: Optional[bool] = None
    armed: Optional[bool] = None
    # skills efetivas de combate
    fighter: Optional[int] = None
    dodger: Optional[int] = None
    shield_user: Optional[int] = None
    armor_user: Optional[int] = None
    wrestler: Optional[int] = None
    striker: Optional[int] = None
    kicker: Optional[int] = None
    # atributos (1000 = média)
    strength: Optional[int] = None
    agility: Optional[int] = None
    toughness: Optional[int] = None
    endurance: Optional[int] = None
    spatial_sense: Optional[int] = None
    kinesthetic_sense: Optional[int] = None
    willpower: Optional[int] = None
    # condição
    pain: Optional[float] = None
    exhaustion: Optional[float] = None
    winded: Optional[float] = None
    stunned: Optional[float] = None
    nausea: Optional[float] = None
    dizziness: Optional[float] = None
    webbed: Optional[float] = None
    blood_ratio: Optional[float] = None         # blood_count / blood_max (0..1)
    wound_count: Optional[int] = None
    bleeding_wounds: Optional[int] = None
    # armadura
    armor_pieces: Optional[int] = None
    armor_slots: Optional[int] = None
    shield_equipped: Optional[bool] = None

    @classmethod
    def from_dict(cls, data: Any) -> "CombatReadiness":
        """Parse tolerante do bloco ``combat`` (ou de um dict que o traga em ``combat``)."""
        try:
            d = data if isinstance(data, dict) else {}
            inner = d.get("combat")
            if isinstance(inner, dict) and not any(
                    isinstance(d.get(k), dict) for k in ("weapon", "skills", "condition")):
                d = inner
            kw: Dict[str, Any] = {}
            for field_name, section, keys, parse in _SCHEMA:
                block = d.get(section)
                block = block if isinstance(block, dict) else {}
                kw[field_name] = parse(next((block[k] for k in keys if k in block), None))
            return cls(**kw)
        except Exception:  # noqa: BLE001
            _LOG.debug("CombatReadiness.from_dict failed", exc_info=True)
            return cls()

    @property
    def known(self) -> bool:
        """Há ALGUM sinal medido? Sem isto, scores e regras ficam neutros."""
        return any(getattr(self, f.name) is not None for f in fields(self))

    # ------------------------------------------------------------------ scores
    @_total(lambda: None)
    def power_score(self) -> Optional[float]:
        """Capacidade 0..1: média ponderada do que é conhecido (``None`` sem dados).

        As skills efetivas já descontam dor/exaustão (DFHack); a condição vai à parte.
        """
        parts: List[Tuple[float, float]] = []

        def add(key: str, value: Optional[float], scale: float, factor: float = 1.0) -> None:
            if value is not None:
                parts.append((POWER_WEIGHTS[key], _unit(value / scale * factor)))

        ref = float(SKILL_REFERENCE_MAX)
        add("weapon", self.weapon_skill, ref, UNARMED_FACTOR if self.armed is False else 1.0)
        add("fighter", self.fighter, ref)
        add("dodger", self.dodger, ref)
        if self.armor_pieces != 0:                       # armor user só conta com armadura
            add("armor_user", self.armor_user, ref)
        if self.shield_equipped is not False:            # shield user só conta com escudo
            add("shield_user", self.shield_user, ref)
        if self.armed is False:                          # sem arma, a luta é de agarrões
            add("wrestler", self.wrestler, ref)
        for key in ("strength", "agility", "toughness", "endurance"):
            add(key, getattr(self, key), ATTRIBUTE_REFERENCE)
        add("armor_cover", self.armor_slots, float(ARMOR_SLOTS_FULL))
        if not parts:
            return None
        return round(sum(w * v for w, v in parts) / sum(w for w, _ in parts), 3)

    @_total(lambda: None)
    def condition_score(self) -> Optional[float]:
        """Saúde funcional 0..1 (1 = saudável): sangue x dor x exaustão x estados x sangramento."""
        statuses = [getattr(self, key) for key in STATUS_FACTOR]
        if all(v is None for v in (self.blood_ratio, self.pain, self.exhaustion,
                                   self.bleeding_wounds, *statuses)):
            return None
        score = _unit(self.blood_ratio) if self.blood_ratio is not None else 1.0
        if self.pain is not None:
            score *= 1.0 - PAIN_MAX_PENALTY * _unit(self.pain / PAIN_COLLAPSE)
        if self.exhaustion is not None:
            score *= 1.0 - EXHAUSTION_MAX_PENALTY * _unit(self.exhaustion / EXHAUSTION_COLLAPSE)
        for (key, factor), value in zip(STATUS_FACTOR.items(), statuses):
            if value is not None and value > 0:
                score *= factor
        if self.bleeding_wounds is not None:
            score *= 1.0 - min(self.bleeding_wounds * BLEED_PENALTY, 0.25)
        return round(_unit(score), 3)

    # ------------------------------------------------------------------- prompt
    @_total(list)
    def lines(self, max_lines: int = MAX_PROMPT_LINES) -> List[str]:
        """Até ``max_lines`` linhas EN compactas ("números") para o bloco de estado do Jev."""
        if not self.known:
            return []
        out: List[str] = []
        noun = self.weapon_skill_noun or self.weapon_skill_token
        skill = f" — {noun} {_lv(self.weapon_skill)}" if self.weapon_skill is not None else ""
        if self.armed is False:
            out.append("Weapon: none (unarmed)" + skill)
        elif self.weapon_name or skill:
            out.append(f"Weapon: {self.weapon_name or 'unknown'}"
                       + (" [ranged]" if self.weapon_ranged else "") + skill)

        def listed(*items: Tuple[str, Any]) -> str:
            return ", ".join(f"{label} {value}" for label, value in items if value is not None)

        combat = listed(("Fighter", None if self.fighter is None else _lv(self.fighter)),
                        ("Dodger", self.dodger), ("Shield", self.shield_user),
                        ("Armor", self.armor_user), ("Wrestler", self.wrestler),
                        *((("Striker", self.striker), ("Kicker", self.kicker))
                          if self.armed is False else ()))
        if combat:
            out.append("Combat skills (effective): " + combat)
        attrs = listed(("STR", self.strength), ("AGI", self.agility), ("TGH", self.toughness),
                       ("END", self.endurance), ("SPA", self.spatial_sense),
                       ("KIN", self.kinesthetic_sense), ("WIL", self.willpower))
        if attrs:
            out.append("Attributes (1000 = average): " + attrs)
        cond = [f"blood {round(self.blood_ratio * 100)}%"] if self.blood_ratio is not None else []
        cond += [f"{k} {v:g}" for k, v in (("pain", self.pain), ("exhaustion", self.exhaustion))
                 if v is not None]
        if any(getattr(self, k) is not None for k in STATUS_FACTOR):
            cond.append("status: " + (", ".join(k for k in STATUS_FACTOR if (getattr(self, k) or 0) > 0)
                                      or "clear"))
        if self.wound_count is not None:
            cond.append(f"wounds {self.wound_count}"
                        + (f" ({self.bleeding_wounds} bleeding)" if self.bleeding_wounds else ""))
        if cond:
            out.append("Condition: " + ", ".join(cond))
        bits = []
        if self.armor_pieces is not None:
            slots = f", {self.armor_slots}/{ARMOR_SLOTS_FULL} slots" if self.armor_slots is not None else ""
            bits.append(f"{self.armor_pieces} pieces{slots}")
        if self.shield_equipped is not None:
            bits.append("shield held" if self.shield_equipped else "no shield")
        if bits:
            out.append("Armor: " + ", ".join(bits))
        power, health = self.power_score(), self.condition_score()
        rating = [f"{name} {score:.2f} ({_band(score, bands)})" for name, score, bands in (
            ("power", power, _POWER_BANDS), ("condition", health, _CONDITION_BANDS))
            if score is not None]
        if rating:
            out.append("Readiness: " + ", ".join(rating))
        advice = self.should_retreat()
        if advice.retreat:
            out.append("Warning: " + "; ".join(advice.reasons))
        return out[:max(0, int(max_lines))]

    # ------------------------------------------------------------------ regras
    @_total(lambda: Matchup("unknown", ("matchup unavailable",)))
    def matchup(self, unit: Any, *, margin: float = MATCHUP_MARGIN) -> Matchup:
        """Nós x ``unit``: poder efetivo (power x condição) contra o risco do alvo."""
        target = assess_target(unit)
        reasons = list(target.reasons)
        power, cond = self.power_score(), self.condition_score()
        if target.level == RISK_UNKNOWN:
            return Matchup("unknown", tuple(reasons), target.level, power)
        if power is None:                                # sem dados nossos: só "deadly" fala
            reasons.append("own readiness unknown")
            level = "deadly" if target.level == "deadly" else "unknown"
            return Matchup(level, tuple(reasons), target.level, None)
        effective = power * (cond if cond is not None else 1.0)
        required = MATCHUP_REQUIRED_POWER[target.level]
        reasons.append(f"enemy risk {target.level}: needs ~{required:.2f}, we have {effective:.2f}"
                       f" (power {power:.2f}" + (f" x condition {cond:.2f})" if cond is not None else ")"))
        if target.level == "harmless" or effective >= required + margin:
            level = "favourable"
        elif effective >= required:
            level = "even"
        elif effective >= required - margin:
            level = "risky"
        else:
            level = "deadly"
        if target.great_danger and level in ("favourable", "even"):
            level = "risky"                              # megabestas: nunca melhor que "risky"
        if self.weapon_skill is not None and self.weapon_skill < TRAIN_MIN_LEVEL:
            reasons.append(f"weapon skill below {skill_label(TRAIN_MIN_LEVEL)}")
        return Matchup(level, tuple(reasons[:4]), target.level, power)

    @_total(lambda: RetreatAdvice())
    def should_retreat(self, threshold: float = RETREAT_BLOOD_RATIO, nearest: Any = None, *,
                       pain_heavy: float = PAIN_HEAVY, exhaustion_heavy: float = EXHAUSTION_HEAVY,
                       bleeding_many: int = BLEEDING_WOUNDS_MANY) -> RetreatAdvice:
        """Recuar? Sangue < ``threshold``, dor pesada, exaustão, atordoado, sangramentos
        múltiplos, desarmado perante ameaça séria, ou ``nearest`` mortal para nós.
        Sem dados (nossos e do inimigo) → ``retreat=False``: nada é assumido."""
        limit = _num(threshold, 1.0)
        limit = RETREAT_BLOOD_RATIO if limit is None else limit
        reasons: List[str] = []
        urgent = False
        if self.blood_ratio is not None and self.blood_ratio < limit:
            reasons.append(f"blood at {round(self.blood_ratio * 100)}% (< {round(limit * 100)}%)")
            urgent = self.blood_ratio < URGENT_BLOOD_RATIO
        if self.pain is not None and self.pain >= pain_heavy:
            reasons.append(f"heavy pain ({self.pain:g})")
            urgent = urgent or self.pain >= PAIN_COLLAPSE
        if self.exhaustion is not None and self.exhaustion >= exhaustion_heavy:
            reasons.append(f"exhausted ({self.exhaustion:g})")
        if (self.stunned or 0) > 0:
            reasons.append("stunned")
        if self.bleeding_wounds is not None and self.bleeding_wounds >= bleeding_many:
            reasons.append(f"{self.bleeding_wounds} bleeding wounds")
        if nearest is not None:
            target = assess_target(nearest)
            label = target.name or "the nearest hostile"
            if self.armed is False and (
                    target.great_danger or target.level in ("moderate", "high", "deadly")):
                reasons.append(f"unarmed against {label} (risk {target.level})")
            if self.matchup(nearest).level == "deadly":
                reasons.append(f"{label} is deadly for us")
        return RetreatAdvice(bool(reasons), tuple(reasons), urgent, self.blood_ratio)

    @_total(lambda: HuntPolicy(reason="hunt policy unavailable: skill unknown"))
    def hunt_policy(self, prey: Any = None, *, min_level: int = TRAIN_MIN_LEVEL,
                    train_first_gap: int = TRAIN_FIRST_GAP) -> HuntPolicy:
        """Mudança 11: ``go`` / ``prefer_low_risk`` / ``train_first`` pela skill da arma
        (a de Fighter se a da arma for desconhecida). Skill desconhecida → ``go``."""
        skill = self.weapon_skill if self.weapon_skill is not None else self.fighter
        target = assess_target(prey) if prey is not None else None
        risk = target.level if target is not None else None
        if target is not None and target.sapient:
            return HuntPolicy("prefer_low_risk", "sapient target: never a hunt target, "
                              "pick a non-sapient animal", min_level, skill, risk)
        if skill is None:
            return HuntPolicy("go", "skill unknown: not blocking the hunt", min_level, None, risk)
        shown, bar = f"{skill_label(skill)} ({skill})", f"{skill_label(min_level)} ({min_level})"
        if skill >= min_level:
            return HuntPolicy("go", f"weapon skill {shown} >= {bar}", min_level, skill, risk)
        below = f"weapon skill {shown} is below {bar}"
        if risk in ("harmless", "low"):
            return HuntPolicy("go", f"{below}, but the prey is {risk} risk (it also trains)",
                              min_level, skill, risk)
        if min_level - skill >= train_first_gap or risk in ("high", "deadly"):
            return HuntPolicy("train_first", f"{below}: train (spar) before a {risk or 'any'}-risk "
                              "hunt", min_level, skill, risk, SPARRING_REQUIRES_APPROVAL)
        return HuntPolicy("prefer_low_risk", f"{below}: prefer low-risk prey first",
                          min_level, skill, risk)

    def annotate_attack(self, unit: Any) -> str:
        """Atalho de :func:`annotate_attack` com este retrato."""
        return annotate_attack(unit, self)


def _band(value: float, bands: Tuple[Tuple[float, str], ...]) -> str:
    return next(name for limit, name in bands if value < limit)


# --------------------------------------------------------------- frases p/ candidatos
def describe_target_risk(unit: Any) -> str:
    """``"enemy risk: high (great danger)"``; vazio para ``None``. Nunca levanta."""
    if unit is None:
        return ""
    target = assess_target(unit)
    tags = [t for t, on in (("great danger", target.great_danger), ("sapient", target.sapient)) if on]
    return f"enemy risk: {target.level}" + (f" ({', '.join(tags)})" if tags else "")


def annotate_attack(unit: Any, readiness: Optional[CombatReadiness] = None) -> str:
    """Frase curta p/ anexar a uma ação de ataque, ex.:
    ``"our spear skill: Proficient, enemy risk: high, matchup: risky"``.

    Sem ``readiness`` (ou sem dados) só leva o risco do inimigo. Nunca levanta.
    """
    try:
        parts: List[str] = []
        mine = readiness if isinstance(readiness, CombatReadiness) else None
        if mine is not None and mine.weapon_skill is not None:
            token = (mine.weapon_skill_token or "").lower().replace("_", " ")
            what = "unarmed" if mine.armed is False else (token or "weapon")
            parts.append(f"our {what} skill: {skill_label(mine.weapon_skill)}")
        risk = describe_target_risk(unit)
        if risk:
            parts.append(risk)
        if mine is not None and mine.known and unit is not None:
            verdict = mine.matchup(unit).level
            if verdict != "unknown":
                parts.append(f"matchup: {verdict}")
        return ", ".join(parts)
    except Exception:  # noqa: BLE001
        _LOG.debug("annotate_attack failed", exc_info=True)
        return ""
