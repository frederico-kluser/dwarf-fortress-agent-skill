"""Wiring do TRADUTOR (item 1): chave da campanha → tradutor real; sem chave → identity.

Prova, offline, que o app deixa de usar sempre o ``IdentityTranslator``:

* ``POST .../keys`` guarda a chave OpenRouter em ``chaves.sqlite`` da campanha;
* o primeiro uso a seguir constrói o tradutor com
  ``KeyStore.get_key("openrouter")`` → ``translate.build_translator`` (a fábrica é
  injetada, por isso não há rede nenhuma);
* ``PATCH .../memory`` traduz PT→EN antes de guardar e ``GET`` devolve o canónico
  EN + os campos ``*_pt``;
* sem chave, tudo continua exatamente como antes (identity).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from fakes import MarkerTranslator

SECRET = "TEST-KEY-NOT-REAL-0001"


def key_factory(seen: dict):
    """Fábrica de tradutores que regista a chave que o app lhe entregou."""

    def factory(get_key, **kwargs):
        seen["key"] = get_key()
        seen["kwargs"] = dict(kwargs)
        return MarkerTranslator()

    return factory


def create_campaign(client: TestClient, name: str = "Urist") -> str:
    response = client.post("/api/campaigns", json={"name": name, "is_new_game": True})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_without_a_key_the_memory_stays_identity(client: TestClient, campaign):
    """Comportamento documentado: sem chave não há tradução nenhuma."""
    client.patch(
        f"/api/campaigns/{campaign['id']}/memory",
        json={"narrative": "O aventureiro acorda numa clareira."},
    )

    body = client.get(f"/api/campaigns/{campaign['id']}/memory").json()

    assert body["narrative"] == "O aventureiro acorda numa clareira."
    assert body["narrative_pt"] == "O aventureiro acorda numa clareira."


def test_stored_key_builds_the_translator_from_the_campaign_key_store(make_client):
    seen: dict = {}
    client = make_client(translator_factory=key_factory(seen))
    cid = create_campaign(client)

    # Antes da chave: identity, o texto fica como foi escrito.
    client.patch(f"/api/campaigns/{cid}/memory", json={"narrative": "Sem chave."})
    assert client.get(f"/api/campaigns/{cid}/memory").json()["narrative"] == "Sem chave."
    assert "key" not in seen  # a fábrica nunca foi chamada sem chave

    # Com a chave: PT -> EN na escrita e EN -> PT na leitura.
    stored = client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})
    assert stored.status_code == 200

    client.patch(
        f"/api/campaigns/{cid}/memory",
        json={"narrative": "O aventureiro acorda numa clareira.", "episodes": [{"content": "Subiu a colina."}]},
    )
    body = client.get(f"/api/campaigns/{cid}/memory").json()

    assert seen["key"] == SECRET  # a chave veio mesmo do KeyStore da campanha
    assert body["narrative"] == "EN<O aventureiro acorda numa clareira.>"
    assert body["narrative_pt"] == "PT<EN<O aventureiro acorda numa clareira.>>"
    assert body["episodes"][0]["content"] == "EN<Subiu a colina.>"
    assert body["episodes"][0]["content_pt"] == "PT<EN<Subiu a colina.>>"


def test_objective_and_inputs_use_the_same_per_campaign_translator(make_client):
    seen: dict = {}
    client = make_client(translator_factory=key_factory(seen))
    cid = create_campaign(client)
    client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})

    objective = client.put(
        f"/api/campaigns/{cid}/objective",
        json={"narrative": "Chegar vivo.", "subgoals": [{"text": "Encontrar água"}]},
    ).json()
    injection = client.post(
        f"/api/campaigns/{cid}/inputs", json={"text": "Há uma caverna a norte."}
    ).json()

    assert seen["key"] == SECRET
    assert objective["narrative"] == "EN<Chegar vivo.>"
    assert objective["subgoals"][0]["text"] == "EN<Encontrar água>"
    assert injection["text"] == "EN<Há uma caverna a norte.>"


def test_keys_are_per_campaign_so_the_translator_is_too(make_client):
    seen: dict = {}
    client = make_client(translator_factory=key_factory(seen))
    first = create_campaign(client, "Urist")
    second = create_campaign(client, "Boatmurdered")
    client.post(f"/api/campaigns/{first}/keys", json={"provider": "openrouter", "key": SECRET})

    # O fake de CoALA é partilhado pelos dois campanhas (fixture), por isso a
    # prova fica no corpo de cada PATCH: cada campanha traduz com o *seu* tradutor.
    translated = client.patch(f"/api/campaigns/{first}/memory", json={"narrative": "Primeira."})
    identity = client.patch(f"/api/campaigns/{second}/memory", json={"narrative": "Segunda."})

    assert translated.json()["narrative"] == "EN<Primeira.>"
    assert identity.json()["narrative"] == "Segunda."


def test_a_broken_factory_degrades_to_identity(make_client):
    def factory(get_key, **kwargs):
        raise RuntimeError("translate piece is unavailable")

    client = make_client(translator_factory=factory)
    cid = create_campaign(client)
    client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})

    client.patch(f"/api/campaigns/{cid}/memory", json={"narrative": "Sem tradutor."})

    assert client.get(f"/api/campaigns/{cid}/memory").json()["narrative"] == "Sem tradutor."


def test_explicit_translator_still_wins_over_the_per_campaign_one(make_client):
    """``create_app(translator=...)`` é o seam de sempre: vence para todas."""
    seen: dict = {}
    client = make_client(translator=MarkerTranslator(), translator_factory=key_factory(seen))
    cid = create_campaign(client)
    client.post(f"/api/campaigns/{cid}/keys", json={"provider": "openrouter", "key": SECRET})

    client.patch(f"/api/campaigns/{cid}/memory", json={"narrative": "Explícito."})

    assert client.get(f"/api/campaigns/{cid}/memory").json()["narrative"] == "EN<Explícito.>"
    assert "key" not in seen  # o tradutor injetado foi usado; a fábrica nunca correu