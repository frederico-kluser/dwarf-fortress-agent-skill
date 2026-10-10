"""Cadeia ``A_ATTACK`` (``game/attack_chain.py``): ecrãs sintéticos → próximo passo.

A geometria real do DF 53.16 não se captura offline; os ecrãs aqui são
construídos a partir dos marcadores documentados (``combat.attack_menu_chain``) e
POSICIONADOS nas coordenadas dos cliques da corrida ``caca-2``
(``scripts/e2e/logs/mission-hunt.jsonl``): Okay@121,35 · "a The stray mule"@62,51 ·
Confirm@47,65 · "a Strike"@45,58 · "a upper body"@51,34 · "a stab/iron
spear"@53,34 · Done@136,31 — um ponto de clique que mude é uma regressão.

Cobrem: tolerância do ``parse_screen``; ``detect_stage`` (cada estágio, overlay,
unknown, modal vs. painéis, mapa que ignora texto); ``find_label``; espécie/
sapiente; ``next_step`` (cada estágio + todos os abortos); ``to_dsl``;
``unwind_step``; a cadeia completa com um jogo de brincar (``AttackChain``) e um
fuzz determinístico (nunca levanta; o DSL nunca sai da gramática fechada).
"""
from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from system_one_dwarf.game import attack_chain as ac  # noqa: E402
from system_one_dwarf.game.attack_chain import (  # noqa: E402
    ACTION_ABORT,
    ACTION_CLICK,
    ACTION_DONE,
    ACTION_KEY,
    ACTION_WAIT,
    AttackChain,
    ChainStep,
    Reason,
    ScreenView,
    detect_stage,
    find_label,
    list_attack_options,
    looks_sapient,
    next_step,
    parse_attack_label,
    parse_screen,
    screen_from_report,
    species_words,
    target_matches,
    to_dsl,
    unwind_step,
)

W, H = 182, 72
MAP_FOCUS = "dungeonmode/Default"
ATTACK_FOCUS = "dungeonmode/Attack"
HELP_FOCUS = "dungeonmode/Help/dungeonmode/Attack"
#: Pontuação do mapa (glifos): nunca forma palavras, mas cola-se ao texto do menu.
_NOISE = ",.'`\"~;:!?-=+*#%&"
MODIFIER_LABELS = ("u Quick", "v Heavy", "w Wild", "x Precise", "z Multi-attack")
SPEAR_MENU = ("stab/iron spear", "bash/shaft/iron spear", "strike/copper buckler", "punch/hands")


def screen(*items, focus=ATTACK_FOCUS, panel=None, noise=True):
    """Ecrã ``{"focus", "lines"}`` 72×182 com ``items=(x, y, texto)``.

    ``panel=(x0, y0, x1, y1)`` esvazia um retângulo (o pop-up do DF tem fundo
    liso); sem ele o texto fica colado ao ruído do mapa (pior caso).
    """
    rows = [[_NOISE[(x * 7 + y * 3) % len(_NOISE)] if noise else " " for x in range(W)]
            for y in range(H)]
    if panel:
        x0, y0, x1, y1 = panel
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                rows[y][x] = " "
    for x, y, text in items:
        for i, ch in enumerate(text):
            rows[y][x + i] = ch
    return {"focus": [focus] if focus else [],
            "lines": ["".join(r).rstrip() for r in rows]}


def map_screen():
    return screen((60, 30, "@"), (2, 70, "The stray mule stands up."), focus=MAP_FOCUS)


def help_screen():
    return screen((100, 31, "Attack: choose how to strike"), (119, 35, "Okay"),
                  (100, 37, "Don't show again"), focus=HELP_FOCUS, panel=(95, 28, 140, 40))


def who_screen(*names):
    items = [(54, 49, "Who will you attack?")]
    items += [(54, 51 + i, f"{chr(97 + i)} {name}") for i, name in enumerate(names)]
    return screen(*items, panel=(50, 47, 100, 56))


def confirm_screen(named="the stray mule", button=True):
    items = [(30, 63, f"Really start a conflict with {named}?")]
    if button:
        items += [(44, 65, "Confirm"), (60, 65, "Cancel")]
    return screen(*items, panel=(25, 61, 100, 67))


def planner_screen():
    return screen((41, 58, "a Strike"), (41, 59, "b Dodge (Adequate)"), panel=(38, 56, 80, 62))


def aim_screen(parts=("upper body", "lower body", "neck", "head")):
    items = [(45, 33, "What do you want to aim for?")]
    items += [(45, 34 + i, f"{chr(97 + i)} {part}") for i, part in enumerate(parts)]
    return screen(*items, panel=(42, 31, 90, 48))


def type_screen(options=SPEAR_MENU, part="head", modifiers=True, done=True):
    items = [(45, 33, f"Attack {part}:")]
    items += [(45, 34 + i, f"{chr(97 + i)} {opt}") for i, opt in enumerate(options)]
    if modifiers:
        items += [(134, 33 + i, label) for i, label in enumerate(MODIFIER_LABELS)]
    if done:
        items.append((134, 31, "Done"))
    return screen(*items, panel=(42, 29, 160, 48))


def text_of(payload):
    return "\n".join(payload["lines"])


# ============================================================ parse_screen


def test_parse_bridge_json_keeps_focus_and_trims_lines():
    view = parse_screen({"focus": [MAP_FOCUS], "lines": ["abc   ", "", "d"]})
    assert view == ScreenView(focus=(MAP_FOCUS,), lines=("abc", "", "d"))
    assert view.is_map and view.text == "abc\n\nd"


def test_parse_accepts_nested_data_and_json_text_and_bytes():
    inner = {"focus": ["dungeonmode/Attack"], "lines": ["x", "y"]}
    for payload in ({"ok": True, "data": inner}, json.dumps(inner), json.dumps(inner).encode(),
                    {"result": {"screen": inner}}):
        view = parse_screen(payload)
        assert view.lines == ("x", "y") and view.focus == ("dungeonmode/Attack",)


def test_parse_batch_report_takes_the_last_read_screen():
    report = {"ok": True, "steps": [
        {"i": 0, "op": "click", "focus": ["a"]},
        {"i": 1, "op": "read", "lines": ["old"], "focus": ["dungeonmode/Default"]},
        {"i": 2, "op": "sleep"},
        {"i": 3, "op": "read", "lines": ["new", "screen"], "focus": ["dungeonmode/Attack"]},
        {"i": 4, "op": "sleep"},
    ]}
    view = parse_screen(report)
    assert view.lines == ("new", "screen") and view.focus == ("dungeonmode/Attack",)
    assert screen_from_report(report) == view
    assert screen_from_report({"steps": [{"op": "key"}]}) is None
    assert screen_from_report("") is None


