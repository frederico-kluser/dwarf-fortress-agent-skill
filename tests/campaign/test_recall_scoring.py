"""Primitivas de recuperação (``campaign/recall.py``): vetor local, pontuação, higiene,
relógio e alinhamento do rabo do gamelog.

Tudo offline e determinístico. O teste ``test_vectors_are_identical_across_processes``
arranca interpretadores com ``PYTHONHASHSEED`` diferentes: falharia se alguém
trocasse ``zlib.crc32`` por ``hash()``. O teste ``test_golden_vector`` fixa o
vetor de uma frase: os vetores persistidos nos SQLite têm de continuar
comparáveis — se mudares ``TRIGRAM_WEIGHT``/``DEFAULT_DIM``/o esquema de
hashing, é preciso reindexar as memórias e atualizar o valor-ouro.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import random
import sqlite3
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from system_one_dwarf.campaign import recall as R
from system_one_dwarf.campaign.recall import (
    Candidate,
    ScoreWeights,
    TimePoint,
    ages_hours,
    clean_text,
    coerce_now,
    cosine,
    fit_lines,
    game_hours,
    hash_embed,
    minmax,
    neutralize,
    new_tail_start,
    pack_vector,
    rank,
    recency_score,
    tokenize,
    unpack_vector,
)

SRC = Path(__file__).resolve().parents[2] / "src"
SENTENCE = "The goblin attacks the dwarf near the river"
#: ``sha1(pack_vector(hash_embed(SENTENCE)))`` — ver o docstring do módulo.
GOLDEN_SENTENCE_DIGEST = "08197ca6028041be941a1cab90d3f151ba6d6c15"


# ---------------------------------------------------------------- tokenize


class TestTokenize:
    def test_lowercases_and_splits_on_non_alphanumerics(self):
        assert tokenize("The Goblin attacks, the DWARF! (near) snake_case") == [
            "goblin", "attacks", "dwarf", "near", "snake", "case",
        ]

    def test_drops_english_and_portuguese_stopwords(self):
        tokens = tokenize("the and of with para uma não sobre goblin")
        assert tokens == ["nao", "goblin"]  # "não" não é stopword; o resto é
        assert tokenize("O anão vai para a montanha") == ["anao", "vai", "montanha"]

    def test_keeps_digits(self):
        assert tokenize("pay 50 coins in 2 days") == ["pay", "50", "coins", "2", "days"]

    def test_folds_accents_so_names_match_without_diacritics(self):
        assert tokenize("Ñorlemhuh") == tokenize("norlemhuh") == ["norlemhuh"]

    def test_caps_at_400_tokens(self):
        text = " ".join(f"w{i}" for i in range(1000))
        assert len(tokenize(text)) == R.MAX_TOKENS == 400
        assert len(tokenize(text, max_tokens=3)) == 3
        assert tokenize(text, max_tokens=0) == []

    @pytest.mark.parametrize("value", [None, 123, b"goblin", ["goblin"], {"a": 1}, ""])
    def test_non_string_or_empty_is_empty(self, value):
        assert tokenize(value) == []

    def test_unicode_scripts_and_emoji(self):
        tokens = tokenize("日本語 テスト 🙂 Ñorlemhuh مرحبا")
        assert "日本語" in tokens and "norlemhuh" in tokens and "مرحبا" in tokens
        assert not any("🙂" in t for t in tokens)

    @pytest.mark.parametrize("bad", [None, "x", [], 2.5])
    def test_invalid_max_tokens_falls_back_to_the_default(self, bad):
        text = " ".join(f"w{i}" for i in range(1000))
        assert len(tokenize(text, max_tokens=bad)) in (R.MAX_TOKENS, 2)  # 2.5 → int(2.5)

    def test_one_giant_token_is_truncated(self):
        (token,) = tokenize("x" * 5000)
        assert len(token) == R.MAX_TOKEN_CHARS


# --------------------------------------------------------------- hash_embed


class TestHashEmbed:
    def test_dimension_and_unit_norm(self):
        vec = hash_embed(SENTENCE)
        assert len(vec) == R.DEFAULT_DIM == 256
        assert math.isclose(sum(x * x for x in vec), 1.0, rel_tol=1e-9)
        assert any(x < 0 for x in vec) and any(x > 0 for x in vec)  # hashing com sinal

    @pytest.mark.parametrize("text", ["", "   ", None, 42, "the and of with"])
    def test_empty_or_only_stopwords_is_the_zero_vector(self, text):
        assert hash_embed(text) == (0.0,) * R.DEFAULT_DIM

    def test_same_text_same_vector_and_case_accent_invariant(self):
        assert hash_embed(SENTENCE) == hash_embed(SENTENCE)
        assert hash_embed("Ñorlemhuh GOBLIN") == hash_embed("norlemhuh goblin")

    def test_dim_is_honoured_and_clamped(self):
        assert len(hash_embed("goblin", dim=64)) == 64
        assert len(hash_embed("goblin", dim=1)) == R.MIN_DIM
        assert len(hash_embed("goblin", dim=10**9)) == R.MAX_DIM
        for bad in ("x", None, True, 3.5):
            assert len(hash_embed("goblin", dim=bad)) == R.DEFAULT_DIM

    def test_similarity_orders_paraphrase_above_unrelated(self):
        base = hash_embed(SENTENCE)
        near = hash_embed("goblin attack dwarf river")
        far = hash_embed("apple pie recipe with cinnamon and sugar")
        assert cosine(base, near) > 0.5
        assert cosine(base, near) > cosine(base, far)
        assert cosine(base, far) < 0.2

    def test_shared_name_raises_similarity(self):
        a = hash_embed("Bomrek sells iron spears")
        b = hash_embed("Where is Bomrek?")
        c = hash_embed("Where is Thrun?")
        assert cosine(a, b) > cosine(a, c)

    def test_golden_vector(self):
        digest = hashlib.sha1(pack_vector(hash_embed(SENTENCE))).hexdigest()
        assert digest == GOLDEN_SENTENCE_DIGEST

    def test_vectors_are_identical_across_processes(self):
        script = (
            "import json, sys; sys.path.insert(0, sys.argv[1]);"
            "from system_one_dwarf.campaign.recall import hash_embed, tokenize;"
            "print(json.dumps([list(hash_embed(sys.argv[2])), tokenize(sys.argv[2])]))"
        )
        outputs = set()
        for seed in ("0", "1", "424242"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            done = subprocess.run(
                [sys.executable, "-c", script, str(SRC), SENTENCE + " Ñorlemhuh 50"],
                capture_output=True, text=True, env=env, check=True, timeout=60,
            )
            outputs.add(done.stdout.strip())
        assert len(outputs) == 1  # qualquer ``hash()`` do Python quebraria isto
        assert list(hash_embed(SENTENCE + " Ñorlemhuh 50")) == json.loads(outputs.pop())[0]


# ------------------------------------------------------------------ cosine


class TestCosine:
    def test_identical_is_one_and_orthogonal_is_zero(self):
        assert cosine((1.0, 2.0), (1.0, 2.0)) == pytest.approx(1.0)
        assert cosine((1.0, 0.0), (0.0, 1.0)) == 0.0

    def test_clamped_to_unit_interval(self):
        assert cosine((1.0, 0.0), (-1.0, 0.0)) == 0.0  # anti-correlação conta 0
        assert cosine((0.1,) * 7, (0.1,) * 7) <= 1.0

    @pytest.mark.parametrize(
        "a,b",
        [((), (1.0,)), ((0.0, 0.0), (1.0, 1.0)), ((1.0,), (1.0, 2.0)), (None, (1.0,)),
         ("ab", "cd"), ((float("nan"), 1.0), (1.0, 1.0)), ((1.0, 2.0), None)],
    )
    def test_degenerate_inputs_are_zero_never_raise(self, a, b):
        assert cosine(a, b) == 0.0


# --------------------------------------------------------------- pack/unpack


class TestPackVector:
    def test_roundtrip_is_float32_accurate(self):
        vec = hash_embed(SENTENCE)
        blob = pack_vector(vec)
        assert len(blob) == 4 * len(vec)
        back = unpack_vector(blob)
        assert len(back) == len(vec)
        assert max(abs(a - b) for a, b in zip(vec, back)) < 1e-6

    def test_non_finite_and_huge_values_become_zero(self):
        out = unpack_vector(pack_vector([float("nan"), float("inf"), -float("inf"), 1e300, 1.0, "x", None]))
        assert out == (0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    @pytest.mark.parametrize("blob", [b"", b"abc", None, "text", 12, b"\x00" * 7])
    def test_bad_blobs_unpack_to_empty(self, blob):
        assert unpack_vector(blob) == ()

    def test_accepts_bytearray_and_memoryview(self):
        blob = pack_vector([0.5, -0.25])
        assert unpack_vector(bytearray(blob)) == (0.5, -0.25)
        assert unpack_vector(memoryview(blob)) == (0.5, -0.25)

    def test_too_many_floats_rejected(self):
        assert unpack_vector(b"\x00" * 4 * (R.MAX_DIM + 1)) == ()


# ------------------------------------------------------------------ recency


class TestRecency:
    def test_age_zero_and_negative_are_one(self):
        assert recency_score(0) == 1.0
        assert recency_score(-50) == 1.0

    def test_exponential_decay_per_hour(self):
        assert recency_score(1) == pytest.approx(0.995)
        assert recency_score(24) == pytest.approx(0.995 ** 24)
        assert recency_score(2, 0.9) == pytest.approx(0.81)

    def test_strictly_decreasing_with_age(self):
        values = [recency_score(age) for age in range(0, 500, 7)]
        assert all(a > b for a, b in zip(values, values[1:]))

    @pytest.mark.parametrize("decay", [0, -1, 2, "x", None, float("nan"), True])
    def test_invalid_decay_falls_back_to_the_default(self, decay):
        assert recency_score(10, decay) == pytest.approx(0.995 ** 10)

    @pytest.mark.parametrize("age", [None, "3", float("nan"), True, [1]])
    def test_invalid_age_scores_zero(self, age):
        assert recency_score(age) == 0.0

    def test_huge_age_underflows_to_zero_without_raising(self):
        assert recency_score(1e9) == 0.0
        assert recency_score(float("inf")) == 0.0


# ------------------------------------------------------------------- minmax


class TestMinMax:
    def test_empty_and_constant(self):
        assert minmax([]) == []
        assert minmax([3, 3, 3]) == [0.5, 0.5, 0.5]
        assert minmax([7]) == [0.5]

    def test_scales_to_unit_interval(self):
        assert minmax([0, 5, 10]) == [0.0, 0.5, 1.0]
        assert minmax([-2, 0, 2]) == [0.0, 0.5, 1.0]

    def test_bad_values_count_as_zero(self):
        assert minmax([None, "x", float("nan"), 4]) == [0.0, 0.0, 0.0, 1.0]
        assert minmax([float("inf"), 1.0]) == [0.0, 1.0]


# ------------------------------------------------------------ ScoreWeights


class TestScoreWeights:
    def test_defaults_are_equal(self):
        w = ScoreWeights()
        assert (w.recency, w.importance, w.relevance) == (1.0, 1.0, 1.0)
        assert w.total == 3.0

    @pytest.mark.parametrize(
        "kwargs",
        [{"recency": -1}, {"importance": float("nan")}, {"relevance": float("inf")},
         {"recency": 0, "importance": 0, "relevance": 0}, {"recency": True}, {"importance": "1"}],
    )
    def test_validation_rejects_bad_weights(self, kwargs):
        with pytest.raises(ValueError):
            ScoreWeights(**kwargs)

    def test_a_single_positive_weight_is_valid(self):
        assert ScoreWeights(recency=0, importance=0, relevance=2).total == 2.0

    def test_from_any_is_tolerant(self):
        default = ScoreWeights(recency=2.0)
        assert ScoreWeights.from_any(None, default) is default
        assert ScoreWeights.from_any({"importance": 3}) == ScoreWeights(importance=3.0)
        assert ScoreWeights.from_any({"importance": -3}, default) is default
        assert ScoreWeights.from_any(object(), default) is default
        w = ScoreWeights(relevance=5.0)
        assert ScoreWeights.from_any(w) is w


# --------------------------------------------------------------------- rank


def cand(item, age=0.0, imp=5.0, rel=0.5):
    return Candidate(item=item, age_hours=age, importance=imp, relevance=rel)


def order(scored):
    return [s.item for s in scored]


class TestRank:
    def test_monotonic_in_recency(self):
        got = rank([cand("old", age=300), cand("new", age=0), cand("mid", age=30)])
        assert order(got) == ["new", "mid", "old"]

    def test_monotonic_in_importance(self):
        got = rank([cand("low", imp=1), cand("high", imp=10), cand("mid", imp=5)])
        assert order(got) == ["high", "mid", "low"]

    def test_monotonic_in_relevance(self):
        got = rank([cand("low", rel=0.0), cand("high", rel=0.9), cand("mid", rel=0.4)])
        assert order(got) == ["high", "mid", "low"]

    def test_components_are_exposed_and_the_score_is_their_mean(self):
        got = rank([cand("a", age=0, imp=10, rel=1.0), cand("b", age=100, imp=1, rel=0.0)])
        top, bottom = got
        assert (top.item, bottom.item) == ("a", "b")
        assert (top.recency, top.importance, top.relevance) == (1.0, 1.0, 1.0)
        assert (bottom.recency, bottom.importance, bottom.relevance) == (0.0, 0.0, 0.0)
        assert top.score == 1.0 and bottom.score == 0.0
        assert top.raw_importance == 10.0 and bottom.raw_relevance == 0.0
        assert top.raw_recency == 1.0 and bottom.raw_recency == pytest.approx(0.995 ** 100)
        assert set(top.as_dict()) == {
            "score", "recency", "importance", "relevance",
            "raw_recency", "raw_importance", "raw_relevance",
        }

    def test_equal_weights_average_normalised_components(self):
        # recência, importância e relevância normalizadas: a=(1, 0, 0.5), b=(0, 1, 0.5)
        got = {s.item: s for s in rank([cand("a", age=0, imp=1, rel=0.5), cand("b", age=500, imp=9, rel=0.5)])}
        assert got["a"].score == pytest.approx((1 + 0 + 0.5) / 3)
        assert got["b"].score == pytest.approx((0 + 1 + 0.5) / 3)

    def test_weights_shift_the_ordering(self):
        pool = [
            cand("recent", age=0, imp=1, rel=0.1),
            cand("important", age=200, imp=10, rel=0.1),
            cand("relevant", age=200, imp=1, rel=0.9),
        ]
        assert rank(pool, weights=ScoreWeights(1, 0, 0), k=1)[0].item == "recent"
        assert rank(pool, weights=ScoreWeights(0, 1, 0), k=1)[0].item == "important"
        assert rank(pool, weights=ScoreWeights(0, 0, 1), k=1)[0].item == "relevant"

    def test_decay_parameter_is_used(self):
        pool = [cand("a", age=10), cand("b", age=20)]
        steep = rank(pool, weights=ScoreWeights(1, 0, 0), decay=0.5)
        assert steep[0].raw_recency == pytest.approx(0.5 ** 10)

    def test_top_k_and_degenerate_k(self):
        pool = [cand(i, age=i) for i in range(6)]
        assert order(rank(pool, k=2)) == [0, 1]
        assert len(rank(pool, k=99)) == 6
        for k in (0, -3, None, "x"):
            assert rank(pool, k=k) == []
        assert rank([], k=5) == []

    def test_single_candidate_is_neutral(self):
        (only,) = rank([cand("x", age=7, imp=3, rel=0.2)])
        assert only.score == only.recency == only.importance == only.relevance == 0.5

    def test_ties_keep_input_order(self):
        got = rank([cand("first"), cand("second"), cand("third")])
        assert order(got) == ["first", "second", "third"]

    def test_tie_prefers_higher_raw_importance_then_input_order(self):
        # a: recência 1, importância 0  /  b: recência 0, importância 1 → mesmo score
        got = rank([cand("a", age=0, imp=1, rel=0.5), cand("b", age=900, imp=9, rel=0.5)])
        assert got[0].score == pytest.approx(got[1].score)
        assert order(got) == ["b", "a"]

    def test_is_deterministic_under_input_permutation(self):
        pool = [cand(f"c{i}", age=i * 3.7, imp=(i * 7) % 10 + 1, rel=((i * 13) % 11) / 10) for i in range(25)]
        expected = order(rank(pool, k=10))
        rng = random.Random(7)
        for _ in range(10):
            shuffled = pool[:]
            rng.shuffle(shuffled)
            assert order(rank(shuffled, k=10)) == expected

    def test_mapping_weights_are_accepted_and_bad_ones_ignored(self):
        pool = [cand("recent", age=0, imp=1), cand("important", age=300, imp=10)]
        assert rank(pool, weights={"recency": 1, "importance": 0, "relevance": 0}, k=1)[0].item == "recent"
        assert rank(pool, weights={"recency": 0, "importance": 1, "relevance": 0}, k=1)[0].item == "important"
        assert len(rank(pool, weights={"recency": -5}, k=2)) == 2  # inválido → pesos iguais

    def test_garbage_components_are_sanitised(self):
        got = rank([cand("nan", age=None, imp=float("nan"), rel=7.0), cand("neg", age=0, imp=2, rel=-3.0)])
        by_item = {s.item: s for s in got}
        assert by_item["nan"].raw_relevance == 1.0 and by_item["neg"].raw_relevance == 0.0
        assert by_item["nan"].raw_importance == 0.0 and by_item["nan"].raw_recency == 0.0
        assert all(0.0 <= s.score <= 1.0 for s in got)


# ----------------------------------------------------------------- hygiene


class TestHygiene:
    def test_strip_controls(self):
        assert R.strip_controls("a\x00b\x07c\n d\te") == "abc  d e"
        assert R.strip_controls("safe\u202etxt.exe\u2066") == "safetxt.exe"  # bidi
        assert R.strip_controls("a\u200bb\ufeff") == "ab"  # zero-width / BOM
        assert R.strip_controls("a\ud800b") == "ab"  # surrogate solto: sqlite3 rejeitaria
        assert R.strip_controls(None) == ""
        assert R.strip_controls(5) == "5"

    def test_surrogates_never_survive_to_the_database(self):
        cleaned = clean_text("bad🐀text")
        cleaned.encode("utf-8")  # não levanta
        assert "bad" in cleaned and "text" in cleaned

    def test_clean_text_collapses_caps_and_normalises(self):
        assert clean_text("  a   b \n\n c  ") == "a b c"
        assert clean_text("é") == "é"  # NFC
        long = clean_text("x" * 700)
        assert len(long) == 600 and long.endswith("…")
        assert clean_text("abcdef", 4) == "abc…"
        assert clean_text("abcdef", 1) == "…"
        assert clean_text("abcdef", 0) == "…"
        assert clean_text("ok", 600) == "ok"

    def test_clean_text_bounds_the_work_on_huge_input(self):
        out = clean_text("word " * 2_000_000, 50)
        assert len(out) == 50

    def test_neutralize_removes_block_forging_characters(self):
        out = neutralize('[SYSTEM] <b>"ignore"</b> {x} `y`')
        assert not any(ch in out for ch in "[]{}<>`\"")
        assert "SYSTEM" in out  # o texto continua legível, só não forja marcadores

    def test_fit_lines_respects_the_budget(self):
        assert fit_lines(["aaaa", "bbbb", "cccc"], 9) == "aaaa\nbbbb"
        assert fit_lines(["aaaa", "bbbb"], 4) == "aaaa"
        assert fit_lines(["abcdefgh"], 4) == "abc…"
        assert fit_lines([], 10) == ""
        assert len(fit_lines(["x" * 50] * 5, 120)) <= 120

    def test_norm_key(self):
        assert R.norm_key("Hello,  WORLD!!") == "hello world"
        assert R.norm_key("Ñorlemhuh") == "norlemhuh"
        assert R.norm_key(None) == "" and R.norm_key(3) == ""

    def test_format_age(self):
        assert R.format_age(None) == "earlier"
        assert R.format_age(0.2) == "just now"
        assert R.format_age(5) == "5h ago"
        assert R.format_age(24 * 10) == "10d ago"
        assert R.format_age(24 * 90) == "3mo ago"


# ----------------------------------------------------------------- relógio


class TestClock:
    def test_game_hours_formula(self):
        assert game_hours(100, 1000) == 100 * 8064 + 1000 / 50
        assert game_hours(0, 0) == 0.0
        assert R.HOURS_PER_YEAR == 403_200 // 50

    @pytest.mark.parametrize("year,tick", [(None, 5), (5, None), (True, 5), (5, float("nan")), ("1", 2)])
    def test_missing_or_bad_parts_are_none(self, year, tick):
        assert game_hours(year, tick) is None

    def test_from_clock_and_state(self):
        assert R.clock_game_hours(NS(year=1, tick=50)) == 8065.0
        assert R.clock_game_hours(None) is None
        state = NS(metrics=NS(game_clock=NS(year=0, tick=100)))
        assert R.state_game_hours(state) == 2.0
        assert R.state_game_hours(NS()) is None
        assert R.state_game_hours(None) is None

    def test_coerce_now_variants(self):
        base = dict(wall=1000.0, game_hours_hint=7.0)
        assert coerce_now(None, **base) == TimePoint(1000.0, 7.0)
        assert coerce_now(12, **base) == TimePoint(1000.0, 12.0)
        assert coerce_now(True, **base) == TimePoint(1000.0, 7.0)  # bool não é número
        point = TimePoint(5.0, 9.0)
        assert coerce_now(point, **base) is point
        assert coerce_now(NS(year=0, tick=500), **base) == TimePoint(1000.0, 10.0)
        assert coerce_now(NS(metrics=NS(game_clock=NS(year=0, tick=50))), **base) == TimePoint(1000.0, 1.0)
        assert coerce_now(object(), **base) == TimePoint(1000.0, 7.0)
        aware = datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert coerce_now(aware, **base).wall == aware.timestamp()
        naive = datetime(2026, 1, 1)
        assert coerce_now(naive, **base).wall == aware.timestamp()  # naive = UTC

    def test_ages_use_game_clock_when_available(self):
        now = TimePoint(wall=10_000.0, game_hours=100.0)
        assert ages_hours([90.0, 40.0], [0.0, 0.0], now) == [10.0, 60.0]

    def test_ages_rows_without_game_time_count_as_the_oldest(self):
        now = TimePoint(wall=10_000.0, game_hours=100.0)
        assert ages_hours([90.0, None, 40.0], [0, 0, 0], now) == [10.0, 60.0, 60.0]

    def test_ages_future_rows_are_age_zero(self):
        now = TimePoint(wall=0.0, game_hours=10.0)
        assert ages_hours([50.0], [0.0], now) == [0.0]

    def test_ages_fall_back_to_wall_clock_in_hours(self):
        now = TimePoint(wall=3600.0 * 5, game_hours=None)
        assert ages_hours([None, None], [3600.0 * 2, 3600.0 * 5], now) == [3.0, 0.0]
        # tem horas de jogo "agora" mas nenhuma linha as tem → parede
        now_game = TimePoint(wall=3600.0 * 5, game_hours=100.0)
        assert ages_hours([None], [3600.0 * 4], now_game) == [1.0]
        # nunca mistura unidades: sem hora de jogo "agora", ignora as das linhas
        assert ages_hours([50.0], [3600.0 * 5], TimePoint(3600.0 * 5, None)) == [0.0]

    def test_iso_from_epoch(self):
        assert R.iso_from_epoch(0) == "1970-01-01T00:00:00.000Z"
        assert R.iso_from_epoch(1_700_000_000.5).endswith(".500Z")
        assert R.iso_from_epoch(1e30) == "1970-01-01T00:00:00.000Z"  # não levanta


# ---------------------------------------------------- rabo do gamelog


class TestTailAlignment:
    def test_only_the_lines_after_the_overlap_are_new(self):
        assert new_tail_start(list("abcde"), list("cdefg")) == 3
        assert list("cdefg")[3:] == ["f", "g"]

    def test_identical_tail_has_nothing_new(self):
        assert new_tail_start(list("abc"), list("abc")) == 3

    def test_growing_window_aligns_at_the_start(self):
        assert new_tail_start(list("ab"), list("abcd")) == 2

    def test_no_overlap_or_no_reference_means_everything_is_new(self):
        assert new_tail_start(list("abc"), list("xyz")) == 0
        assert new_tail_start([], list("abc")) == 0
        assert new_tail_start(list("abc"), []) == 0

    def test_repeated_legitimate_lines_are_not_collapsed(self):
        prev = ["stands up", "stands up", "stands up"]
        cur = ["stands up", "stands up", "stands up", "stands up", "stands up"]
        assert new_tail_start(prev, cur) == 3  # 2 linhas novas, não 0
        assert new_tail_start(prev, prev) == 3  # snapshot repetido: nada de novo

    def test_a_single_matching_line_is_not_enough_when_more_were_seen(self):
        assert new_tail_start(list("abcd"), list("dxyz")) == 0
        assert new_tail_start(list("abcd"), list("cdxyz"), min_overlap=1) == 2

    def test_a_one_line_reference_may_align_on_that_line(self):
        assert new_tail_start(["a"], ["a", "b"]) == 1

    def test_combat_flood_still_aligns_on_the_last_old_lines(self):
        old = [f"old{i}" for i in range(20)]
        new = [f"new{i}" for i in range(15)]
        window = (old + new)[-20:]
        assert new_tail_start(old[-8:], window) == 5  # as 5 antigas restantes
        assert window[5:] == new

    def test_after_last_marker_cuts_at_the_real_game_markers(self):
        lines = [
            "*** STARTING NEW GAME ***", "Generating world using parameter set CREATE WORLD NOW = 1",
            " Seed: abc", "You've finally got your equipment together.", "** Starting Adventure **",
            "The lady Tislur Nodvope: Hello.", "The stray mule stands up.",
        ]
        assert R.after_last_marker(lines) == 5
        assert R.after_last_marker(["a", "b"]) == 0
        assert R.after_last_marker([]) == 0
        assert R.after_last_marker(["** Starting Adventure **"]) == 1

    def test_line_fingerprint_is_stable_and_normalised(self):
        assert R.line_fingerprint("Hello  World") == R.line_fingerprint("  hello world ")
        assert R.line_fingerprint("a") != R.line_fingerprint("b")
        assert R.line_fingerprint("a\x00b") == R.line_fingerprint("ab")
        assert len(R.line_fingerprint("x")) == 12
        assert R.line_fingerprint(None) == R.line_fingerprint("")


# ------------------------------------------------------------------- SQLite


class TestPrivateSqlite:
    def test_dir_0700_and_file_0600_even_with_a_permissive_umask(self, tmp_path):
        old = os.umask(0)
        try:
            conn = R.connect_private(tmp_path / "camp" / "x.sqlite")
            conn.execute("CREATE TABLE t (a)")
            conn.commit()
            conn.close()
        finally:
            os.umask(old)
        assert stat.S_IMODE((tmp_path / "camp").stat().st_mode) == 0o700
        assert stat.S_IMODE((tmp_path / "camp" / "x.sqlite").stat().st_mode) == 0o600

    def test_a_loose_existing_file_is_tightened(self, tmp_path):
        path = tmp_path / "loose.sqlite"
        path.write_bytes(b"")
        path.chmod(0o644)
        R.connect_private(path).close()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_meta_get_and_set(self, tmp_path):
        conn = R.connect_private(tmp_path / "m.sqlite")
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        assert R.meta_get(conn, "k") is None
        assert R.meta_get(conn, "k", "dflt") == "dflt"
        R.meta_set(conn, "k", 5)
        assert R.meta_get(conn, "k") == "5"
        R.meta_set(conn, "k", "x'); DROP TABLE meta;--")
        assert R.meta_get(conn, "k") == "x'); DROP TABLE meta;--"
        assert conn.execute("SELECT COUNT(*) FROM meta").fetchone()[0] == 1
        conn.close()

    def test_connection_rows_are_name_addressable(self, tmp_path):
        conn = R.connect_private(tmp_path / "r.sqlite")
        row = conn.execute("SELECT 1 AS one").fetchone()
        assert isinstance(row, sqlite3.Row) and row["one"] == 1
        conn.close()


# ------------------------------------------------------------------ guarded


class TestGuarded:
    class Store:
        errors = 0

        @R.guarded(list)
        def boom(self):
            raise RuntimeError("disk on fire")

        @R.guarded(lambda: None)
        def fine(self):
            return "ok"

        @R.guarded(dict)
        def interrupted(self):
            raise KeyboardInterrupt

    def test_failures_become_the_default_and_are_counted(self):
        store = self.Store()
        assert store.boom() == []
        assert store.boom() == []
        assert store.errors == 2
        assert store.fine() == "ok"
        assert self.Store.boom.__name__ == "boom"  # functools.wraps

    def test_base_exceptions_propagate(self):
        with pytest.raises(KeyboardInterrupt):
            self.Store().interrupted()
