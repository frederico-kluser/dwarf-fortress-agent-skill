#!/usr/bin/env python3
"""Constrói o índice de busca FTS5 (index.db) sobre todos os artigos das skills.

Stdlib-only (sqlite3 com FTS5) — sem dependências externas, portável para qualquer
agente que rode Python (pi, Claude Code, Cursor, Codex, OpenRouter custom).

Uso:  python3 build_index.py
Saída: <skills>/scripts/index.db  (artefato derivado; rode após (re)gerar as skills)
"""
import os, sys, sqlite3, glob

HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_ROOT = os.path.dirname(HERE)
DB = os.path.join(HERE, "index.db")


def first_heading(text, fallback):
    for line in text.splitlines():
        if line.startswith("# "):
            return line[2:].strip()
    return fallback


def build(verbose=True):
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.execute("""CREATE VIRTUAL TABLE docs USING fts5(
        title, skill, path UNINDEXED, body,
        tokenize='porter unicode61'
    )""")

    n = 0
    for skill_dir in sorted(glob.glob(os.path.join(SKILLS_ROOT, "*", "references"))):
        skill = os.path.basename(os.path.dirname(skill_dir))
        for path in sorted(glob.glob(os.path.join(skill_dir, "*.md"))):
            with open(path, encoding="utf-8") as f:
                body = f.read()
            title = first_heading(body, os.path.basename(path)[:-3])
            rel = os.path.relpath(path, SKILLS_ROOT)
            con.execute("INSERT INTO docs(title, skill, path, body) VALUES (?,?,?,?)",
                        (title, skill, rel, body))
            n += 1
    con.commit()
    con.execute("INSERT INTO docs(docs) VALUES('optimize')")
    con.commit()
    con.close()
    if verbose:
        print(f"Indexados {n} artigos em {DB}")
    return n


if __name__ == "__main__":
    build()