def test_parse_plain_text_with_printed_focus_marker_and_tuple_pair():
    view = parse_screen("line one\nline two\n· focus: dungeonmode/Default")
    assert view.focus == (MAP_FOCUS,) and view.lines == ("line one", "line two")
    pair = parse_screen(("dungeonmode/Attack", "a\nb"))  # o que o SubprocessBridge devolve
    assert pair.focus == ("dungeonmode/Attack",) and pair.lines == ("a", "b")
    assert parse_screen(["x", "y"]).lines == ("x", "y")
    assert parse_screen({"screen_text": "p\nq", "focus": MAP_FOCUS}).lines == ("p", "q")


@pytest.mark.parametrize("junk", [None, 42, 3.5, object(), {"lines": 5}, {"data": {"data": {}}},
                                  "", "{not json", b"\xff\xfe", {"steps": "no"}, [None, 1, {}]])
def test_parse_never_raises_and_garbage_is_an_empty_or_blank_screen(junk):
    view = parse_screen(junk)
    assert isinstance(view, ScreenView)
    assert detect_stage(junk) == "unknown"


def test_parse_replaces_control_and_bidi_characters_without_moving_columns():
    raw = "ab\x00c‮def\x1b[0m​Z"
    view = parse_screen({"lines": [raw]})
    assert len(view.lines[0]) == len(raw)
    assert "\x00" not in view.lines[0] and "‮" not in view.lines[0]
    assert view.lines[0].index("Z") == raw.index("Z")  # a coluna do Z não mexeu


def test_parse_caps_lines_columns_and_focus_items():
    big = {"focus": [f"f{i}" for i in range(50)], "lines": ["x" * 5000] * 500}
    view = parse_screen(big)
    assert len(view.lines) == ac.MAX_SCREEN_LINES
    assert max(len(line) for line in view.lines) == ac.MAX_LINE_CHARS
    assert len(view.focus) == ac.MAX_FOCUS_ITEMS
    assert len(parse_screen("y" * 3_000_000).lines[0]) <= ac.MAX_LINE_CHARS


# ============================================================ detect_stage


@pytest.mark.parametrize("build,expected", [
    (help_screen, "help_overlay"),
    (lambda: who_screen("The stray mule"), "who_will_you_attack"),
    (confirm_screen, "confirm_conflict"),
    (planner_screen, "planner"),
    (aim_screen, "aim_menu"),
    (type_screen, "attack_type"),
    (map_screen, "done"),
])
def test_each_stage_is_detected_with_focus_and_as_plain_text(build, expected):
    payload = build()
    assert detect_stage(payload) == expected
    plain = {"focus": [], "lines": payload["lines"]}  # sem foco: decidem os marcadores
    if expected != "done":
        assert detect_stage(plain) == expected
    assert detect_stage(text_of(payload)) == ("unknown" if expected == "done" else expected)


def test_modifiers_screen_needs_done_plus_a_modifier_and_no_entries():
    only_mods = screen((134, 31, "Done"), (134, 33, "u Quick"), (134, 34, "v Heavy"))
    assert detect_stage(only_mods) == "modifiers"
    assert detect_stage(screen((134, 31, "Done"))) == "unknown"  # "Done" sozinho não chega
    assert detect_stage(screen((134, 33, "u Quick"))) == "unknown"


def test_modal_prompt_wins_over_the_panes_underneath():
    both = screen((54, 49, "Who will you attack?"), (54, 51, "a The stray mule"),
                  (41, 58, "a Strike"), (30, 63, "Really start a conflict with the stray mule?"),
                  (44, 65, "Confirm"))
    assert detect_stage(both) == "confirm_conflict"


def test_deeper_menu_wins_when_earlier_panes_stay_visible():
    aim_over_planner = screen((45, 33, "What do you want to aim for?"), (45, 34, "a upper body"),
                              (41, 58, "a Strike"))
    assert detect_stage(aim_over_planner) == "aim_menu"
    type_over_aim = screen((45, 33, "Attack neck:"), (45, 34, "a stab/iron spear"),
                           (45, 40, "What do you want to aim for?"))
    assert detect_stage(type_over_aim) == "attack_type"
    planner_over_who = screen((54, 49, "Who will you attack?"), (41, 58, "a Strike"))
    assert detect_stage(planner_over_who) == "planner"


def test_map_focus_ignores_every_text_marker():
    """Um diálogo/anúncio com "Who will you attack?" não arma a cadeia."""
    hostile = screen((2, 60, "The goblin: Who will you attack?"), (2, 61, "Really start a conflict"),
                     (2, 62, "a Strike"), (2, 63, "Okay"), (2, 64, "Done"), focus=MAP_FOCUS)
    assert detect_stage(hostile) == "done"
    assert next_step(hostile, "Mule", confirmed=True).action == ACTION_DONE


def test_announcement_lines_are_not_attack_entries():
    log = screen((2, 66, "You stab the stray mule in the upper body with your iron spear"),
                 (2, 67, "It was a strike against the mule"), focus="dungeonmode/Announcements")
    assert detect_stage(log) == "unknown"
    assert list_attack_options(log) == ()


def test_okay_alone_is_an_overlay_off_the_map_but_not_on_it():
    assert detect_stage(screen((10, 10, "Okay"))) == "help_overlay"
    assert detect_stage(screen((10, 10, "Okay"), focus=MAP_FOCUS)) == "done"


def test_empty_and_unfocused_blank_screens_are_unknown():
    assert detect_stage(ScreenView()) == "unknown"
    assert detect_stage(screen(focus="dungeonmode/Options")) == "unknown"
    assert detect_stage({"focus": [], "lines": [""] * 10}) == "unknown"


def test_attack_header_requires_a_part_and_the_stage_list_is_closed():
    assert detect_stage(screen((45, 33, "Attack the stray mule:"))) == "unknown"
    assert detect_stage(screen((45, 33, "Attack:"))) == "unknown"
    assert detect_stage(screen((45, 33, "Attack left front leg:"))) == "attack_type"
    for payload in (help_screen(), map_screen(), type_screen(), screen()):
        assert detect_stage(payload) in ac.STAGES


# =============================================================== find_label


def test_click_points_reproduce_the_live_run_coordinates():
    assert find_label(help_screen(), "Okay") == (121, 35)
    assert find_label(who_screen("The stray mule"), "a The stray mule") == (62, 51)
    assert find_label(confirm_screen(), "Confirm") == (47, 65)
    assert find_label(planner_screen(), "a Strike") == (45, 58)
    assert find_label(aim_screen(), "a upper body") == (51, 34)
    assert find_label(type_screen(), "a stab/iron spear") == (53, 34)
    assert find_label(type_screen(), r"\bDone\b") == (136, 31)


