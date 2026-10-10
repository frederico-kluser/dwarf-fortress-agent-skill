"""Shuffle + voto nas opções de decisão do JEV-A (Mudança 05, ``ARQUITETURA.md`` §8).

Porquê: um modelo inclina-se para a **1.ª opção** de uma lista (viés de
posição). Uma só leitura não diz se a resposta é *robusta* à ordem ou apenas a
opção que calhou primeiro; aqui o mesmo passo é lido N vezes com **permutações
distintas** e as respostas votam. A concordância mede a estabilidade no próprio
passo e um palpite posicional nunca chega a ``auto`` (cada permutação põe um
candidato diferente à cabeça: o palpite parte-se e a confiança agregada cai).

Peças: ``build_vote_requests`` (N pedidos, saída sempre última) · ``cast_votes``
(corre-os; nunca levanta) · ``tally_votes`` (des-baralha e conta) ·
``VoteOutcome.as_decision_result`` (um ``DecisionResult`` verdadeiro: o loop —
``_record_decision``/``_read_action``/``_routing_reason`` — fica **sem
mudanças**) · ``vote_count_for`` (só decisões com consequências votam).

Decisões (finais)
-----------------
* **n = 3**, configurável em 1..5. Com ``n <= nº de candidatos`` cada voto tem
  uma **1.ª posição diferente** (o viés a expor é o da 1.ª opção); a última
  posição real equilibra-se em *best effort*.
* **Vencedor** = maior massa de probabilidade somada (cada voto vale massa 1:
  as suas probabilidades normalizadas, ou um *one-hot* quando faltam) **e**
  maioria estrita dos votos válidos. Empate, sem maioria, ou massa e votos em
  desacordo ⇒ sem vencedor seguro ⇒ a chave de saída. Zero votos válidos: idem.
* **Confiança agregada** = média da confiança dos votos que concordam com o
  líder × (votos do líder / votos lançados): com a votação completa é «média ×
  concordância» e um 2/3 nunca chega a ``auto`` (0.67 × conf < 0.90). Um voto
  falhado baixa o teto (não se presume que concordaria). Confiança
  indisponível num voto concordante ⇒ agregada ``None`` (teto ``hitl``).
* **needs_human** = o MÁXIMO ``p``; **urgência** = o nível mais urgente.
* O voto **nunca baixa um limiar**: não decide bandas, só produz uma confiança
  que o loop passa pelos mesmos limiares de sempre.

Sem rede, jogo ou relógio (semente e ``decide`` injetados); o que o loop chama
nunca levanta: entrada inválida ⇒ ``[]``/resultado neutro + ``warning``.
"""
from __future__ import annotations

import itertools
import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Mapping, Sequence

from .bands import (
    DEFAULT_AUTO_THRESHOLD,
    DEFAULT_HITL_THRESHOLD,
    band_for_choice_or_score,
    band_for_noul,
    validate_thresholds,
)
from .client import Answer, DecisionResult, Usage, money
from .fleet import build_action, unshuffle

log = logging.getLogger("system_one_dwarf.jev.voting")

__all__ = [
    "CRITICAL_URGENCY_SCORE",
    "CRITICAL_VERBS",
    "DEFAULT_EXIT_KEY",
    "DEFAULT_VOTES",
    "MAX_ISSUES",
    "MAX_VOTES",
    "MIN_VOTES",
    "TIE_EPSILON",
    "VoteBallot",
    "VoteOutcome",
    "build_vote_requests",
    "cast_votes",
    "tally_votes",
    "vote_count_for",
]

#: Votos por omissão numa decisão com consequências (Mudança 05: «2–3 permutações»).
DEFAULT_VOTES = 3
MIN_VOTES = 1
#: Teto: acima disto o custo/latência deixam de compensar.
MAX_VOTES = 5
#: Chave da opção de saída do ``build_action`` (sempre a última apresentada).
DEFAULT_EXIT_KEY = "other"
#: Verbos cuja escolha tem consequências (``MacroAction.verb``): só estes votam.
CRITICAL_VERBS: tuple[str, ...] = ("attack", "flee", "drink_potion", "hunt")
#: Urgência numérica a partir da qual conta como *Critical*: ponto médio entre
#: ``Pressing`` (1) e ``Critical`` (2) da régua de 3 níveis do JEV-A.
CRITICAL_URGENCY_SCORE = 1.5
#: Duas massas somadas a menos disto são um empate (ruído de vírgula flutuante).
TIE_EPSILON = 1e-9
#: Máximo de textos de ``issues`` (marcas do voto + os herdados dos votos).
MAX_ISSUES = 8

