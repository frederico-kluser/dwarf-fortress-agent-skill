"""ScreenTranslator: ``xiaomi/mimo-v2.6-pro`` com reasoning effort ``none``.

Motor de tradução de ecrã decidido em ``pesquisas/traducao-ecra-mimo.md`` §1.
Contratos testados: body com ``reasoning: {"effort": "none"}`` e ``temperature=0``;
degradação do parâmetro ``reasoning`` cai para AUSENTE e nunca para effort ligado;
preço registado; ``build_screen_translator`` mantém as proteções do chain
(identity sem chave, glossário/TM à frente do motor).
"""

from __future__ import annotations

from dwarf_translate_fakes import FAKE_KEY, FakeResponse, FakeTransport, chat_response

from system_one_dwarf.translate import (
    SCREEN_MODEL,
    SCREEN_REASONING_PARAM,
    SCREEN_TEMPERATURE,
    ChainTranslator,
    ScreenTranslator,
    build_screen_translator,
)
from system_one_dwarf.translate.fallback import Glossary, IdentityTranslator
from system_one_dwarf.translate.translator import price_for


# ------------------------------------------------------------------ constants


def test_screen_engine_defaults_are_pinned() -> None:
    assert SCREEN_MODEL == "xiaomi/mimo-v2.6-pro"
    assert SCREEN_TEMPERATURE == 0.0
    assert SCREEN_REASONING_PARAM == {"effort": "none"}


def test_screen_model_price_is_registered() -> None:
    assert price_for(SCREEN_MODEL) == (0.435, 0.87)
    assert price_for("modelo/desconhecido") != price_for(SCREEN_MODEL)


# --------------------------------------------------------------- request body


def test_envia_effort_none_temperature_zero_e_modelo_mimo() -> None:
    transport = FakeTransport([chat_response("olá")])
    translator = ScreenTranslator(lambda: FAKE_KEY, transport=transport)

    result = translator.translate_screen("hello")

    body = transport.bodies()[0]
    assert body["model"] == SCREEN_MODEL
    assert body["reasoning"] == {"effort": "none"}
    assert body["temperature"] == 0.0
    assert result.translated_pt == "olá"


def test_system_prompt_e_pt_pt_com_regras_de_keybinds_e_itens() -> None:
    transport = FakeTransport([chat_response("olá")])
    ScreenTranslator(lambda: FAKE_KEY, transport=transport).translate_screen("hello")

    system = transport.bodies()[0]["messages"][0]["content"]
    assert "European Portuguese (pt-PT)" in system
    assert "keybinds" in system
    assert "common nouns" in system


def test_nome_e_modelo_do_repr_nao_vazam_a_chave() -> None:
    translator = ScreenTranslator(lambda: FAKE_KEY, transport=FakeTransport())
    assert translator.name == "screen-translator"
    assert translator.reasoning_mode == "none"
    assert FAKE_KEY not in repr(translator)


# ------------------------------------------------- reasoning degradation rules


def test_rejeicao_do_effort_cai_para_campo_ausente_nunca_para_effort_ligado() -> None:
    transport = FakeTransport(
        [
            FakeResponse(status_code=400, payload={"error": {"message": "bad request"}}),
            chat_response("olá"),
        ]
    )
    translator = ScreenTranslator(lambda: FAKE_KEY, transport=transport)

    result = translator.translate_screen("hello")

    # 1.ª chamada: effort "none"; 2.ª: campo ausente (NUNCA {"effort": "low"}).
    assert transport.reasoning_params() == [{"effort": "none"}, "<absent>"]
    assert translator.reasoning_mode == "dropped"
    assert result.translated_pt == "olá"


def test_degradacao_e_unico_passo_e_uma_segunda_rejeicao_e_terminal() -> None:
    from system_one_dwarf.translate import TranslationRequestError

    transport = FakeTransport(
        [
            FakeResponse(status_code=422, payload={"error": {"message": "nope"}}),
            FakeResponse(status_code=422, payload={"error": {"message": "nope"}}),
        ]
    )
    translator = ScreenTranslator(lambda: FAKE_KEY, transport=transport)

    try:
        translator.translate_screen("hello")
    except TranslationRequestError:
        pass
    else:  # pragma: no cover - falha o contrato
        raise AssertionError("a segunda rejeição devia ser terminal")

    assert transport.reasoning_params() == [{"effort": "none"}, "<absent>"]
    assert translator.reasoning_mode == "dropped"


# ------------------------------------------------------- build_screen_translator


def test_sem_chave_utilizavel_e_identity() -> None:
    assert isinstance(build_screen_translator(None), IdentityTranslator)
    assert isinstance(build_screen_translator(lambda: ""), IdentityTranslator)
    assert isinstance(build_screen_translator(lambda: "   "), IdentityTranslator)

    def broken() -> str:
        raise RuntimeError("no key")

    assert isinstance(build_screen_translator(broken), IdentityTranslator)


def test_com_chave_e_chain_com_primario_mimo() -> None:
    transport = FakeTransport([chat_response("olá")])
    translator = build_screen_translator(
        lambda: FAKE_KEY, transport=transport, glossary=Glossary.empty()
    )

    assert isinstance(translator, ChainTranslator)
    assert translator.primary.model == SCREEN_MODEL

    result = translator.translate_screen("hello")

    assert result.translated_pt == "olá"
    assert transport.bodies()[0]["reasoning"] == {"effort": "none"}
    assert transport.bodies()[0]["temperature"] == 0.0