def test_find_label_occurrence_bounds_literal_fallback_and_inputs():
    two = who_screen("The stray mule", "The stray mule")
    assert find_label(two, "The stray mule") == (63, 51)
    assert find_label(two, "The stray mule", occurrence=1) == (63, 52)
    assert find_label(two, "The stray mule", occurrence=2) is None
    assert find_label(two, "stray", min_y=52) == (62, 52)
    assert find_label(two, "stray", max_y=50) is None
    assert find_label(two, "THE STRAY MULE", ignore_case=False) is None
    assert find_label(two, "THE STRAY MULE") == (63, 51)
    assert find_label(two, "(") is None  # regex inválido → literal; não há "("
    assert find_label(planner_screen(), "(Adequate") == (53, 59)  # regex inválido → literal "(Adequate"
    assert find_label(planner_screen(), "(Adequate)") == (54, 59)  # regex válido: o grupo "Adequate"
    assert find_label(two, "") is None
    assert find_label(None, "x") is None
    assert find_label(text_of(who_screen("The stray mule")), "mule") is not None
    assert find_label(two, "stray", occurrence=-3) == (62, 51)  # negativo → 0


def test_label_matching_uses_compiled_regex_objects_too():
    assert find_label(help_screen(), re.compile(r"Okay")) == (121, 35)


# ===================================================== espécie / sapiente


@pytest.mark.parametrize("line,target,expected", [
    ("a The stray mule", "Mule (tame)", True),
    ("The stray small reindeer cow", "Reindeer Cow (tame)", True),
    ("The stray rabbit", "Buck Rabbit (tame)", True),
    ("The stray mules", "mule", True),
    ("Cow", "Cow (tame)", True),
    ("The stray cow", "Cow (tame)", True),
    ("a The stray boar", "Wild boar", True),
    ("The wild boar man", "Wild boar", False),
    ("The stray tame wild", "Mule", False),
    ("The wild boar man", "wild boar", False),
    ("The giant wolf spider", "wolf", False),
    ("The stray mule stands up", "Mule (tame)", False),
    ("The stray horse", "Mule (tame)", False),
    ("", "Mule", False),
    ("a The stray mule", "", False),
    (None, "Mule", False),
])
def test_target_matches_requires_the_same_species(line, target, expected):
    assert target_matches(line, target) is expected


def test_species_words_drop_articles_sex_age_and_hotkeys():
    assert species_words("a The stray small reindeer cow") == ("reindeer",)
    assert species_words("Mule (tame)") == ("mule",)
    assert species_words("Cow (tame)") == ("cow",)
    assert species_words("The stray cow") == ("cow",)  # só palavras de estado: o núcleo fica
    assert species_words("Wild Boar") == ("boar",)
    assert species_words(123) == ()


@pytest.mark.parametrize("text,expected", [
    ("Goblin", True), ("Dwarf", True), ("the elf archer", True), ("human merchant", True),
    ('Hode Ijamosman "Clockshadows", Swordsman', True), ("The wild boar man", True),
    ("Tislur Nodvope, lady", True), ("Mule (tame)", False), ("The stray small reindeer cow", False),
    ("Buck Rabbit", False), ("", False), (None, False), (12, False),
])
def test_looks_sapient(text, expected):
    assert looks_sapient(text) is expected


def test_sapient_words_stay_in_sync_with_the_candidate_generator():
    from system_one_dwarf.agent.candidates import SAPIENT_WORDS as CANDIDATES_WORDS

    assert ac.SAPIENT_WORDS == CANDIDATES_WORDS


# ============================================================== next_step


def test_help_overlay_clicks_okay_then_next_then_aborts_without_a_button():
    step = next_step(help_screen(), "Mule (tame)")
    assert (step.action, step.x, step.y, step.reason) == (ACTION_CLICK, 121, 35, Reason.HELP_DISMISSED)
    nxt = next_step(screen((80, 40, "Movement (1 of 3)"), (90, 50, "Next"), focus="dungeonmode/Help"),
                    "Mule")
    assert (nxt.action, nxt.reason) == (ACTION_CLICK, Reason.HELP_NEXT)
    bare = next_step(screen((80, 40, "Click the help button"), focus="dungeonmode/Help"), "Mule")
    assert (bare.action, bare.reason, bare.key) == (ACTION_ABORT, Reason.HELP_NO_BUTTON, "LEAVESCREEN")


def test_who_list_selects_only_the_matching_species_row():
    view = who_screen('Hode Ijamosman "Clockshadows", Swordsman', "The stray mule")
    step = next_step(view, "Mule (tame)")
    assert (step.action, step.x, step.y, step.reason) == (ACTION_CLICK, 62, 52, Reason.TARGET_SELECTED)
    assert step.label == "b The stray mule"


def test_who_list_occurrence_picks_the_nth_identical_entry():
    view = who_screen("The stray mule", "The stray mule", "The stray rabbit")
    assert next_step(view, "mule", occurrence=1).y == 52
    assert next_step(view, "mule", occurrence=5).reason == Reason.TARGET_NOT_LISTED


def test_who_list_without_the_target_aborts_with_leavescreen():
    step = next_step(who_screen("The stray rabbit"), "Mule (tame)")
    assert (step.action, step.reason, step.key) == (ACTION_ABORT, Reason.TARGET_NOT_LISTED, "LEAVESCREEN")


def test_who_list_never_clicks_log_sentences_with_the_species_name():
    view = screen((54, 49, "Who will you attack?"), (54, 51, "a The stray rabbit"),
                  (2, 66, "The stray mule stands up."), panel=(50, 47, 100, 56))
    assert next_step(view, "Mule").reason == Reason.TARGET_NOT_LISTED


def test_conflict_prompt_waits_without_consent_and_clicks_with_it():
    held = next_step(confirm_screen(), "Mule (tame)")
    assert (held.action, held.reason, held.detail) == (
        ACTION_WAIT, Reason.NEEDS_CONFIRMATION, "the stray mule")
    ok = next_step(confirm_screen(), "Mule (tame)", confirmed=True)
    assert (ok.action, ok.x, ok.y, ok.reason) == (ACTION_CLICK, 47, 65, Reason.CONFLICT_CONFIRMED)


@pytest.mark.parametrize("named,reason", [
    ("Hode Ijamosman \"Clockshadows\", Swordsman", Reason.CONFLICT_MISMATCH),
    ("the goblin", Reason.CONFLICT_MISMATCH),
    ("the stray horse", Reason.CONFLICT_MISMATCH),
    ("the stray mule and the stray rabbit", Reason.CONFLICT_MISMATCH),
    ("the wild boar man", Reason.CONFLICT_MISMATCH),
    ("2 creatures", Reason.CONFLICT_MISMATCH),
])
def test_conflict_prompt_naming_someone_else_aborts_even_when_confirmed(named, reason):
    step = next_step(confirm_screen(named), "Mule (tame)", confirmed=True)
    assert (step.action, step.reason, step.key) == (ACTION_ABORT, reason, "LEAVESCREEN")


