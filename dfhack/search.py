#!/usr/bin/env python3
"""Busca full-text BM25 nos artigos das skills (SQLite FTS5, stdlib-only).

Conserta as falhas do antigo search.sh (ripgrep de frase contígua):
  - AND implícito de termos (multi-palavra funciona);
  - ranking BM25 (título pesa mais) + snippet de contexto;
  - stemming porter (miner/mining, smelt/smelting);
  - tradução PT→EN automática via glossary-pt-en.tsv (perguntas em português
    casam o conteúdo em inglês);
  - cascata AND → OR → prefixo quando há 0 resultados.

Uso:
  python3 search.py "steel smelting"                 # todas as skills
  python3 search.py --skill df-materiais "como fazer aço"
  python3 search.py --json --limit 8 "magma forge"
"""
import os, sys, re, sqlite3, json, argparse

HERE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(HERE, "index.db")
GLOSSARY = os.path.join(HERE, "glossary-pt-en.tsv")


def load_glossary():
    g = {}
    if os.path.exists(GLOSSARY):
        with open(GLOSSARY, encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if not line or line.startswith("#") or "\t" not in line:
                    continue
                pt, en = line.split("\t", 1)
                g[pt.strip().lower()] = en.strip().lower().split()
    return g


def tokenize(q):
    return [t for t in re.split(r"[^0-9A-Za-zÀ-ÿ]+", q.lower()) if len(t) > 1]


def build_match(tokens, glossary, op="AND", prefix=False):
    """Monta expressão FTS5: cada token PT vira grupo (token OR en1 OR en2)."""
    groups = []
    for t in tokens:
        variants = [t] + glossary.get(t, [])
        variants = list(dict.fromkeys(variants))
        if prefix:
            variants = [f'"{v}"*' if not v.endswith("*") else v for v in variants]
        else:
            variants = [f'"{v}"' for v in variants]
        groups.append("(" + " OR ".join(variants) + ")")
    return f" {op} ".join(groups)


def run(con, match, skill, limit):
    sql = ("SELECT title, skill, path, snippet(docs, 3, '[', ']', ' … ', 12) "
           "FROM docs WHERE docs MATCH ?")
    params = [match]
    if skill and skill != "all":
        sql += " AND skill = ?"
        params.append(skill)
    sql += " ORDER BY bm25(docs, 5.0, 2.0, 1.0) LIMIT ?"
    params.append(limit)
    return con.execute(sql, params).fetchall()


def ensure_index():
    """Constrói index.db na primeira vez (artefato derivado, não versionado)."""
    if os.path.exists(DB):
        return True
    try:
        import build_index
        print("Construindo índice de busca (primeira execução)…", file=sys.stderr)
        build_index.build(verbose=False)
        return os.path.exists(DB)
    except Exception as e:
        print(f"falha ao construir índice ({e}); rode build_index.py manualmente",
              file=sys.stderr)
        return False


def search(query, skill="all", limit=8):
    if not ensure_index():
        return None, "índice ausente — rode scripts/build_index.py"
    con = sqlite3.connect(DB)
    glossary = load_glossary()
    tokens = tokenize(query)
    if not tokens:
        return [], "consulta vazia"
    for op, prefix in (("AND", False), ("OR", False), ("OR", True)):
        match = build_match(tokens, glossary, op=op, prefix=prefix)
        try:
            rows = run(con, match, skill, limit)
        except sqlite3.OperationalError:
            rows = []
        if rows:
            return rows, op + ("+prefix" if prefix else "")
    return [], "sem resultados"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--skill", default="all")
    ap.add_argument("--limit", type=int, default=8)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("query", nargs="+")
    a = ap.parse_args()
    rows, mode = search(" ".join(a.query), a.skill, a.limit)

    if rows is None:
        print(mode, file=sys.stderr); sys.exit(1)
    if a.json:
        print(json.dumps([
            {"title": t, "skill": s, "path": p, "snippet": re.sub(r"\s+", " ", sn)}
            for t, s, p, sn in rows], ensure_ascii=False, indent=2))
        return
    if not rows:
        print(f"Nenhum resultado para: {' '.join(a.query)}"); return
    for t, s, p, sn in rows:
        print(f"[{s}] {t}\n  {p}\n  … {re.sub(chr(10), ' ', sn).strip()} …\n")


if __name__ == "__main__":
    main()