_PERMUTATION_RETRIES = 64
_ROUND = 6
#: Mesmos ids que ``MacroAction.is_exit`` trata como opção de saída.
_EXIT_IDS = frozenset({"other", "none", "none_of_the_above"})


# --------------------------------------------------------------------------- #
# pequenos helpers tolerantes
# --------------------------------------------------------------------------- #
def _field(obj: Any, name: str, default: Any = None) -> Any:
    """Atributo ou chave: os fakes dos testes e o cliente real divergem."""
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _number(value: Any) -> float | None:
    """Número finito (``bool``/``NaN``/``inf``/texto ⇒ ``None``)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return None if math.isnan(number) or math.isinf(number) else number


def _unit_value(value: Any) -> float | None:
    """Número em ``[0, 1]`` (o ``p`` de um ``noul``); fora disso ⇒ ``None``."""
    number = _number(value)
    return number if number is not None and 0.0 <= number <= 1.0 else None


def _clean_confidence(value: Any) -> float | None:
    """Confiança **utilizável** ou ``None`` (indisponível).

    Mesma regra do cliente e do loop: ausente, ``bool``, ``NaN``, fora de
    ``[0, 1]`` e ``0.0`` (o «sem confiança» dos fallbacks) são indisponíveis —
    nunca se leem como «distribuição plana».
    """
    number = _unit_value(value)
    return None if number is None or number == 0.0 else number


def _clamp_votes(value: Any, default: int = DEFAULT_VOTES) -> int:
    """``value`` limitado a ``MIN_VOTES..MAX_VOTES`` (lixo ⇒ ``default``)."""
    number = _number(value)
    return default if number is None else max(MIN_VOTES, min(MAX_VOTES, int(number)))


def _permutation_cap(size: int, wanted: int) -> int:
    """``min(wanted, size!)`` sem calcular fatoriais gigantes."""
    total = 1
    for factor in range(2, max(size, 1) + 1):
        total *= factor
        if total >= wanted:
            return wanted
    return min(total, wanted)


def _candidate_id(candidate: Any) -> str:
    if isinstance(candidate, str):
        return candidate.strip()
    return str(_field(candidate, "id", "") or "").strip()


def _candidate_verb(candidate: Any) -> str:
    """Verbo semântico: ``verb`` da macro-ação, senão o prefixo do ``id``."""
    verb = None if isinstance(candidate, str) else _field(candidate, "verb", None)
    if isinstance(verb, str) and verb.strip():
        return verb.strip().lower()
    return _candidate_id(candidate).split(":", 1)[0].strip().lower()


def _is_exit_candidate(candidate: Any, exit_key: str) -> bool:
    if not isinstance(candidate, str) and _field(candidate, "is_exit", False) is True:
        return True
    identifier = _candidate_id(candidate)
    return identifier == exit_key or identifier.lower() in _EXIT_IDS


# --------------------------------------------------------------------------- #
# pedidos: N permutações distintas
# --------------------------------------------------------------------------- #
def _jev_candidates(candidates: Any, exit_key: str) -> list[dict[str, Any]]:
    """Candidatos → ``[{id, name, description}]``, sem a opção de saída.

    Aceita mapeamentos e macro-ações (``as_jev_candidate()``). A opção de saída
    é descartada em silêncio: o ``build_action`` acrescenta a sua e recusa uma
    candidata com esse ``id`` — a lista inteira do gerador pode vir sem filtro.
    """
    if isinstance(candidates, (str, bytes)) or not isinstance(candidates, Iterable):
        raise ValueError("candidates must be a sequence of {id, name, description}")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for position, candidate in enumerate(candidates, 1):
        if _is_exit_candidate(candidate, exit_key):
            continue
        as_jev = getattr(candidate, "as_jev_candidate", None)
        if callable(as_jev):
            item = dict(as_jev())
        elif isinstance(candidate, Mapping):
            item = dict(candidate)
        else:
            raise ValueError(f"candidate {position} must be a mapping or a macro-action")
        identifier = str(item.get("id") or "").strip()
        if not identifier or identifier in seen:
            raise ValueError(f"candidate {position} has an empty or duplicate id")
        seen.add(identifier)
        out.append(item)
    if not out:
        raise ValueError("voting needs at least one non-exit candidate")
    return out


def _resolve_seed(base_seed: Any) -> int | str:
    """Semente do voto: a dada (``int``/``str``) ou uma fresca e registável."""
    if isinstance(base_seed, int) and not isinstance(base_seed, bool):
        return base_seed
    if isinstance(base_seed, str) and base_seed:
        return base_seed
    return random.SystemRandom().getrandbits(48)


def _distinct_orders(ids: Sequence[str], count: int, rng: random.Random) -> list[tuple[str, ...]]:
    """Até ``count`` ordens **distintas** de ``ids`` (reprodutíveis pela semente).

    A 1.ª posição roda por uma ordem sorteada: com ``count <= len(ids)`` cada
    voto tem um candidato diferente à cabeça (distinção garantida por
    construção). Com mais votos do que candidatos repete-se a cabeça e o resto
    reordena-se até ser distinto; a última posição real equilibra-se em *best
    effort*. O que não for sorteável em ``_PERMUTATION_RETRIES`` tentativas
    enumera-se, pelo que termina sempre.
    """
    size = len(ids)
    heads = list(ids)
    rng.shuffle(heads)
    orders: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    lasts: set[str] = set()
    for index in range(_permutation_cap(size, max(1, count))):
        head = heads[index % size]
        rest = [item for item in ids if item != head]
        order: tuple[str, ...] | None = None
        for _ in range(_PERMUTATION_RETRIES):
            rng.shuffle(rest)
            tail = list(rest)
            if len(tail) >= 2 and tail[-1] in lasts:
                for position, item in enumerate(tail[:-1]):
                    if item not in lasts:
                        tail[position], tail[-1] = tail[-1], tail[position]
                        break
            if (head, *tail) not in seen:
                order = (head, *tail)
                break
        if order is None:
            order = next(perm for perm in itertools.permutations(ids) if perm not in seen)
        seen.add(order)
        lasts.add(order[-1])
        orders.append(order)
    return orders


def build_vote_requests(
    candidates: Any,
    state_summary: str,
    n: int = DEFAULT_VOTES,
    base_seed: int | str | None = None,
    builder: Callable[..., Mapping[str, Any]] = build_action,
    **builder_kwargs: Any,
) -> list[dict[str, Any]]:
    """N pedidos do JEV-A, cada um com uma **permutação distinta** dos candidatos.

    Cada pedido sai do ``builder`` (por omissão :func:`..fleet.build_action`)
    com a lista já permutada — estado e rótulos ``A1..An`` seguem a permutação
    — e ``shuffle=False``: a ordem do ``choice`` é a que o estado mostra e a
    saída fica sempre por último. ``ordering["action"]`` regista a ordem
    apresentada (des-baralhar respostas posicionais) e ``meta["vote"]`` a
    semente e o índice do voto (auditoria).

    Args:
        candidates: macro-ações ou ``[{id, name, description}]``.
        state_summary: resumo EN do estado (igual em todos os votos).
        n: nº de votos (``1..5``); com menos de ``n`` ordens possíveis (``k!``)
            saem menos pedidos — nunca repetidos.
        base_seed: ``None`` ⇒ semente fresca (em ``meta.vote.seed``);
            ``int``/``str`` ⇒ as mesmas permutações sempre.
        builder: construtor do pedido (injeção/testes); tem de aceitar o
            argumento ``shuffle`` como o ``build_action``.
        **builder_kwargs: repassados ao ``builder`` (``urgency_levels``,
            ``exit_key``…). ``shuffle=False`` = «ordem canónica» e reduz o voto
            a **1 pedido**; ``shuffle_seed`` é ignorado.

    Returns:
        Os pedidos, ou ``[]`` se a entrada for inválida (o chamador volta ao
        pedido único). **Nunca levanta.**
    """
    try:
        kwargs = dict(builder_kwargs)
        shuffle = kwargs.pop("shuffle", True)
        kwargs.pop("shuffle_seed", None)
        items = _jev_candidates(candidates, str(kwargs.get("exit_key") or DEFAULT_EXIT_KEY))
        by_id = {item["id"]: item for item in items}
        ids = list(by_id)
        seed = _resolve_seed(base_seed)
        orders = (
            [tuple(ids)] if shuffle is False
            else _distinct_orders(ids, _clamp_votes(n), random.Random(seed))
        )
        kwargs["shuffle"] = False  # a ordem já vem permutada: o builder não baralha
        requests: list[dict[str, Any]] = []
        for index, order in enumerate(orders):
            built = dict(builder([by_id[item] for item in order], state_summary, **kwargs))
            ordering = dict(built.get("ordering") or {})
            if not ordering.get("action"):  # a ordem apresentada é a do ``criteria``
                criteria = ((built.get("questions") or {}).get("action") or {}).get("criteria")
                if isinstance(criteria, Mapping):
                    ordering["action"] = [str(key) for key in criteria]
            meta = dict(built.get("meta") or {})
            meta["vote"] = {"index": index, "of": len(orders), "seed": seed, "order": list(order)}
            requests.append({**built, "ordering": ordering, "meta": meta})
        return requests
    except Exception as exc:  # noqa: BLE001 - o loop volta ao pedido único
        log.warning("build_vote_requests failed (%s: %s); falling back to a single request",
                    type(exc).__name__, exc)
        return []


def cast_votes(
    decide: Callable[[Mapping[str, Any]], Any],
    requests: Sequence[Mapping[str, Any]],
    *,
    map_fn: Callable[[Callable[[Any], Any], Sequence[Any]], Iterable[Any]] | None = None,
) -> list[Any]:
    """Corre ``decide(request)`` por pedido e devolve os resultados **alinhados**.

    Um voto que falha não derruba os outros: o seu lugar fica com a própria
    exceção (``tally_votes`` conta-a como voto falhado e regista o tipo).
    ``map_fn(call, requests)`` permite paralelizar (``ThreadPoolExecutor.map``);
    por omissão corre em série. **Nunca levanta** (salvo ``KeyboardInterrupt``).
    """
    def call(request: Any) -> Any:
        try:
            return decide(request)
        except Exception as exc:  # noqa: BLE001 - a fronteira é o decider injetado
            return exc

    pending = list(requests or ())
    try:
        return list((map_fn or map)(call, pending))
    except Exception as exc:  # noqa: BLE001 - um mapper partido não pode matar o passo
        log.warning("cast_votes mapper failed (%s: %s)", type(exc).__name__, exc)
        return [exc for _ in pending]


# --------------------------------------------------------------------------- #
# apuramento
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class VoteBallot:
    """Um voto, já traduzido para ids canónicos de ação.

    ``valid`` é falso quando o voto falhou (``reason``: ``no_result``,
    ``error:<Tipo>``, ``no_answers``, ``no_action_answer``, ``unknown_option``).
    ``position`` é o índice 0-based da opção escolhida na ordem **apresentada
    neste voto**; ``probabilities`` vem com chaves canónicas e normalizada
    (``None`` quando o voto não a trouxe).
    """

    index: int
    valid: bool
    action: str | None = None
    raw_value: Any = None
    confidence: float | None = None
    probabilities: Mapping[str, float] | None = None
    position: int | None = None
    order: tuple[str, ...] = ()
    needs_human: float | None = None
    urgency: float | None = None
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    reason: str | None = None
    issues: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoteOutcome:
    """Resultado do apuramento de N votos de uma mesma decisão.

    ``winner`` é o id vencedor, ou ``exit_key`` sem vencedor seguro (empate,
    sem maioria, massa/votos em desacordo, sem votos válidos); ``consensus``
    diz se há vencedor seguro (a própria saída pode ter consenso).
    ``agreement`` = parte dos votos **válidos** que escolheram ``winner``;
    ``confidence`` = média das confianças dos votos do líder × (votos do líder /
    lançados), ``None`` se indisponível; ``split`` = ≥ 2 válidos e não
    unânimes; ``position_stability`` = parte dos válidos que repetem a escolha
    mais votada; ``first_position_rate`` = parte dos válidos que escolheram a
    opção apresentada em 1.º (≈ 1 ⇒ viés de posição); ``tally`` = massa somada
    por ação; ``votes`` = contagem de escolhas; ``needs_human`` = MÁXIMO ``p``;
    ``urgency`` = o nível mais alto.
    """

    winner: str
    exit_key: str = DEFAULT_EXIT_KEY
    consensus: bool = False
    votes_cast: int = 0
    votes_valid: int = 0
    agreement: float = 0.0
    confidence: float | None = None
    split: bool = False
    tie: bool = False
    position_stability: float = 0.0
    first_position_rate: float | None = None
    tally: Mapping[str, float] = field(default_factory=dict)
    votes: Mapping[str, int] = field(default_factory=dict)
    needs_human: float | None = None
    urgency: float | None = None
    ballots: tuple[VoteBallot, ...] = ()
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    model: str | None = None
    provider: str | None = None
    request_id: str | None = None
    attempts: int = 1
    issues: tuple[str, ...] = ()

    @property
    def votes_failed(self) -> int:
        """Votos sem resposta utilizável (falhados ou ilegíveis)."""
        return self.votes_cast - self.votes_valid

    def as_dict(self) -> dict[str, Any]:
        """Resumo JSON-friendly e pequeno (cai em ``DecisionResult.raw['vote']``)."""
        def rounded(value: float | None) -> float | None:
            return None if value is None else round(value, _ROUND)

        return {
            "winner": self.winner,
            "consensus": self.consensus,
            "votes_cast": self.votes_cast,
            "votes_valid": self.votes_valid,
            "agreement": rounded(self.agreement),
            "confidence": rounded(self.confidence),
            "split": self.split,
            "tie": self.tie,
            "position_stability": rounded(self.position_stability),
            "first_position_rate": rounded(self.first_position_rate),
            "tally": {key: rounded(value) for key, value in self.tally.items()},
            "votes": dict(self.votes),
            "needs_human": self.needs_human,
            "urgency": self.urgency,
            "ballots": [
                {"index": b.index, "valid": b.valid, "action": b.action,
                 "confidence": b.confidence, "position": b.position, "reason": b.reason}
                for b in self.ballots
            ],
            "issues": list(self.issues),
        }

    def as_decision_result(
        self,
        template: Any = None,
        *,
        auto_threshold: float = DEFAULT_AUTO_THRESHOLD,
        hitl_threshold: float = DEFAULT_HITL_THRESHOLD,
    ) -> DecisionResult:
        """Um :class:`~.client.DecisionResult` verdadeiro com as respostas agregadas.

        ``answers``: ``action`` (``choice``: vencedor, confiança agregada,
        probabilidades médias), ``urgency`` (``score``: o mais urgente) e
        ``needs_human`` (``noul``: o MÁXIMO ``p``). ``usage`` é a **soma** dos
        votos, ``latency_ms`` o máximo (correm em paralelo) e ``issues`` leva
        ``vote:3/3`` / ``vote_split:2/3`` / ``vote_failed:1/3``. Com **zero
        votos válidos** ``action`` fica ``value=None`` (pergunta não
        respondida): o loop escala, não lê «o JEV escolheu a saída».

        ``template`` (um resultado de voto) cede ``model``/``provider``/
        ``request_id``/``raw``; os limiares só preenchem o ``band``
        informativo (o loop recalcula a banda com a sua configuração).
        """
        try:
            validate_thresholds(auto_threshold, hitl_threshold)
        except Exception:  # noqa: BLE001 - limiares inválidos: usa os por omissão
            auto_threshold, hitl_threshold = DEFAULT_AUTO_THRESHOLD, DEFAULT_HITL_THRESHOLD
        vote_raw = self.as_dict()
        answered = self.votes_valid > 0
        probabilities = (
            {key: round(value / self.votes_valid, _ROUND) for key, value in self.tally.items()}
            if answered else None
        )
        common = {"legend": None, "chosen_probability": None}
        answers = {
            "action": Answer(
                name="action", type="choice", value=self.winner if answered else None,
                confidence=self.confidence, probabilities=probabilities,
                band=band_for_choice_or_score(self.confidence, auto_threshold, hitl_threshold),
                raw={"vote": vote_raw}, issues=self.issues,
                **{**common, "chosen_probability": (probabilities or {}).get(self.winner)},
            ),
            "urgency": Answer(
                name="urgency", type="score", value=self.urgency, confidence=None,
                probabilities=None, band=band_for_choice_or_score(None), raw={"vote": {"aggregation": "max"}},
                **common,
            ),
            "needs_human": Answer(
                name="needs_human", type="noul", value=self.needs_human, confidence=None,
                probabilities=None, band=band_for_noul(self.needs_human, auto_threshold, hitl_threshold),
                raw={"vote": {"aggregation": "max"}}, **common,
            ),
        }
        base_raw = _field(template, "raw", None)
        raw: dict[str, Any] = dict(base_raw) if isinstance(base_raw, Mapping) else {}
        raw["vote"] = vote_raw
        return DecisionResult(
            answers=answers, usage=self.usage, latency_ms=int(self.latency_ms),
            model=_field(template, "model", None) or self.model, raw=raw,
            request_id=_field(template, "request_id", None) or self.request_id,
            provider=_field(template, "provider", None) or self.provider,
            attempts=int(self.attempts), issues=tuple(self.issues),
        )


def _request_order(request: Any) -> tuple[str, ...]:
    """Ordem de apresentação das opções neste pedido (``ordering``/``criteria``)."""
    ordering = _field(request, "ordering", None)
    order = ordering.get("action") if isinstance(ordering, Mapping) else None
    if isinstance(order, Sequence) and not isinstance(order, (str, bytes)) and order:
        return tuple(str(key) for key in order)
    questions = _field(request, "questions", None)
    question = questions.get("action") if isinstance(questions, Mapping) else None
    criteria = _field(question, "criteria", None)
    return tuple(str(key) for key in criteria) if isinstance(criteria, Mapping) else ()


def _resolve_option(value: Any, order: Sequence[str]) -> str | None:
    """Chave canónica da opção a que ``value`` se refere, ou ``None``.

    Aceita a chave (o caso normal) e, por tolerância, a **posição** apresentada
    (``"opt_2"``/``2``), que se des-baralha pela ordem deste pedido.
    """
    if isinstance(value, str) and value.strip() in order:
        return value.strip()
    if not order:
        return value.strip() if isinstance(value, str) and value.strip() else None
    try:
        return unshuffle(value, order)
    except Exception:  # noqa: BLE001 - JevRequestError: nem chave nem posição válidas
        return None


def _clean_probabilities(raw: Any, order: Sequence[str]) -> dict[str, float] | None:
    """Probabilidades com chaves canónicas, ``>= 0`` e **normalizadas** (soma 1).

    Cada voto pesa o mesmo: um voto cujas probabilidades somam 0.6 não vale
    menos do que outro que soma 1.0. Sem entradas utilizáveis ⇒ ``None``.
    """
    if not isinstance(raw, Mapping):
        return None
    mass: dict[str, float] = {}
    for key, value in raw.items():
        number, option = _number(value), _resolve_option(key, order)
        if option is not None and number is not None and number >= 0.0:
            mass[option] = mass.get(option, 0.0) + number
    total = sum(mass.values())
    return {key: value / total for key, value in mass.items()} if total > 0.0 else None


def _usage_of(result: Any) -> Usage:
    usage = _field(result, "usage", None)
    if usage is None:
        return Usage()
    return Usage(
        input_tokens=int(_number(_field(usage, "input_tokens", 0)) or 0),
        output_tokens=int(_number(_field(usage, "output_tokens", 0)) or 0),
        cost_usd=money(_number(_field(usage, "cost_usd", 0.0)) or 0.0),
    )


def _read_ballot(index: int, result: Any, request: Any) -> VoteBallot:
    """Lê um voto contra o SEU pedido (a ordem apresentada é a deste voto)."""
    order = _request_order(request)
    if result is None:
        return VoteBallot(index=index, valid=False, order=order, reason="no_result")
    if isinstance(result, BaseException):
        return VoteBallot(index=index, valid=False, order=order,
                          reason=f"error:{type(result).__name__}")
    answers = _field(result, "answers", None)
    if not isinstance(answers, Mapping):
        return VoteBallot(index=index, valid=False, order=order, reason="no_answers")

    # Mesmo um voto sem ação utilizável custou dinheiro e pode estar preocupado
    # (needs_human) ou achar urgente: conta para a soma e para o máximo.
    carried: dict[str, Any] = {
        "usage": _usage_of(result),
        "latency_ms": int(_number(_field(result, "latency_ms", 0)) or 0),
        "needs_human": _unit_value(_field(answers.get("needs_human"), "value", None)),
        "urgency": _number(_field(answers.get("urgency"), "value", None)),
    }
    action = answers.get("action")
    if action is None:
        return VoteBallot(index=index, valid=False, order=order, reason="no_action_answer", **carried)
    raw_value = _field(action, "value", None)
    option = _resolve_option(raw_value, order)
    if option is None:
        return VoteBallot(index=index, valid=False, order=order, raw_value=raw_value,
                          reason="unknown_option", **carried)
    issues = _field(action, "issues", ()) or ()
    return VoteBallot(
        index=index, valid=True, action=option, raw_value=raw_value, order=order,
        confidence=_clean_confidence(_field(action, "confidence", None)),
        probabilities=_clean_probabilities(_field(action, "probabilities", None), order),
        position=order.index(option) if option in order else None,
        issues=tuple(str(issue) for issue in issues if isinstance(issue, str)),
        **carried,
    )


def _resolve_exit(exit_key: Any, requests: Sequence[Any]) -> str:
    """Chave de saída: a dada, senão o ``meta.exit_key`` dos pedidos, senão ``other``."""
    if isinstance(exit_key, str) and exit_key.strip():
        return exit_key.strip()
    for request in requests:
        meta = _field(request, "meta", None)
        candidate = meta.get("exit_key") if isinstance(meta, Mapping) else None
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return DEFAULT_EXIT_KEY


def tally_votes(
    results: Sequence[Any],
    requests: Sequence[Mapping[str, Any]],
    exit_key: str | None = None,
) -> VoteOutcome:
    """Apura os votos de um mesmo passo e devolve um :class:`VoteOutcome`.

    ``results[i]`` responde a ``requests[i]`` (um :class:`~.client.DecisionResult`;
    ``None`` ou uma exceção contam como voto falhado). Respostas posicionais
    (``"opt_2"``) são des-baralhadas pela ordem do próprio pedido. Votos
    falhados/ilegíveis são ignorados e **baixam o teto** da confiança agregada.
    ``exit_key=None`` ⇒ ``meta.exit_key`` dos pedidos, senão ``"other"``.

    **Nunca levanta**: entrada impossível ⇒ resultado sem votos válidos
    (vencedor = chave de saída; ``issues`` explica).
    """
    try:
        return _tally(list(results or ()), list(requests or ()), exit_key)
    except Exception as exc:  # noqa: BLE001 - o loop nunca morre num apuramento
        log.warning("tally_votes failed (%s: %s)", type(exc).__name__, exc)
        exit_name = exit_key if isinstance(exit_key, str) and exit_key else DEFAULT_EXIT_KEY
        return VoteOutcome(winner=exit_name, exit_key=exit_name,
                           issues=(f"vote_error:{type(exc).__name__}",))


def _first_text(items: Iterable[Any], name: str) -> str | None:
    for item in items:
        value = _field(item, name, None)
        if isinstance(value, str) and value:
            return value
    return None


def _tally(results: list[Any], requests: list[Any], exit_key: Any) -> VoteOutcome:
    exit_name = _resolve_exit(exit_key, requests)
    cast = len(requests)
    ballots = tuple(
        _read_ballot(index, results[index] if index < len(results) else None, request)
        for index, request in enumerate(requests)
    )
    valid = [ballot for ballot in ballots if ballot.valid]

    # Cada voto vale massa 1: as suas probabilidades (normalizadas) ou um
    # one-hot na escolha — «soma de probabilidades, com plurality como recurso»
    # sem misturar escalas.
    votes: dict[str, int] = {}
    mass: dict[str, float] = {}
    for ballot in valid:
        votes[ballot.action] = votes.get(ballot.action, 0) + 1  # type: ignore[index]
        for key, value in (ballot.probabilities or {ballot.action: 1.0}).items():
            mass[key] = mass.get(key, 0.0) + value  # type: ignore[arg-type]

    winner, consensus, tie = exit_name, False, False
    if valid:
        ranked = sorted(mass.items(), key=lambda item: (-item[1], item[0]))
        top_action, top_mass = ranked[0]
        tie = len(ranked) > 1 and abs(ranked[1][1] - top_mass) <= TIE_EPSILON
        if not tie and votes.get(top_action, 0) * 2 > len(valid):
            winner, consensus = top_action, True

    # O «líder» dá a confiança: o vencedor com consenso; sem consenso, o mais
    # votado (desempate pela massa) — uma votação partida mostra uma confiança
    # honestamente baixa (abstain) em vez de «indisponível».
    leader: str | None = None
    if valid:
        leader = winner if consensus else max(
            votes, key=lambda key: (votes[key], mass.get(key, 0.0), key)
        )
    backers = [ballot for ballot in valid if ballot.action == leader]
    confidence: float | None = None
    if backers and all(ballot.confidence is not None for ballot in backers):
        mean = sum(ballot.confidence for ballot in backers) / len(backers)  # type: ignore[misc]
        raw_confidence = min(1.0, mean * (len(backers) / cast))
        # Arredonda o ruído de vírgula flutuante (0.9499999999999998), sem nunca
        # transformar um valor real pequeno em 0.0 («indisponível»).
        confidence = round(raw_confidence, _ROUND) or raw_confidence

    agreement = votes.get(winner, 0) / len(valid) if valid else 0.0
    split = len(valid) >= 2 and agreement < 1.0

    issues: list[str] = []
    if not valid:
        issues.append(f"vote_failed:{cast}/{cast}")
    else:
        mark = "vote_split" if split else "vote"
        issues.append(f"{mark}:{votes.get(leader, 0) if leader else 0}/{len(valid)}")
        if tie:
            issues.append("vote_tie")
        if not consensus:
            issues.append("vote_no_consensus")
        if cast > len(valid):
            issues.append(f"vote_failed:{cast - len(valid)}/{cast}")
        if confidence is None:
            issues.append("vote_confidence_unavailable")
    for ballot in ballots:
        for issue in ballot.issues:
            if issue not in issues and len(issues) < MAX_ISSUES:
                issues.append(issue)

    needs = [b.needs_human for b in ballots if b.needs_human is not None]
    urgent = [b.urgency for b in ballots if b.urgency is not None]
    sources = [r for r in results if r is not None and not isinstance(r, BaseException)]
    return VoteOutcome(
        winner=winner, exit_key=exit_name, consensus=consensus, votes_cast=cast,
        votes_valid=len(valid), agreement=agreement, confidence=confidence,
        split=split, tie=tie,
        position_stability=max(votes.values()) / len(valid) if valid else 0.0,
        first_position_rate=(
            sum(1 for b in valid if b.position == 0) / len(valid) if valid else None
        ),
        tally={key: round(value, _ROUND) for key, value in sorted(mass.items())},
        votes=dict(sorted(votes.items())),
        needs_human=max(needs) if needs else None,
        urgency=max(urgent) if urgent else None,
        ballots=ballots,
        usage=Usage(
            input_tokens=sum(b.usage.input_tokens for b in ballots),
            output_tokens=sum(b.usage.output_tokens for b in ballots),
            cost_usd=money(sum(b.usage.cost_usd for b in ballots)),
            raw={"ballots": cast},
        ),
        latency_ms=max((b.latency_ms for b in ballots), default=0),
        model=_first_text(sources, "model"),
        provider=_first_text(sources, "provider"),
        request_id=_first_text(sources, "request_id"),
        attempts=sum(int(_number(_field(src, "attempts", 1)) or 1) for src in sources) or 1,
        issues=tuple(issues),
    )


# --------------------------------------------------------------------------- #
# quando votar
# --------------------------------------------------------------------------- #
def _is_critical_urgency(urgency: Any) -> bool:
    """``Critical`` por nome, por nível (``>= CRITICAL_URGENCY_SCORE``) ou por resposta."""
    if urgency is None or isinstance(urgency, bool):
        return False
    if isinstance(urgency, (int, float)):
        number = _number(urgency)
        return number is not None and number >= CRITICAL_URGENCY_SCORE
    if isinstance(urgency, str):
        text = urgency.strip().lower()
        try:
            return float(text) >= CRITICAL_URGENCY_SCORE
        except ValueError:
            return "critical" in text
    if isinstance(urgency, Mapping) or hasattr(urgency, "value"):
        return _is_critical_urgency(_field(urgency, "value", None))
    return False


def vote_count_for(
    candidates: Any,
    *,
    configured: int = DEFAULT_VOTES,
    critical_verbs: Iterable[str] = CRITICAL_VERBS,
    urgency: Any = None,
) -> int:
    """Quantos votos esta decisão merece (``1..5``): >1 só se tiver consequências.

    Votar custa ``n`` chamadas; uma decisão rotineira (``explore``/``wait``/
    ``eat``…) fica em ``1`` (custo extra zero). Vota-se com um verbo crítico
    (``attack``/``flee``/``drink_potion``/``hunt``) nos candidatos **ou**
    urgência *Critical*; com um só candidato real não há o que permutar e nunca
    se pedem mais de ``k!`` votos. ``candidates``: macro-ações, mapeamentos ou
    ids ``"verbo:alvo"`` (saída ignorada; verbo = ``verb`` senão o prefixo do
    ``id``); ``urgency``: ``"Critical…"``, nível numérico ou ``Answer``.

    **Nunca levanta** (lixo ⇒ ``1``).
    """
    try:
        wanted = _clamp_votes(configured)
        if wanted <= 1 or isinstance(candidates, (str, bytes)) or not isinstance(candidates, Iterable):
            return 1
        real = [item for item in candidates if not _is_exit_candidate(item, DEFAULT_EXIT_KEY)]
        if len(real) <= 1:
            return 1
        critical = {str(verb).strip().lower() for verb in critical_verbs if str(verb).strip()}
        if not (_is_critical_urgency(urgency) or any(_candidate_verb(c) in critical for c in real)):
            return 1
        return max(MIN_VOTES, min(wanted, _permutation_cap(len(real), wanted)))
    except Exception as exc:  # noqa: BLE001 - nunca estraga o passo por causa do voto
        log.warning("vote_count_for failed (%s: %s); using a single read", type(exc).__name__, exc)
        return 1