def test_conflict_prompt_unreadable_or_without_button_aborts():
    unreadable = screen((30, 63, "Really start a conflict"), (44, 65, "Confirm"))
    assert next_step(unreadable, "Mule", confirmed=True).reason == Reason.CONFLICT_UNREADABLE
    no_button = next_step(confirm_screen(button=False), "Mule", confirmed=True)
    assert no_button.reason == Reason.NO_CONFIRM_BUTTON


def test_conflict_prompt_that_wraps_onto_the_next_line_is_still_read():
    wrapped = screen((30, 63, "Really start a conflict with the stray"), (30, 64, "mule?"),
                     (44, 66, "Confirm"), panel=(25, 61, 100, 68))
    step = next_step(wrapped, "Mule (tame)", confirmed=True)
    assert (step.action, step.reason) == (ACTION_CLICK, Reason.CONFLICT_CONFIRMED)
    # a linha de baixo só conta quando fecha a pergunta (não é "Confirm Cancel")
    stray = screen((30, 63, "Really start a conflict with the stray mule"), (30, 64, "Confirm   Cancel"),
                   panel=(25, 61, 100, 68))
    assert next_step(stray, "Mule", confirmed=True).reason == Reason.CONFLICT_CONFIRMED


def test_planner_clicks_strike_never_dodge_or_the_shield_entry():
    step = next_step(planner_screen(), "Mule")
    assert (step.action, step.x, step.y, step.reason) == (ACTION_CLICK, 45, 58, Reason.STRIKE_SELECTED)
    no_strike = next_step(screen((41, 58, "b Dodge (Adequate)"), (41, 59, "c Wrestle"),
                                 (41, 57, "What now?"), (60, 60, "Strike")), "Mule")
    # "Strike" sem tecla de atalho ainda é a entrada; mas "c strike/…" nunca o é
    assert no_strike.action == ACTION_CLICK
    shield_only = screen((41, 58, "b Dodge (Adequate)"), (41, 59, "c strike/copper buckler"))
    assert detect_stage(shield_only) == "attack_type"


@pytest.mark.parametrize("aim,expected_y", [("head", 37), ("neck", 36), ("upper body", 34),
                                            ("lower body", 35), ("skull", 37), ("Throat", 36),
                                            ("torso", 34), ("???", 37)])
def test_aim_menu_clicks_the_requested_part(aim, expected_y):
    step = next_step(aim_screen(), "Mule", aim=aim)
    assert (step.action, step.y, step.reason) == (ACTION_CLICK, expected_y, Reason.AIM_SELECTED)


def test_aim_falls_back_in_the_documented_order_and_never_to_a_leg():
    no_head = aim_screen(("upper body", "lower body", "neck"))
    assert (next_step(no_head, "Mule", aim="head").reason, next_step(no_head, "Mule").y) == (
        Reason.AIM_FALLBACK, 36)
    body_only = aim_screen(("upper body", "lower body", "left front leg"))
    step = next_step(body_only, "Mule", aim="head")
    assert (step.reason, step.y, step.detail) == (Reason.AIM_FALLBACK, 34, "upper body")
    legs_only = aim_screen(("left front leg", "right front leg"))
    gone = next_step(legs_only, "Mule", aim="head")
    assert (gone.action, gone.reason, gone.key) == (ACTION_ABORT, Reason.AIM_UNAVAILABLE, "LEAVESCREEN")
    explicit = next_step(aim_screen(("upper body", "left front leg")), "Mule", aim="leg")
    assert (explicit.action, explicit.y) == (ACTION_CLICK, 35)  # pedida de propósito: "left front leg"
    assert next_step(no_head, "Mule", aim="head", aim_fallbacks=()).reason == Reason.AIM_UNAVAILABLE


def test_attack_type_follows_the_preference_then_the_known_fallbacks():
    view = type_screen(modifiers=False)
    assert next_step(view, "Mule").reason == "attack_selected:stab"
    assert (next_step(view, "Mule").x, next_step(view, "Mule").y) == (53, 34)
    bash = next_step(view, "Mule", attack_pref=("bash", "stab"))
    assert (bash.reason, bash.y) == ("attack_selected:bash", 35)
    punch_only = type_screen(("punch/left hand", "punch/right hand"), modifiers=False)
    assert next_step(punch_only, "Mule", attack_pref=("stab", "bash")).reason == "attack_fallback:punch"
    slash = type_screen(("slash/iron short sword", "stab/iron short sword"), modifiers=False)
    assert next_step(slash, "Mule", attack_pref=("slash",)).reason == "attack_selected:slash"
    assert next_step(slash, "Mule", attack_pref=("hack", "stab")).reason == "attack_selected:stab"
    assert next_step(slash, "Mule", attack_pref=()).reason == "attack_selected:stab"  # omissão


def test_attack_type_with_the_weapon_lodged_aborts_instead_of_clicking_done():
    """Prova ao vivo: com a lança presa faltavam stab, bash e punch."""
    lodged = type_screen(options=(), done=True)
    step = next_step(lodged, "Mule")
    assert (step.action, step.reason, step.key) == (ACTION_ABORT, Reason.NO_ATTACK_TYPE, "LEAVESCREEN")
    assert to_dsl(step) == "key LEAVESCREEN"
    grab = type_screen(options=("grab/left hand", "throw/item"), done=True)  # não são golpes
    assert next_step(grab, "Mule").reason == Reason.NO_ATTACK_TYPE


def test_modifiers_are_toggled_before_the_entry_and_only_known_ones():
    view = type_screen()
    first = next_step(view, "Mule", modifiers=("quick", "Wild", "bogus"))
    assert (first.action, first.x, first.y, first.reason) == (ACTION_CLICK, 137, 33, "modifier:quick")
    second = next_step(view, "Mule", modifiers=("wild",))
    assert (second.x, second.y, second.reason) == (137, 35, "modifier:wild")
    multi = next_step(view, "Mule", modifiers=("multi-attack",))
    assert (multi.reason, multi.y) == ("modifier:multi", 37)
    ignored = next_step(view, "Mule", modifiers=("bogus", None, 7))
    assert ignored.reason == "attack_selected:stab"
    missing = next_step(type_screen(modifiers=False), "Mule", modifiers=("quick",))
    assert missing.reason == "attack_selected:stab"  # modificador ausente: segue sem ele


def test_after_the_strike_is_selected_only_done_or_waiting_is_left():
    view = type_screen()
    done = next_step(view, "Mule", attack_selected=True)
    assert (done.action, done.x, done.y, done.reason) == (ACTION_CLICK, 136, 31, Reason.DONE_CLICKED)
    no_done = type_screen(done=False, modifiers=False)
    waiting = next_step(no_done, "Mule", attack_selected=True)
    assert (waiting.action, waiting.reason) == (ACTION_WAIT, Reason.WAIT_GAME)
    mods = screen((134, 31, "Done"), (134, 33, "u Quick"))
    assert next_step(mods, "Mule").reason == Reason.DONE_CLICKED
    assert next_step(mods, "Mule", modifiers=("quick",)).reason == "modifier:quick"


