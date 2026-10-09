"""Objetivo com campos ``_pt`` (item 2): canónico EN + exibição PT, aditivo.

O objetivo é material de prompt, por isso o canónico é **EN**.  Os campos
``narrative_pt`` / ``subgoals[].text_pt`` são a exibição PT produzida pelo seam
``safe_translate`` do tradutor da campanha.  Regra aditiva escolhida (e
documentada em ``docs/api.md``): o campo ``_pt`` só aparece quando há uma tradução
de exibição **diferente** do canónico — sem chave (identity) o payload mantém-se
exatamente o do contrato original, para nenhum cliente antigo mudar de forma.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fakes import FakeTranslator, MarkerTranslator

SECRET = "TEST-KEY-NOT-REAL-0001"


def keyed_client(make_client) -> TestClient:
    client = make_client(
        translator_factory=lambda get_key, **kwargs: FakeTranslator(
            pt_to_en={"Chegar vivo.": "Reach the fortress alive.", "Encontrar água": "Find water"},
            en_to_pt={
                "Reach the fortress alive.": "Chegar vivo à fortaleza.",
                "Find water": "Encontrar água",
            },
        )
    )
    cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]
    client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})
    return client, cid


def test_objective_pt_fields_follow_the_canonical_en(make_client):
    client, cid = keyed_client(make_client)

    body = client.put(
        f"/api/campaigns/{cid}/objective",
        json={"narrative": "Chegar vivo.", "subgoals": [{"id": "sg-1", "text": "Encontrar água"}]},
    ).json()

    assert body["narrative"] == "Reach the fortress alive."
    assert body["narrative_pt"] == "Chegar vivo à fortaleza."
    assert body["subgoals"][0]["text"] == "Find water"
    assert body["subgoals"][0]["text_pt"] == "Encontrar água"
    # GET devolve exatamente o mesmo objeto (sem deriva entre escrita e leitura).
    assert client.get(f"/api/campaigns/{cid}/objective").json() == body


def test_objective_without_translation_keeps_the_original_shape(client: TestClient, campaign):
    """Sem chave não há ``_pt`` para mostrar: a forma original fica intacta."""
    cid = campaign["id"]

    assert client.get(f"/api/campaigns/{cid}/objective").json() == {"narrative": "", "subgoals": []}

    put = client.put(
        f"/api/campaigns/{cid}/objective",
        json={"narrative": "Reach alive.", "subgoals": [{"id": "sg-1", "text": "Find water"}]},
    ).json()

    assert put == {
        "narrative": "Reach alive.",
        "subgoals": [{"id": "sg-1", "text": "Find water", "done": False}],
    }
    assert "narrative_pt" not in put
    assert "text_pt" not in put["subgoals"][0]


def test_marker_translator_shows_both_directions(make_client):
    client = make_client(translator=MarkerTranslator())
    cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]

    body = client.put(
        f"/api/campaigns/{cid}/objective", json={"narrative": "Chegar vivo.", "subgoals": []}
    ).json()

    assert body["narrative"] == "EN<Chegar vivo.>"
    assert body["narrative_pt"] == "PT<EN<Chegar vivo.>>"


def test_pt_field_is_omitted_when_it_would_repeat_the_canonical(make_client):
    """Uma tradução igual ao canónico não acrescenta nada e é omitida."""
    client = make_client(translator=FakeTranslator())  # identity
    cid = client.post("/api/campaigns", json={"name": "Urist"}).json()["id"]

    body = client.put(
        f"/api/campaigns/{cid}/objective",
        json={"narrative": "Reach alive.", "subgoals": [{"text": "Find water"}]},
    ).json()

    assert body == {
        "narrative": "Reach alive.",
        "subgoals": [{"id": "sg-1", "text": "Find water", "done": False}],
    }