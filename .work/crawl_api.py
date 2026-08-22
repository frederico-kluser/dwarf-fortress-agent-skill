#!/usr/bin/env python3
"""Fase 1 (V3) — Re-aquisição de conteúdo da wiki VIVA via MediaWiki Action API.

Por quê: o pipeline antigo lia wikitext bruto de um dump de 2023 e o pandoc não
expande templates MediaWiki — infoboxes, tabelas de token e subpáginas /raw saíam
vazias ou viravam "[TABLE]". A API `action=parse` devolve o HTML JÁ RENDERIZADO
(templates expandidos server-side), resolvendo isso na fonte.

Escopo (decisão do usuário): só o namespace 0 (conteúdo v50/Premium atual).

Saída: .work/pages.jsonl  — um objeto por página: {title, url, html, categories}.
Resume: pula títulos já presentes no pages.jsonl (crawl educado, sem re-bater).

Etiqueta (https://www.mediawiki.org/wiki/API:Etiquette): requisições em série,
~1 req/s, User-Agent honesto com contato, maxlag=5, backoff exponencial.
"""
import os, sys, json, time, html as _html

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
API = "https://dwarffortresswiki.org/api.php"
OUT = os.path.join(HERE, "pages.jsonl")
NAMESPACE = 0                      # 0 = Main = conteúdo v50 atual
DELAY = 0.4                        # segundos entre requisições (serial, educado)
UA = "dwarf-rag-fortress/2.0 (+https://github.com/frederico-kluser/dwarf-fortress-agent-skill)"

session = requests.Session()
session.headers.update({"User-Agent": UA})


def api_get(params, max_retries=5):
    """GET com format=json, maxlag e backoff exponencial."""
    params = dict(params)
    params.setdefault("format", "json")
    params.setdefault("formatversion", "2")
    params.setdefault("maxlag", "5")
    delay = DELAY
    for attempt in range(max_retries):
        try:
            r = session.get(API, params=params, timeout=60)
            if r.status_code == 200:
                data = r.json()
                if "error" in data and data["error"].get("code") == "maxlag":
                    wait = int(r.headers.get("Retry-After", 5))
                    print(f"    maxlag, esperando {wait}s", file=sys.stderr)
                    time.sleep(wait)
                    continue
                return data
            if r.status_code in (429, 503):
                wait = int(r.headers.get("Retry-After", delay * 2))
                print(f"    HTTP {r.status_code}, esperando {wait}s", file=sys.stderr)
                time.sleep(wait)
                delay *= 2
                continue
            r.raise_for_status()
        except (requests.RequestException, ValueError) as e:
            print(f"    erro ({e}); retry {attempt + 1}/{max_retries}", file=sys.stderr)
            time.sleep(delay)
            delay *= 2
    return None


def enumerate_titles():
    """list=allpages no ns 0, sem redirects. Retorna lista de títulos."""
    titles = []
    apcontinue = None
    while True:
        params = {
            "action": "query", "list": "allpages",
            "apnamespace": NAMESPACE, "apfilterredir": "nonredirects",
            "aplimit": "500",
        }
        if apcontinue:
            params["apcontinue"] = apcontinue
        data = api_get(params)
        if not data:
            print("  falha ao enumerar; parando", file=sys.stderr)
            break
        batch = data.get("query", {}).get("allpages", [])
        titles.extend(p["title"] for p in batch)
        print(f"  enumeradas {len(titles)} páginas...", file=sys.stderr)
        cont = data.get("continue", {})
        apcontinue = cont.get("apcontinue")
        if not apcontinue:
            break
        time.sleep(DELAY)
    return titles


def parse_page(title):
    """action=parse → HTML renderizado + categorias. Retorna (html, [cats]) ou None."""
    params = {
        "action": "parse", "page": title,
        "prop": "text|categories",
        "redirects": "1", "disablelimitreport": "1",
        "disableeditsection": "1", "disabletoc": "1",
    }
    data = api_get(params)
    if not data or "parse" not in data:
        return None
    p = data["parse"]
    html = p.get("text", "")
    if isinstance(html, dict):           # formatversion antigo usa {"*": ...}
        html = html.get("*", "")
    cats = []
    for c in p.get("categories", []):
        name = c.get("category") if isinstance(c, dict) else None
        if name:
            cats.append(name.replace("_", " "))
    return _html.unescape(html), cats


def load_done():
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            for line in f:
                try:
                    done.add(json.loads(line)["title"])
                except Exception:
                    pass
    return done


def main():
    print("Enumerando títulos do namespace 0...", file=sys.stderr)
    titles = enumerate_titles()
    print(f"Total de títulos: {len(titles)}", file=sys.stderr)

    done = load_done()
    if done:
        print(f"Resume: {len(done)} já coletadas, pulando.", file=sys.stderr)

    todo = [t for t in titles if t not in done]
    print(f"A coletar: {len(todo)}", file=sys.stderr)

    written = 0
    with open(OUT, "a", encoding="utf-8") as out:
        for i, title in enumerate(todo, 1):
            res = parse_page(title)
            time.sleep(DELAY)
            if res is None:
                print(f"  [{i}/{len(todo)}] FALHA: {title}", file=sys.stderr)
                continue
            html, cats = res
            url = "https://dwarffortresswiki.org/index.php/" + title.replace(" ", "_")
            rec = {"title": title, "url": url, "categories": cats, "html": html}
            out.write(json.dumps(rec, ensure_ascii=False) + "\n")
            out.flush()
            written += 1
            if i % 50 == 0:
                print(f"  [{i}/{len(todo)}] {written} gravadas (última: {title})",
                      file=sys.stderr)

    print(f"\nDONE. {written} páginas novas gravadas em {OUT}", file=sys.stderr)


if __name__ == "__main__":
    main()