def test_unknown_screens_wait_then_abort_after_more_than_n_in_a_row():
    unknown = screen((10, 10, "Some other viewscreen"), focus="dungeonmode/Announcements")
    for streak in range(0, 3):
        step = next_step(unknown, "Mule", unknown_streak=streak)
        assert (step.action, step.stage, step.reason) == (ACTION_WAIT, "unknown", Reason.WAIT_UNKNOWN)
    dead = next_step(unknown, "Mule", unknown_streak=3)
    assert (dead.action, dead.reason, dead.key) == (ACTION_ABORT, Reason.UNKNOWN_SCREEN, "LEAVESCREEN")
    assert next_step(unknown, "Mule", unknown_streak=1, max_unknown=1).action == ACTION_ABORT
    assert next_step(unknown, "Mule", unknown_streak=0, max_unknown=0).action == ACTION_ABORT
    # com o golpe já escolhido, um "Done" solto num ecrã desconhecido fecha-o (nunca aborta)
    stray = screen((134, 31, "Done"), focus="dungeonmode/Attack")
    stray_done = next_step(stray, "Mule", attack_selected=True, unknown_streak=9)
    assert (stray_done.action, stray_done.reason) == (ACTION_CLICK, Reason.DONE_CLICKED)


@pytest.mark.parametrize("target", ["Goblin", "Dwarf", "human merchant", 'Urist "Axe", Swordsman',
                                    "the wild boar man"])
def test_sapient_targets_are_never_selected_in_any_stage(target):
    for build in (help_screen, lambda: who_screen("The stray mule"), confirm_screen,
                  planner_screen, aim_screen, type_screen):
        step = next_step(build(), target, confirmed=True)
        assert step.action == ACTION_ABORT and step.reason == Reason.SAPIENT_TARGET
        assert step.key == "LEAVESCREEN"
    on_map = next_step(map_screen(), target)
    assert on_map.action == ACTION_DONE  # nada aberto: nada a fechar


@pytest.mark.parametrize("target", ["", "   ", None, 7, "(tame)", "the"])
def test_empty_or_meaningless_targets_abort_with_no_target(target):
    step = next_step(who_screen("The stray mule"), target)
    assert (step.action, step.reason) == (ACTION_ABORT, Reason.NO_TARGET)


def test_abort_never_presses_leavescreen_on_the_map_or_an_empty_screen():
    assert next_step(map_screen(), "Goblin").key is None
    assert next_step(ScreenView(), "Mule", unknown_streak=9).key is None
    assert unwind_step(map_screen()).key is None


def test_next_step_is_deterministic_and_never_raises_on_hostile_arguments():
    view = who_screen("The stray mule")
    assert next_step(view, "Mule") == next_step(view, "Mule")
    weird = next_step(view, "Mule", aim=object(), attack_pref=[None, 3, object()],
                      modifiers=[object()], occurrence="x", max_unknown="y")
    assert isinstance(weird, ChainStep) and weird.action in ac.ACTIONS


# ================================================================ to_dsl


def test_to_dsl_grammar_for_every_action():
    click = ChainStep("planner", ACTION_CLICK, 45, 58)
    assert to_dsl(click) == "click 45,58"
    assert to_dsl(click, settle_ms=400) == "click 45,58; sleep 400"
    assert to_dsl(click, settle_ms=400, observe=True) == "click 45,58; sleep 400; read screen"
    assert to_dsl(ChainStep("start", ACTION_KEY, key="A_ATTACK")) == "key A_ATTACK"
    assert to_dsl(ChainStep("x", ACTION_ABORT, key="LEAVESCREEN")) == "key LEAVESCREEN"
    assert to_dsl(ChainStep("x", ACTION_WAIT)) == "sleep 500"
    assert to_dsl(ChainStep("x", ACTION_WAIT), settle_ms=250, observe=True) == "sleep 250; read screen"
    assert to_dsl(ChainStep("x", ACTION_DONE)) == ""
    assert to_dsl(ChainStep("x", ACTION_ABORT)) == ""
    assert to_dsl(ChainStep("x", ACTION_ABORT), observe=True) == ""


@pytest.mark.parametrize("step", [
    ChainStep("x", ACTION_CLICK, -1, 5), ChainStep("x", ACTION_CLICK, 5, 100000),
    ChainStep("x", ACTION_CLICK, True, 5), ChainStep("x", ACTION_CLICK, "4", 5),
    ChainStep("x", ACTION_CLICK, None, None), ChainStep("x", ACTION_CLICK, 4.0, 5),
    ChainStep("x", ACTION_KEY, key="A_TALK"), ChainStep("x", ACTION_KEY, key="LEAVESCREEN; key X"),
    ChainStep("x", ACTION_KEY, key=None), ChainStep("x", ACTION_ABORT, key="SELECT"),
    "click 1,1", None, 42,
])
def test_to_dsl_refuses_anything_outside_the_closed_grammar(step):
    assert to_dsl(step) == ""


def test_to_dsl_validates_the_settle_time():
    click = ChainStep("x", ACTION_CLICK, 1, 2)
    assert to_dsl(click, settle_ms=-5) == "click 1,2"
    assert to_dsl(click, settle_ms=10 ** 9) == "click 1,2"
    assert to_dsl(ChainStep("x", ACTION_WAIT), settle_ms=-5) == ""


# ============================================================ unwind_step


def test_unwind_presses_one_leavescreen_at_a_time_and_never_on_the_map():
    menu = aim_screen()
    assert to_dsl(unwind_step(menu, 0)) == "key LEAVESCREEN"
    assert unwind_step(menu, 3, max_presses=4).key == "LEAVESCREEN"
    spent = unwind_step(menu, 4, max_presses=4, reason="step_budget")
    assert spent.is_terminal and spent.detail == "unwind_exhausted" and spent.reason == "step_budget"
    home = unwind_step(map_screen(), 0, reason="x")
    assert home.is_terminal and home.stage == "done" and home.detail == "unwound"
    assert unwind_step(ScreenView(), 0).is_terminal


def test_unwind_dismisses_the_help_overlay_with_okay_not_leavescreen():
    step = unwind_step(help_screen(), 0)
    assert (step.action, step.x, step.y) == (ACTION_CLICK, 121, 35)
    assert step.is_terminal is False


# ============================================================ AttackChain


class FakeGame:
    """Jogo de brincar: um guião de ecrãs; cada DSL executado avança para o seguinte."""

    def __init__(self, *screens):
        self.screens = list(screens)
        self.index = 0
        self.ran = []

    @property
    def view(self):
        return self.screens[min(self.index, len(self.screens) - 1)]

    def run(self, op):
        self.ran.append(op)
        self.index += 1


def drive(chain, game, limit=40):
    """Laço do executor: observa → DSL → executa → relê. Devolve os passos."""
    steps = []
    for _ in range(limit):
        step = chain.observe(game.view)
        steps.append(step)
        dsl = to_dsl(step, settle_ms=400, observe=True)
        if not dsl:
            return steps
        op = dsl.split(";")[0].strip()
        assert re.fullmatch(r"(click \d+,\d+|key (LEAVESCREEN|A_ATTACK)|sleep \d+)", op), op
        game.run(op)
    raise AssertionError(f"the chain did not finish in {limit} steps: {game.ran}")


def happy_script(after_strike=None):
    return [
        map_screen(), help_screen(),
        who_screen('Hode Ijamosman "Clockshadows", Swordsman', "The stray mule"),
        confirm_screen(), planner_screen(), aim_screen(), type_screen(),
        after_strike if after_strike is not None else map_screen(),
    ]


def test_full_chain_from_the_map_to_the_strike_matches_the_live_run():
    game = FakeGame(*happy_script())
    chain = AttackChain("Mule (tame)", confirmed=True)
    steps = drive(chain, game)
    assert game.ran == ["key A_ATTACK", "click 121,35", "click 62,52", "click 47,65",
                        "click 45,58", "click 48,37", "click 53,34"]
    assert steps[-1].action == ACTION_DONE and steps[-1].reason == Reason.STRIKE_COMMITTED
    assert chain.strike_committed and chain.finished and not chain.aborted
    assert [s.stage for s in steps][:3] == ["start", "help_overlay", "who_will_you_attack"]


def test_chain_is_driven_by_act_batch_reports_exactly_as_the_executor_will():
    """O executor corre ``act batch --json "<dsl>"`` e dá o RELATÓRIO ao ``observe``."""
    def report_for(dsl, view):
        ops = [part.strip() for part in dsl.split(";")]
        steps = []
        for index, op in enumerate(ops):
            row = {"i": index, "op": op.split()[0], "ok": True}
            if op == "read screen":
                row.update(lines=view["lines"], focus=view["focus"])
            steps.append(row)
        return {"ok": True, "via": "rpc", "dry_run": False, "steps": steps,
                "summary": {"total": len(ops), "executed": len(ops)}, "aborted_at": None}

    script = happy_script()
    chain = AttackChain("Mule (tame)", confirmed=True)
    index, sent = 0, []
    view = parse_screen(script[0])  # a 1.ª leitura (`act batch "read screen"`)
    for _ in range(20):
        step = chain.observe(view)
        dsl = to_dsl(step, settle_ms=500, observe=True)
        if not dsl:
            break
        sent.append(dsl)
        index += 1
        report = report_for(dsl, script[min(index, len(script) - 1)])
        view = screen_from_report(report)
        assert view is not None and view.focus
    assert sent[0] == "key A_ATTACK; sleep 500; read screen"
    assert sent[1] == "click 121,35; sleep 500; read screen"
    assert chain.strike_committed and chain.history[-1].action == ACTION_DONE
    assert chain.observe(report_for("read screen", script[0])).action == ACTION_DONE  # dict directo


def test_chain_clicks_done_when_the_type_entry_does_not_execute_the_strike():
    script = happy_script(after_strike=type_screen())
    script.append(map_screen())
    game = FakeGame(*script)
    steps = drive(AttackChain("Mule (tame)", aim="neck", confirmed=True), game)
    assert game.ran[-2:] == ["click 53,34", "click 136,31"]  # entrada e depois "Done"
    assert game.ran[5] == "click 48,36"  # c neck
    assert steps[-1].reason == Reason.STRIKE_COMMITTED


def test_pure_next_step_alone_walks_from_the_help_overlay_to_done():
    """Sem o ``AttackChain``: só ``next_step`` e as duas bandeiras que a função pura pede."""
    script = [help_screen(), who_screen("The stray mule"), confirm_screen(), planner_screen(),
              aim_screen(), type_screen(), type_screen(), map_screen()]
    prefs = ("stab", "bash", "strike", "punch")
    attack_selected, trace = False, []
    for view in script:
        step = next_step(view, "Mule (tame)", "head", prefs, (), True, attack_selected=attack_selected)
        trace.append((step.stage, step.action, to_dsl(step)))
        attack_selected = attack_selected or step.reason.startswith("attack_selected")
    assert trace == [
        ("help_overlay", ACTION_CLICK, "click 121,35"),
        ("who_will_you_attack", ACTION_CLICK, "click 62,51"),
        ("confirm_conflict", ACTION_CLICK, "click 47,65"),
        ("planner", ACTION_CLICK, "click 45,58"),
        ("aim_menu", ACTION_CLICK, "click 48,37"),
        ("attack_type", ACTION_CLICK, "click 53,34"),
        ("attack_type", ACTION_CLICK, "click 136,31"),  # "Done" depois do golpe escolhido
        ("done", ACTION_DONE, ""),
    ]


def test_announcements_panel_after_the_strike_is_closed_not_treated_as_a_failure():
    panel = screen((10, 10, "You stab the stray mule in the head, tearing the brain!"),
                   focus="dungeonmode/Announcements")
    game = FakeGame(*happy_script(after_strike=panel), map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True)
    steps = drive(chain, game)
    assert game.ran[-2:] == ["click 53,34", "key LEAVESCREEN"]
    assert steps[-1].action == ACTION_DONE and steps[-1].reason == Reason.STRIKE_COMMITTED
    assert chain.strike_committed and not chain.aborted


def test_stubborn_panel_after_the_strike_still_ends_in_done_after_bounded_presses():
    panel = screen((10, 10, "Something"), focus="dungeonmode/Announcements")
    game = FakeGame(*happy_script(after_strike=panel))  # o painel nunca sai
    chain = AttackChain("Mule (tame)", confirmed=True, max_unwind=2)
    steps = drive(chain, game)
    assert game.ran.count("key LEAVESCREEN") == 2
    assert steps[-1].action == ACTION_DONE and steps[-1].detail == "unwind_exhausted"
    assert chain.strike_committed and not chain.aborted


def test_chain_holds_at_the_prompt_until_consent_and_then_resumes():
    game = FakeGame(*happy_script())
    chain = AttackChain("Mule (tame)")  # confirmed=False por omissão
    for _ in range(4):  # mapa, ajuda, quem, prompt
        step = chain.observe(game.view)
        if step.action == ACTION_WAIT:
            break
        game.run(to_dsl(step))
    assert (step.action, step.reason) == (ACTION_WAIT, Reason.NEEDS_CONFIRMATION)
    assert not chain.aborted and not chain.finished
    chain.confirm()
    resumed = chain.observe(game.view)
    assert (resumed.action, resumed.x, resumed.y) == (ACTION_CLICK, 47, 65)


def test_naive_loop_without_consent_aborts_and_backs_out_of_the_prompt():
    game = FakeGame(map_screen(), who_screen("The stray mule"), confirm_screen())
    chain = AttackChain("Mule (tame)", max_repeat=2)
    steps = drive(chain, game)
    reasons = [s.reason for s in steps]
    assert reasons.count(Reason.NEEDS_CONFIRMATION) == 2 and Reason.CONFLICT_CONFIRMED not in reasons
    assert chain.abort_reason == Reason.STALLED and not chain.strike_committed
    assert game.ran.count("key LEAVESCREEN") == chain.max_unwind  # o prompt não sai: recuo limitado
    assert "click 47,65" not in game.ran  # nunca carregou em Confirm sem consentimento


def test_chain_started_inside_the_menu_does_not_reopen_it_at_the_end():
    game = FakeGame(who_screen("The stray mule"), confirm_screen(), planner_screen(), aim_screen(),
                    type_screen(), map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True)
    steps = drive(chain, game)
    assert "key A_ATTACK" not in game.ran
    assert steps[-1].action == ACTION_DONE and chain.strike_committed


def test_chain_aborts_on_a_different_creature_and_unwinds_to_the_map():
    game = FakeGame(map_screen(), who_screen("The stray mule"),
                    confirm_screen('Hode Ijamosman "Clockshadows"'),  # vira-se o alvo
                    planner_screen(), who_screen("The stray mule"), map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True)
    steps = drive(chain, game)
    assert chain.aborted and chain.abort_reason == Reason.CONFLICT_MISMATCH
    assert steps[-1].is_terminal and steps[-1].action == ACTION_ABORT
    presses = [op for op in game.ran if op == "key LEAVESCREEN"]
    assert 1 <= len(presses) <= ac.DEFAULT_MAX_UNWIND
    assert game.ran[-1] == "key LEAVESCREEN"  # a última tecla foi com o menu aberto; o mapa não leva
    assert not chain.strike_committed


def test_unwinding_stops_pressing_when_the_map_is_back():
    game = FakeGame(map_screen(), who_screen("The stray mule"), confirm_screen("the goblin"),
                    map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True)
    drive(chain, game)
    assert game.ran == ["key A_ATTACK", "click 62,51", "key LEAVESCREEN"]
    assert chain.finished and chain.abort_reason == Reason.CONFLICT_MISMATCH


def test_unknown_screens_four_times_in_a_row_abort_with_one_escape_each():
    foreign = screen((10, 10, "Dwarf Fortress Adventure"), focus="dungeonmode/Options")
    game = FakeGame(map_screen(), foreign, foreign, foreign, foreign, foreign, map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True, max_unknown=3, max_repeat=99)
    steps = drive(chain, game)
    assert [s.action for s in steps[:5]] == [ACTION_KEY, ACTION_WAIT, ACTION_WAIT, ACTION_WAIT,
                                            ACTION_ABORT]
    assert steps[4].reason == Reason.UNKNOWN_SCREEN and steps[4].key == "LEAVESCREEN"
    assert game.ran.count("key LEAVESCREEN") == 2  # a do abort + 1 do recuo; no mapa já não
    assert steps[-1].is_terminal and chain.finished


def test_same_click_on_the_same_screen_is_a_stall_after_max_repeat():
    stuck = who_screen("The stray mule")
    game = FakeGame(map_screen(), stuck)  # o último ecrã repete-se: o clique não surte efeito
    chain = AttackChain("Mule (tame)", confirmed=True, max_repeat=3)
    steps = drive(chain, game, limit=30)
    clicks = [s for s in steps if s.action == ACTION_CLICK]
    assert len(clicks) == 3  # 3 tentativas iguais; à 4.ª aborta
    assert chain.abort_reason == Reason.STALLED


def test_same_click_on_a_changing_screen_is_not_a_stall():
    pages = [screen((80, 40, f"Movement ({i} of 9)"), (90, 50, "Next"), focus="dungeonmode/Help")
             for i in range(8)]
    game = FakeGame(map_screen(), *pages, map_screen())
    steps = drive(AttackChain("Mule (tame)", confirmed=True, max_steps=30), game)
    assert not any(s.reason == Reason.STALLED for s in steps)
    assert game.ran.count("click 92,50") == 8


def test_step_budget_is_enforced_and_unwinds():
    game = FakeGame(*happy_script(after_strike=type_screen()))
    chain = AttackChain("Mule (tame)", confirmed=True, max_steps=3)
    steps = drive(chain, game)
    assert chain.abort_reason == Reason.STEP_BUDGET and not chain.strike_committed
    assert steps[-1].is_terminal


@pytest.mark.parametrize("target,reason", [("Goblin", Reason.SAPIENT_TARGET),
                                           ('Urist "Axe", Swordsman', Reason.SAPIENT_TARGET),
                                           ("", Reason.NO_TARGET), ("the", Reason.NO_TARGET)])
def test_the_menu_is_never_opened_for_a_sapient_or_empty_target(target, reason):
    game = FakeGame(map_screen(), who_screen("The stray mule"))
    chain = AttackChain(target, confirmed=True)
    steps = drive(chain, game)
    assert game.ran == [] and to_dsl(steps[0]) == ""  # nem A_ATTACK, nem LEAVESCREEN
    assert steps[0].is_terminal and chain.abort_reason == reason and chain.finished


def test_menu_that_never_opens_aborts_without_pressing_anything():
    game = FakeGame(map_screen(), map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True)
    steps = drive(chain, game)
    assert game.ran == ["key A_ATTACK"]
    assert chain.abort_reason == Reason.MENU_NOT_OPEN and steps[-1].key is None


def test_help_overlay_that_swallowed_a_attack_reopens_exactly_once():
    script = [map_screen(), help_screen(), map_screen(), who_screen("The stray mule"),
              confirm_screen(), planner_screen(), aim_screen(), type_screen(), map_screen()]
    game = FakeGame(*script)
    drive(AttackChain("Mule (tame)", confirmed=True), game)
    assert game.ran[:4] == ["key A_ATTACK", "click 121,35", "key A_ATTACK", "click 62,51"]
    again = FakeGame(map_screen(), help_screen(), map_screen(), help_screen(), map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True)
    drive(chain, again)
    assert again.ran.count("key A_ATTACK") == 2  # uma reabertura, não um laço
    assert chain.abort_reason == Reason.NO_STRIKE_COMMITTED


def test_weapon_lodged_menu_aborts_the_chain_and_never_clicks_done():
    script = happy_script()
    script[6] = type_screen(options=(), done=True)
    script += [aim_screen(), map_screen()]
    game = FakeGame(*script)
    chain = AttackChain("Mule (tame)", confirmed=True)
    drive(chain, game)
    assert chain.abort_reason == Reason.NO_ATTACK_TYPE and not chain.strike_committed
    assert "click 136,31" not in game.ran  # nada de "Done" às cegas


def test_modifiers_are_spent_one_per_observation_then_the_entry_is_clicked():
    view = type_screen()
    game = FakeGame(map_screen(), help_screen(), who_screen("The stray mule"), confirm_screen(),
                    planner_screen(), aim_screen(), view, view, view, map_screen())
    chain = AttackChain("Mule (tame)", confirmed=True, modifiers=("quick", "wild", "bogus", "quick"))
    drive(chain, game)
    assert game.ran[-3:] == ["click 137,33", "click 137,35", "click 53,34"]
    assert chain.strike_committed


def test_aim_fallback_and_target_occurrence_flow_through_the_chain():
    game = FakeGame(map_screen(), who_screen("The stray mule", "The stray mule"), confirm_screen(),
                    planner_screen(), aim_screen(("upper body", "lower body")), type_screen(),
                    map_screen())
    chain = AttackChain("Mule", aim="head", occurrence=1, confirmed=True)
    steps = drive(chain, game)
    assert game.ran[1] == "click 62,52"  # 2.ª mula
    assert game.ran[4] == "click 51,34"  # head → neck ausentes → upper body
    assert [s.reason for s in steps if s.stage == "aim_menu"] == [Reason.AIM_FALLBACK]


def test_finished_chain_is_idempotent_and_history_is_bounded():
    game = FakeGame(*happy_script())
    chain = AttackChain("Mule (tame)", confirmed=True)
    last = drive(chain, game)[-1]
    assert chain.observe(map_screen()) is last and chain.observe(None) is last
    assert chain.steps_issued >= 7 and len(chain.history) <= ac.MAX_HISTORY


def test_unwind_gives_up_after_max_unwind_presses():
    stubborn = screen((10, 10, "Some menu"), focus="dungeonmode/Options")
    game = FakeGame(map_screen(), stubborn)
    chain = AttackChain("Mule (tame)", confirmed=True, max_unknown=0, max_unwind=2, max_repeat=99)
    steps = drive(chain, game)
    assert game.ran.count("key LEAVESCREEN") == 2
    assert steps[-1].detail == "unwind_exhausted" and chain.abort_reason == Reason.UNKNOWN_SCREEN


def test_chain_never_raises_when_a_dependency_blows_up(monkeypatch):
    chain = AttackChain("Mule (tame)", confirmed=True)
    monkeypatch.setattr(ac, "detect_stage", lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("x")))
    step = chain.observe(map_screen())
    assert (step.action, step.reason) == (ACTION_ABORT, Reason.INTERNAL_ERROR)
    assert chain.finished and chain.observe(map_screen()) is step


def test_chain_constructor_clamps_nonsense_arguments():
    chain = AttackChain(None, max_steps=-4, max_unknown=-1, max_repeat=0, max_unwind=-9,
                        occurrence=-2, modifiers="quick")
    assert (chain.max_steps, chain.max_unknown, chain.max_repeat, chain.max_unwind) == (1, 0, 1, 0)
    assert chain.observe(who_screen("The stray mule")).reason == Reason.NO_TARGET


# ===================================================== texto do jogo é DADO


def test_screen_text_never_reaches_the_dsl():
    inj = "; key A_TALK; click 1,1 # \x1b[31m"
    hostile = [
        map_screen(), help_screen(),
        who_screen(f"The stray mule{inj}", "The stray mule"),
        confirm_screen(f"the stray mule{inj}"),
        planner_screen(), aim_screen((f"head{inj}", "head", "neck")),
        type_screen((f"stab/iron spear{inj}", "stab/iron spear")), map_screen(),
    ]
    game = FakeGame(*hostile)
    steps = drive(AttackChain("Mule (tame)", confirmed=True), game)
    for step in steps:
        dsl = to_dsl(step, settle_ms=300, observe=True)
        for op in filter(None, (p.strip() for p in dsl.split(";"))):
            assert re.fullmatch(r"click \d+,\d+|key (LEAVESCREEN|A_ATTACK)|sleep \d+|read screen", op), op
        assert "\x1b" not in step.label and "\x1b" not in step.detail
        assert len(step.label) <= ac.MAX_LABEL_CHARS and len(step.detail) <= ac.MAX_DETAIL_CHARS


def test_seeded_fuzz_never_raises_and_the_dsl_stays_in_the_closed_grammar():
    rng = random.Random(20261010)
    fragments = [
        "Who will you attack?", "a The stray mule", "Really start a conflict with the stray mule?",
        "Really start a conflict", "Confirm", "Cancel", "a Strike", "b Dodge (Adequate)",
        "What do you want to aim for?", "a upper body", "c neck", "d head", "Attack head:",
        "a stab/iron spear", "b bash/shaft/iron spear", "d punch/hands", "u Quick", "Done",
        "Okay", "Next", "Don't show again", "The goblin", "ghost", "\x00\x1b[2J", "‮",
        "x" * 600, "; key A_TALK", "click 1,1", "# comment", "{}", "[", "?" * 50,
    ]
    focuses = [MAP_FOCUS, ATTACK_FOCUS, HELP_FOCUS, "dungeonmode/Options", "", "weird/focus"]
    for _ in range(300):
        lines = []
        for _row in range(rng.randint(0, 30)):
            row = [" " * rng.randint(0, 40)]
            for _frag in range(rng.randint(0, 3)):
                row += [rng.choice(fragments), " " * rng.randint(1, 3)]
            lines.append("".join(row))
        payload = {"focus": [rng.choice(focuses)], "lines": lines}
        assert detect_stage(payload) in ac.STAGES
        step = next_step(payload, rng.choice(["Mule (tame)", "Goblin", "", "stray rabbit"]),
                         aim=rng.choice(["head", "neck", "leg", "???"]), confirmed=rng.random() < 0.5)
        dsl = to_dsl(step, settle_ms=rng.choice([None, 0, 500, -3]), observe=rng.random() < 0.5)
        assert re.fullmatch(
            r"|(click \d+,\d+|key (LEAVESCREEN|A_ATTACK)|sleep \d+)(; sleep \d+)?(; read screen)?"
            r"|read screen", dsl) or dsl == "", dsl
        chain = AttackChain("Mule (tame)", confirmed=rng.random() < 0.5)
        for _step in range(8):
            out = chain.observe(payload)
            assert isinstance(out, ChainStep) and out.action in ac.ACTIONS


def test_label_and_option_parsers_cover_the_documented_entries():
    one = parse_attack_label("a stab/iron spear")
    assert (one.verb, one.hotkey, one.detail) == ("stab", "a", "iron spear")
    assert parse_attack_label("b bash/shaft/iron spear").detail == "shaft/iron spear"
    assert parse_attack_label("punch/hands").verb == "punch"
    assert parse_attack_label("Stabs/iron spear").verb == "stab"
    assert parse_attack_label("a grab/left hand") is None
    assert parse_attack_label(None) is None
    options = list_attack_options(type_screen())
    assert [o.verb for o in options] == ["stab", "bash", "strike", "punch"]
    assert [(o.x, o.y) for o in options][0] == (53, 34)
