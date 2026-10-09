#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
coala.py — Motor de memória persistente CoALA sobre SQLite (apenas stdlib) · v2.

Arquitetura (Sumers et al., "Cognitive Architectures for Language Agents"):
  - Memória Episódica    -> registos cronológicos ancorados no tempo
  - Memória Semântica    -> factos atemporais com proveniência e supersessão
  - Memória Procedimental-> conhecimento executável (skills, comandos, contratos)
  - Working Memory       -> `recall` materializa excertos orçamentados p/ prompt

Memória LOCAL por projeto — NÃO existe memória global. A base vive em
  <projeto>/.agents/<projeto>-coala-memory-agent-skill/memory/coala.sqlite
e é resolvida por esta ordem (a primeira que se aplica):
  1. --db <caminho>      2. env COALA_DB
  3. motor vendorizado: este ficheiro está em <x>-coala-memory-agent-skill/scripts/
  4. descoberta: sobe a partir do diretório atual até .agents/*-coala-memory-agent-skill/coala.json
  5. nada encontrado -> erro (exit 3) com a instrução de instalação; nunca cai numa base global.

Modo WAL ativo. FTS5 (BM25) para ranking léxico + vetores para ranking semântico,
fusão Reciprocal Rank Fusion (RRF) executada em SQL com funções de janela.

Esquema v3: cada registo guarda o seu ID POR CONTEÚDO (`content_id`, ver CID_FIELDS) — o mesmo em
qualquer máquina. `export --format jsonl` → `import --jsonl` refaz a base (registos, supersessões e
grafo) pelos ids; reimportar é NO-OP. `forget --tag T` é a única operação que APAGA (privacidade).

Uso:  python3 scripts/coala.py <comando> [opções]   |   python3 scripts/coala.py --selftest
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import io
import json
import os
import re
import shutil
import sqlite3
import stat
import struct
import subprocess
import sys
import tempfile
import urllib.parse
from datetime import datetime, timezone

# ----------------------------------------------------------------- constantes
ENGINE_VERSION = "2.1.0"
SCHEMA_VERSION = 3               # PRAGMA user_version (v0/v1 = esquema original, sem meta; v3 = ids por conteúdo)
RRF_K = 60                       # constante k da fórmula RRF (Cormack et al., 2009)
EMBED_DIMS = 256                 # dimensões do vetor (fallback hashing)
CHUNK_MAX_CHARS = 1200           # tamanho máximo de um chunk
TOKEN_CHARS = 4                  # estimativa: 1 token ≈ 4 caracteres
MAX_OUTPUT_BYTES = 48 * 1024     # portão de saída: 48 KB
DEFAULT_BUDGET = 2000            # orçamento padrão do recall (tokens estimados)

DB_ENV = "COALA_DB"
W_FTS_ENV = "COALA_RRF_W_FTS"
W_VEC_ENV = "COALA_RRF_W_VEC"

SKILL_SUFFIX = "-agent-skill"                    # convenção: <projeto>-agent-skill (minúsculas)
LEGACY_SKILL_SUFFIXES = ("-coala-memory-agent-skill", "-memory-agent-skill")  # nomes antigos: migram


def is_memory_skill(name: str) -> bool:
    """Reconhece uma skill de memória local (novo nome ou legado)."""
    return name.endswith(SKILL_SUFFIX) or any(name.endswith(s) for s in LEGACY_SKILL_SUFFIXES)
MANIFEST_NAME = "coala.json"                 # manifesto da instalação local
INGEST_CONFIG_NAME = "ingest.json"           # fontes de material do projeto
DB_SUBPATH = ("memory", "coala.sqlite")
INSTALL_HINT = ("python3 ~/Agent-Skills/coala-agent-skill/scripts/coala-install.py"
                " install --project <raiz-do-projeto>")
# memória global aposentada em 2026-09-26: só é referida para o `doctor` avisar se ressurgir
LEGACY_GLOBAL_DB = os.path.join(os.path.expanduser("~"), ".coala-memory", "coala.sqlite")

MEMORY_TYPES = ("episodic", "semantic", "procedural")
ORIGINS = ("owner", "agent", "untrusted", "system")
REQUIRED_TABLES = ("memory_entries", "entity_nodes", "entity_edges", "chunks", "chunks_fts",
                   "provenance", "coala_meta", "ingest_sources")

# Id por conteúdo (esquema v3). Contrato partilhado com a memória dos sites (sitemem/1):
#   sha256(json.dumps({schema, key, type, site, page, kind, body}, sort_keys=True,
#          separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()[:16]
# SEM status, evidence, valid_from, ttl_days, supersedes nem campos só-locais — promover um registo
# (hypothesis → validated) não muda o id. Campo ausente conta como null.
CID_FIELDS = ("schema", "key", "type", "site", "page", "kind", "body")
# registos genéricos do motor (add/ingest, texto livre) usam o MESMO contrato com este `schema`
# e um `body` com o conteúdo e os metadados imutáveis (ver entry_content_id)
GENERIC_CID_SCHEMA = "coala/entry"
CID_RE = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")     # id trazido de fora (linha JSONL / registo)
LIKE_ESCAPE = "\\"                                   # escape dos LIKE de tags (`%`/`_` são literais)

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS memory_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_type TEXT NOT NULL CHECK (memory_type IN ('episodic','semantic','procedural')),
  content TEXT NOT NULL,
  origin_class TEXT NOT NULL DEFAULT 'agent'
    CHECK (origin_class IN ('owner','agent','untrusted','system')),
  supersession_key TEXT,
  superseded_by INTEGER REFERENCES memory_entries(id),
  recorded_at TEXT NOT NULL,
  valid_from TEXT,
  valid_until TEXT,
  source TEXT,
  tags TEXT,
  content_id TEXT
);
CREATE TABLE IF NOT EXISTS entity_nodes (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT UNIQUE NOT NULL,
  kind TEXT
);
CREATE TABLE IF NOT EXISTS entity_edges (
  src INTEGER REFERENCES entity_nodes(id),
  dst INTEGER REFERENCES entity_nodes(id),
  rel TEXT NOT NULL,
  UNIQUE(src,rel,dst)
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  entry_id INTEGER REFERENCES memory_entries(id) ON DELETE CASCADE,
  text TEXT NOT NULL,
  embedding BLOB
);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, content='chunks', content_rowid='id');
CREATE TABLE IF NOT EXISTS provenance (
  entry_id INTEGER PRIMARY KEY REFERENCES memory_entries(id),
  note TEXT
);
CREATE TABLE IF NOT EXISTS coala_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ingest_sources (
  path TEXT PRIMARY KEY,
  sha256 TEXT NOT NULL,
  size INTEGER,
  segments INTEGER NOT NULL,
  key_base TEXT NOT NULL,
  rule TEXT,
  ingested_at TEXT NOT NULL,
  git_commit TEXT
);
CREATE TABLE IF NOT EXISTS entry_entities (
  entry_id INTEGER NOT NULL REFERENCES memory_entries(id) ON DELETE CASCADE,
  entity_id INTEGER NOT NULL REFERENCES entity_nodes(id) ON DELETE CASCADE,
  PRIMARY KEY (entry_id, entity_id)
);
CREATE INDEX IF NOT EXISTS idx_entry_entities_entity ON entry_entities(entity_id);
CREATE INDEX IF NOT EXISTS idx_memory_supersession ON memory_entries(supersession_key, superseded_by);
CREATE INDEX IF NOT EXISTS idx_memory_type ON memory_entries(memory_type);
CREATE INDEX IF NOT EXISTS idx_memory_recorded ON memory_entries(recorded_at);
CREATE INDEX IF NOT EXISTS idx_chunks_entry ON chunks(entry_id);
CREATE INDEX IF NOT EXISTS idx_edges_src ON entity_edges(src);
CREATE INDEX IF NOT EXISTS idx_edges_dst ON entity_edges(dst);
"""

VEC_EXT_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_vec USING vec0(embedding float[256]);
"""

# ------------------------------------------------------------- contrato erros
class CoalaError(Exception):
    """Erro com contrato estável: 'Erro: <o quê> — Solução: <o que fazer>'."""
    exit_code = 1
    label = "Erro"

    def __init__(self, what: str, solution: str):
        super().__init__(f"{self.label}: {what} — Solução: {solution}")
        self.what = what
        self.solution = solution

    def render(self) -> str:
        return f"{self.label}: {self.what} — Solução: {self.solution}"


class UsageError(CoalaError):
    exit_code = 2
    label = "Erro"


class DependencyError(CoalaError):
    exit_code = 3
    label = "Erro"


# ------------------------------------------------------------------- utilitários
def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def est_tokens(text: str) -> int:
    """Estimativa determinística de tokens: ceil(chars / 4), mínimo 1."""
    if not text:
        return 0
    return max(1, (len(text) + TOKEN_CHARS - 1) // TOKEN_CHARS)


def tokenize(text: str) -> list:
    return re.findall(r"[0-9A-Za-zÀ-ÿ_]+", (text or "").lower())


def chunk_text(text: str, max_chars: int = CHUNK_MAX_CHARS) -> list:
    """Divide o conteúdo em chunks por parágrafos, respeitando max_chars."""
    text = (text or "").strip()
    if not text:
        return []
    chunks = []
    current = ""
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        while len(para) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(para[:max_chars])
            para = para[max_chars:].strip()
        if not para:
            continue
        if current and len(current) + len(para) + 2 > max_chars:
            chunks.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks or [text[:max_chars]]


def file_sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def engine_version_of(path: str) -> str:
    """Lê ENGINE_VERSION de um coala.py (sem o importar)."""
    try:
        with open(path, encoding="utf-8") as fh:
            m = re.search(r'^ENGINE_VERSION\s*=\s*"([^"]+)"', fh.read(), re.M)
        return m.group(1) if m else "?"
    except OSError:
        return "?"


def ro_uri(path: str) -> str:
    return "file:" + urllib.parse.quote(os.path.abspath(path)) + "?mode=ro"


# ------------------------------------------------------------ id por conteúdo
def content_id(record: dict) -> str:
    """Id por conteúdo de um registo canónico — o contrato de CID_FIELDS, byte a byte."""
    canon = {f: record.get(f) for f in CID_FIELDS}
    raw = json.dumps(canon, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def record_of_content(content: str):
    """Se o conteúdo é um registo canónico em JSON (objeto com `schema` e `body`), devolve-o; senão None."""
    if not content or not content.lstrip().startswith("{"):
        return None
    try:
        rec = json.loads(content)
    except (ValueError, TypeError):
        return None
    if isinstance(rec, dict) and isinstance(rec.get("schema"), str) and rec["schema"] and "body" in rec:
        return rec
    return None


def entry_content_id(memory_type: str, content: str, key: str = None, origin: str = None,
                     source: str = None, tags: str = None, recorded_at: str = None,
                     valid_from: str = None) -> str:
    """
    Id por conteúdo de um registo do motor:
      - conteúdo = registo canónico em JSON (ex.: sitemem/1) → o `id` dele, se trouxer um; senão o
        contrato sobre os campos dele;
      - texto livre (add/ingest) → o mesmo contrato com schema `coala/entry` e um body com o conteúdo e
        os metadados IMUTÁVEIS (origem, fonte, tags, recorded_at, valid_from) — a mesma identidade do
        `import --from` (tipo, conteúdo, recorded_at, chave), mais o que distingue as versões do ingest.
    """
    rec = record_of_content(content)
    if rec is not None:
        rid = rec.get("id")
        if isinstance(rid, str) and CID_RE.match(rid):
            return rid
        return content_id(rec)
    return content_id({"schema": GENERIC_CID_SCHEMA, "key": key, "type": memory_type,
                       "body": {"content": content, "origin": origin, "source": source, "tags": tags,
                                "recorded_at": recorded_at, "valid_from": valid_from}})


def row_content_id(row) -> str:
    """entry_content_id de uma linha de memory_entries (sqlite3.Row ou dict)."""
    return entry_content_id(row["memory_type"], row["content"], row["supersession_key"],
                            row["origin_class"], row["source"], row["tags"], row["recorded_at"],
                            row["valid_from"])


# --------------------------------------------------- embeddings (fallback local)
def embed_text(text: str, dims: int = EMBED_DIMS) -> list:
    """
    Fallback determinístico SEM dependências externas (documentado em schema.md):
    hashing de unigramas (peso 1.0) + bigramas (peso 0.75) -> vetor float32 de
    `dims` dimensões, normalizado em L2. A semelhança de cosseno em Python
    aproxima sobreposição semântico-léxica; nunca falha por falta da extensão.
    """
    vec = [0.0] * dims
    toks = tokenize(text)
    feats = [(t, 1.0) for t in toks]
    feats += [(f"{a} {b}", 0.75) for a, b in zip(toks, toks[1:])]
    for feat, weight in feats:
        digest = hashlib.sha1(feat.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dims
        vec[idx] += weight
    norm = sum(v * v for v in vec) ** 0.5
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def pack_vec(vec: list) -> bytes:
    return struct.pack(f"<{len(vec)}f", *vec)


def unpack_vec(blob: bytes) -> list:
    n = len(blob) // 4
    return list(struct.unpack(f"<{n}f", blob[: n * 4]))


def cosine(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


# ------------------------------------------------------------- redação segredos
SECRET_PATTERNS = [
    (re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"), "sk-[REDACTADO]"),
    (re.compile(r"\bwhsec_[A-Za-z0-9_\-]{6,}"), "whsec_[REDACTADO]"),
    (re.compile(r"\bcfut_[A-Za-z0-9_\-]{6,}"), "cfut_[REDACTADO]"),
    (re.compile(r"\bgd_pat_[A-Za-z0-9_\-]{10,}"), "gd_pat_[REDACTADO]"),
    (re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}"), "gh*_[REDACTADO]"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "github_pat_[REDACTADO]"),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "AKIA[REDACTADO]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}"), "xox*-[REDACTADO]"),
    (re.compile(r"\bhf_[A-Za-z0-9]{20,}"), "hf_[REDACTADO]"),
    (re.compile(r"\bglpat-[A-Za-z0-9_\-]{16,}"), "glpat-[REDACTADO]"),
    (re.compile(r"\bnpm_[A-Za-z0-9]{30,}"), "npm_[REDACTADO]"),
    (re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"), "jwt-[REDACTADO]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
     "[CHAVE-PRIVADA-REMOVIDA]"),
    (re.compile(r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|token|secret|"
                r"client[_-]?secret|password|passwd)"
                r"(\s*[=:]\s*)[\"']?[A-Za-z0-9_\-/+\.]{12,}[\"']?"),
     lambda m: f"{m.group(1)}{m.group(2)}[REDACTADO]"),
]


def redact(text: str) -> str:
    """Mascara valores com aspeto de segredo. Aplica-se a TODA a saída."""
    if not text:
        return text
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def emit(text: str) -> None:
    """Escreve no stdout aplicando redação e o portão de 48 KB."""
    data = redact(text)
    if not data.endswith("\n"):
        data += "\n"
    raw = data.encode("utf-8")
    if len(raw) > MAX_OUTPUT_BYTES:
        head = raw[: MAX_OUTPUT_BYTES - 256].decode("utf-8", errors="ignore")
        data = (head
                + "\n\n… [TRUNCADO: saída excedeu 48 KB — restringe com --type/--tags/--limit"
                  " ou usa `search`/`recall` com consultas mais específicas]\n")
    sys.stdout.write(data)
    sys.stdout.flush()


# ------------------------------------------------------- resolução da base local
def skill_dir_of_engine(engine_file: str = None):
    """Se o motor está vendorizado em <x>-coala-memory-agent-skill/scripts/, devolve a skill."""
    here = os.path.dirname(os.path.abspath(engine_file or __file__))
    skill = os.path.dirname(here)
    if os.path.basename(here) == "scripts" and is_memory_skill(os.path.basename(skill)):
        return skill
    return None


def skill_dir_of_db(path: str):
    """Se a base está em <x>-coala-memory-agent-skill/memory/, devolve a skill."""
    mem = os.path.dirname(os.path.abspath(path))
    skill = os.path.dirname(mem)
    if os.path.basename(mem) == DB_SUBPATH[0] and is_memory_skill(os.path.basename(skill)):
        return skill
    return None


def project_root_of(skill_dir: str) -> str:
    """<projeto>/.agents/<skill> -> <projeto>."""
    return os.path.dirname(os.path.dirname(os.path.abspath(skill_dir)))


def find_project_skill(start: str = None):
    """Sobe a partir de `start` até encontrar .agents/*-coala-memory-agent-skill/coala.json."""
    d = os.path.abspath(start or os.getcwd())
    while True:
        agents = os.path.join(d, ".agents")
        if os.path.isdir(agents):
            try:
                names = sorted(os.listdir(agents))
            except OSError:
                names = []
            found = [os.path.join(agents, n) for n in names
                     if is_memory_skill(n)
                     and os.path.isfile(os.path.join(agents, n, MANIFEST_NAME))]
            if len(found) > 1:
                raise UsageError(
                    f"há mais do que uma memória CoALA em {agents} "
                    f"({', '.join(os.path.basename(f) for f in found)})",
                    "indica qual usar com --db <skill>/memory/coala.sqlite")
            if found:
                return found[0]
        parent = os.path.dirname(d)
        if parent == d:
            return None
        d = parent


def resolve_db(override: str = None, cwd: str = None, env=None, engine_file: str = None):
    """Devolve (caminho_da_base, como_foi_resolvida, skill_dir|None). Nunca usa base global."""
    env = os.environ if env is None else env
    if override:
        p = os.path.abspath(os.path.expanduser(override))
        return p, "--db", skill_dir_of_db(p)
    if env.get(DB_ENV):
        p = os.path.abspath(os.path.expanduser(env[DB_ENV]))
        return p, f"env {DB_ENV}", skill_dir_of_db(p)
    skill = skill_dir_of_engine(engine_file)
    if skill:
        return os.path.join(skill, *DB_SUBPATH), "motor da skill local", skill
    skill = find_project_skill(cwd)
    if skill:
        return os.path.join(skill, *DB_SUBPATH), "descoberta a partir do diretório atual", skill
    start = os.path.abspath(cwd or os.getcwd())
    raise DependencyError(
        f"nenhuma memória CoALA local encontrada a partir de {start} (procura "
        f".agents/*{SKILL_SUFFIX}/{MANIFEST_NAME}; não existe memória global)",
        f"instala a memória no projeto com `{INSTALL_HINT}` ou indica --db <caminho>")


def db_path(override: str = None) -> str:
    return resolve_db(override)[0]


# -------------------------------------------------------------------- base dados
def harden_perms(path: str) -> None:
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            try:
                os.chmod(p, 0o600)
            except OSError:
                pass


def init_schema(conn: sqlite3.Connection) -> str:
    """Cria/migra o esquema (idempotente, só aditivo). Devolve o nome do backend vetorial."""
    try:
        conn.executescript(SCHEMA_SQL)
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower() or "no such module" in str(exc).lower():
            raise DependencyError(
                f"o SQLite desta máquina não tem FTS5 ({exc})",
                "instala um SQLite compilado com FTS5 (padrão no Python ≥3.9 do sistema) "
                "ou usa outro python3")
        raise CoalaError(f"falha ao criar o esquema ({exc})", "verifica permissões do ficheiro DB")

    # v3 (aditivo): coluna do id por conteúdo nas bases antigas + índice + preenchimento dos que faltam
    if "content_id" not in table_columns(conn, "memory_entries"):
        try:
            conn.execute("ALTER TABLE memory_entries ADD COLUMN content_id TEXT")
        except sqlite3.OperationalError:              # outro processo migrou entre a leitura e o ALTER
            if "content_id" not in table_columns(conn, "memory_entries"):
                raise
    conn.execute("CREATE INDEX IF NOT EXISTS idx_memory_cid ON memory_entries(content_id)")
    backfill_content_ids(conn)

    ver = conn.execute("PRAGMA user_version").fetchone()[0]
    if ver < SCHEMA_VERSION:
        ts = now_iso()
        first = conn.execute("SELECT MIN(recorded_at) FROM memory_entries").fetchone()[0]
        conn.execute("INSERT OR IGNORE INTO coala_meta(key, value) VALUES ('created_at', ?)",
                     (first or ts,))
        conn.execute("INSERT OR REPLACE INTO coala_meta(key, value) VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
        conn.execute("INSERT OR REPLACE INTO coala_meta(key, value) VALUES ('schema_migrated_at', ?)",
                     (ts,))
        conn.execute("INSERT OR REPLACE INTO coala_meta(key, value) VALUES ('engine_version', ?)",
                     (ENGINE_VERSION,))
        conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")
        conn.commit()

    backend = "hashing-256"
    try:  # extensão opcional sqlite-vec; nunca falhar por falta dela
        import sqlite_vec  # type: ignore
        if hasattr(conn, "enable_load_extension"):
            conn.enable_load_extension(True)
        try:
            sqlite_vec.load(conn)  # type: ignore[attr-defined]
        except Exception:
            conn.load_extension("vec0")
        conn.enable_load_extension(False)
        conn.executescript(VEC_EXT_SQL)
        backend = "sqlite-vec"
    except Exception:
        backend = "hashing-256"
    return backend


def backfill_content_ids(conn) -> int:
    """Preenche content_id dos registos que ainda não o têm (base v2 migrada, ou escritos por um motor
    antigo depois da migração). Idempotente; devolve quantos preencheu."""
    rows = conn.execute(   # por posição: vale com ou sem row_factory (o instalador abre bases cruas)
        "SELECT id, memory_type, content, supersession_key, origin_class, source, tags, recorded_at,"
        " valid_from FROM memory_entries WHERE content_id IS NULL").fetchall()
    if not rows:
        return 0
    conn.executemany("UPDATE memory_entries SET content_id=? WHERE id=?",
                     [(entry_content_id(*tuple(r)[1:]), r[0]) for r in rows])
    conn.commit()
    return len(rows)


def table_columns(conn, table: str) -> set:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def open_scratch_copy(path: str):
    """
    Cópia EM MEMÓRIA da base (ou base vazia, se não existe), já com o esquema atual: o --dry-run do
    `import --jsonl` e do `forget` corre o MESMO algoritmo nela e descarta — contagens exatas, disco intocado.
    """
    mem = sqlite3.connect(":memory:")
    if os.path.isfile(path):
        src = sqlite3.connect(ro_uri(path), uri=True)
        try:
            src.backup(mem)
        finally:
            src.close()
    mem.row_factory = sqlite3.Row
    mem.execute("PRAGMA foreign_keys=ON")
    backend = init_schema(mem)
    return mem, backend


def connect_path(path: str):
    """Abre a base num caminho explícito (WAL, FK, busy_timeout) e garante o esquema."""
    parent = os.path.dirname(path)
    if parent and not os.path.isdir(parent):
        os.makedirs(parent, mode=0o700, exist_ok=True)
    try:
        conn = sqlite3.connect(path)
    except sqlite3.Error as exc:
        raise CoalaError(
            f"não foi possível abrir a base de dados em {path} ({exc})",
            "verifica permissões do diretório ou indica --db para outro caminho")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    backend = init_schema(conn)
    harden_perms(path)
    return conn, path, backend


def connect(override: str = None):
    """Resolve a base local (ver docstring do módulo) e abre-a. Devolve (conn, path, backend)."""
    path, _how, _skill = resolve_db(override)
    return connect_path(path)


def open_ro(path: str) -> sqlite3.Connection:
    """Ligação só-de-leitura (nunca escreve, nunca cria)."""
    if not os.path.isfile(path):
        raise CoalaError(f"a base {path} não existe", "confirma o caminho (ou corre `init`)")
    conn = sqlite3.connect(ro_uri(path), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_names(conn) -> set:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


# --------------------------------------------------------------- proveniência
def add_provenance(conn, entry_id: int, note: str) -> None:
    row = conn.execute("SELECT note FROM provenance WHERE entry_id=?", (entry_id,)).fetchone()
    if row and row["note"]:
        conn.execute("UPDATE provenance SET note=? WHERE entry_id=?",
                     (row["note"] + " | " + note, entry_id))
    else:
        conn.execute("INSERT INTO provenance(entry_id, note) VALUES (?,?)", (entry_id, note))


def supersede_entry(conn, old_id: int, new_id: int, ts: str) -> bool:
    """Marca `old_id` como suplantado por `new_id`. Devolve True se fez efeito."""
    row = conn.execute("SELECT superseded_by FROM memory_entries WHERE id=?", (old_id,)).fetchone()
    if row is None or row["superseded_by"] is not None:
        return False
    conn.execute(
        "UPDATE memory_entries SET superseded_by=?, valid_until=COALESCE(valid_until, ?) WHERE id=?",
        (new_id, ts, old_id))
    add_provenance(conn, old_id, f"Suplantado por #{new_id} em {ts}")
    add_provenance(conn, new_id, f"Suplanta #{old_id}")
    return True


def expire_entry(conn, entry_id: int, ts: str, reason: str) -> bool:
    """Fecha o tempo de validade de um registo ativo (sem o apagar nem reescrever)."""
    cur = conn.execute(
        "UPDATE memory_entries SET valid_until=? WHERE id=? AND superseded_by IS NULL"
        " AND (valid_until IS NULL OR valid_until > ?)", (ts, entry_id, ts))
    if cur.rowcount:
        add_provenance(conn, entry_id, f"Expirado em {ts}: {reason}")
        return True
    return False


def index_content(conn, backend: str, entry_id: int, content: str) -> int:
    n = 0
    for ch in chunk_text(content):
        vec = embed_text(ch)
        cur = conn.execute("INSERT INTO chunks(entry_id, text, embedding) VALUES (?,?,?)",
                           (entry_id, ch, pack_vec(vec)))
        conn.execute("INSERT INTO chunks_fts(rowid, text) VALUES (?,?)", (cur.lastrowid, ch))
        if backend == "sqlite-vec":
            try:
                import sqlite_vec  # type: ignore
                conn.execute("INSERT INTO chunks_vec(rowid, embedding) VALUES (?,?)",
                             (cur.lastrowid, sqlite_vec.serialize_float32(vec)))
            except Exception:
                pass
        n += 1
    return n


def insert_entry(conn, backend: str, memory_type: str, content: str, origin: str,
                 key: str = None, source: str = None, tags: str = None,
                 valid_from: str = None, valid_until: str = None):
    """
    Insere um registo + chunks/FTS/vetores + proveniência. Se `key` (chave de
    supersessão) coincidir com registos ativos anteriores, cada um é suplantado
    por este (superseded_by + valid_until + nota de proveniência).
    Devolve (entry_id, [ids suplantados]).
    """
    ts = now_iso()
    cid = entry_content_id(memory_type, content, key, origin, source, tags, ts, valid_from)
    cur = conn.execute(
        "INSERT INTO memory_entries(memory_type, content, origin_class, supersession_key,"
        " recorded_at, valid_from, valid_until, source, tags, content_id)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (memory_type, content, origin, key, ts, valid_from, valid_until, source, tags, cid))
    entry_id = cur.lastrowid
    index_content(conn, backend, entry_id, content)
    add_provenance(conn, entry_id,
                   f"Registado em {ts}; origem={origin}; fonte={source or 'n/a'}")
    superseded = []
    if key:
        for old_id in active_key_conflicts(conn, key, exclude_id=entry_id):
            if supersede_entry(conn, old_id, entry_id, ts):
                superseded.append(old_id)
    return entry_id, superseded


def active_key_conflicts(conn, key: str, exclude_id: int = None) -> list:
    sql = ("SELECT id FROM memory_entries WHERE supersession_key=? AND superseded_by IS NULL")
    params = [key]
    if exclude_id is not None:
        sql += " AND id != ?"
        params.append(exclude_id)
    return [r["id"] for r in conn.execute(sql, params).fetchall()]


def ensure_entities(conn, names: list, kind: str = "conceito") -> list:
    ids = []
    for name in names:
        name = name.strip()
        if not name:
            continue
        row = conn.execute("SELECT id FROM entity_nodes WHERE name=?", (name,)).fetchone()
        if row:
            ids.append(row["id"])
        else:
            cur = conn.execute("INSERT INTO entity_nodes(name, kind) VALUES (?,?)", (name, kind))
            ids.append(cur.lastrowid)
    return ids


def link_entities(conn, src_id: int, dst_id: int, rel: str) -> bool:
    try:
        conn.execute("INSERT INTO entity_edges(src, dst, rel) VALUES (?,?,?)",
                     (src_id, dst_id, rel))
        return True
    except sqlite3.IntegrityError:
        return False


def link_entry_entities(conn, entry_id: int, entity_ids: list) -> None:
    """Liga o registo às entidades que ele cita (é por aqui que o `forget` sabe que nós do grafo lhe pertencem)."""
    conn.executemany("INSERT OR IGNORE INTO entry_entities(entry_id, entity_id) VALUES (?,?)",
                     [(entry_id, e) for e in entity_ids])


def entry_entity_names(conn) -> dict:
    """{entry_id: [nomes das entidades ligadas, ordenados]} (vazio numa base sem a tabela)."""
    if "entry_entities" not in table_names(conn):
        return {}
    out = {}
    for r in conn.execute("SELECT l.entry_id AS eid, n.name AS name FROM entry_entities l"
                          " JOIN entity_nodes n ON n.id = l.entity_id ORDER BY l.entry_id, n.name"):
        out.setdefault(r["eid"], []).append(r["name"])
    return out


# ------------------------------------------------------------------ filtros/busca
def like_escape(text: str) -> str:
    """Escapa os curingas do LIKE (`%`, `_`) e o próprio escape: a tag casa só literalmente."""
    return (text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2)
                .replace("%", LIKE_ESCAPE + "%").replace("_", LIKE_ESCAPE + "_"))


def tag_clause(col: str = "e.tags") -> str:
    """Condição SQL 'a lista CSV de tags em <col> contém esta tag' (par de tag_param)."""
    return f"(',' || LOWER(COALESCE({col},'')) || ',') LIKE ? ESCAPE '{LIKE_ESCAPE}'"


def tag_param(tag: str) -> str:
    return f"%,{like_escape(tag.strip().lower())},%"


def build_filter(mtype: str = None, tags: list = None, include_superseded: bool = False,
                 include_expired: bool = False, now: str = None, any_tags: list = None):
    """`tags` = TODAS têm de estar presentes (AND); `any_tags` = basta UMA (OR). Tags casam literalmente."""
    conds = []
    params = []
    if not include_superseded:
        conds.append("e.superseded_by IS NULL")
    if not include_expired:
        conds.append("(e.valid_until IS NULL OR e.valid_until > ?)")
        params.append(now or now_iso())
    if mtype:
        conds.append("e.memory_type = ?")
        params.append(mtype)
    for tag in (tags or []):
        conds.append(tag_clause())
        params.append(tag_param(tag))
    anyt = [t.strip().lower() for t in (any_tags or []) if t.strip()]
    if anyt:
        conds.append("(" + " OR ".join(tag_clause() for _ in anyt) + ")")
        params += [tag_param(t) for t in anyt]
    where = (" AND " + " AND ".join(conds)) if conds else ""
    return where, params


def build_fts_query(qtext: str) -> str:
    toks = tokenize(qtext)
    if not toks:
        raise UsageError(f"a consulta {qtext!r} não tem termos pesquisáveis",
                         "usa palavras com letras ou números (ex.: `search \"deploy cloudflare\"`)")
    return " OR ".join('"' + t.replace('"', '""') + '"' for t in toks)


def fts_search(conn, qtext: str, where: str, params: list, limit: int = 100) -> list:
    """
    Ranking léxico BM25 (mais baixo = melhor). Devolve [(eid, bm25)].
    Nota: bm25() só pode ser usado onde o FTS5 está no FROM direto, por isso a
    agregação por entrada (melhor chunk) é feita fora do SQL.
    """
    match = build_fts_query(qtext)
    sql = ("SELECT c.entry_id AS eid, bm25(chunks_fts) AS s"
           " FROM chunks_fts"
           " JOIN chunks c ON c.id = chunks_fts.rowid"
           " JOIN memory_entries e ON e.id = c.entry_id"
           " WHERE chunks_fts MATCH ? " + where +
           " ORDER BY s ASC LIMIT ?")
    try:
        rows = conn.execute(sql, [match] + list(params) + [max(limit * 4, 200)]).fetchall()
    except sqlite3.OperationalError as exc:
        raise CoalaError(f"consulta FTS5 inválida ({exc})", "simplifica a consulta (palavras soltas)")
    best = {}
    for r in rows:
        eid, s = r["eid"], r["s"]
        if eid not in best or s < best[eid]:
            best[eid] = s
    return sorted(best.items(), key=lambda kv: (kv[1], kv[0]))[:limit]


def vector_search(conn, qtext: str, where: str, params: list, limit: int = 100) -> list:
    """Ranking vetorial por cosseno (maior = melhor). Devolve [(eid, sim)]."""
    qvec = embed_text(qtext)
    sql = ("SELECT c.entry_id AS eid, c.embedding AS emb FROM chunks c"
           " JOIN memory_entries e ON e.id = c.entry_id"
           " WHERE c.embedding IS NOT NULL" + where)
    best = {}
    for row in conn.execute(sql, list(params)).fetchall():
        if not row["emb"]:
            continue
        sim = cosine(qvec, unpack_vec(row["emb"]))
        eid = row["eid"]
        if eid not in best or sim > best[eid]:
            best[eid] = sim
    ranked = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
    return ranked[:limit]


def hybrid_search(conn, qtext: str, where: str, params: list, limit: int = 10,
                  w_fts: float = 1.0, w_vec: float = 1.0, k: int = RRF_K) -> list:
    """
    Fusão Reciprocal Rank Fusion em SQL com funções de janela:
        score(d) = Σ_m  w_m / (k + r_m(d)),  k = 60
    onde r_m(d) é o posto do documento d no método m (1-based).
    """
    fts = fts_search(conn, qtext, where, params, limit=max(limit * 10, 100))
    vec = vector_search(conn, qtext, where, params, limit=max(limit * 10, 100))

    conn.execute("DROP TABLE IF EXISTS temp.fts_scores")
    conn.execute("DROP TABLE IF EXISTS temp.vec_scores")
    conn.execute("CREATE TEMP TABLE fts_scores(eid INTEGER PRIMARY KEY, s REAL)")
    conn.execute("CREATE TEMP TABLE vec_scores(eid INTEGER PRIMARY KEY, sim REAL)")
    conn.executemany("INSERT OR REPLACE INTO fts_scores(eid, s) VALUES (?,?)", fts)
    conn.executemany("INSERT OR REPLACE INTO vec_scores(eid, sim) VALUES (?,?)", vec)

    sql = """
    WITH fts_ranked AS (
        SELECT eid, ROW_NUMBER() OVER (ORDER BY s ASC) AS r FROM fts_scores
    ),
    vec_ranked AS (
        SELECT eid, ROW_NUMBER() OVER (ORDER BY sim DESC) AS r FROM vec_scores
    ),
    all_ranks AS (
        SELECT eid, r, 'fts' AS m FROM fts_ranked
        UNION ALL
        SELECT eid, r, 'vec' AS m FROM vec_ranked
    )
    SELECT a.eid AS eid,
           SUM(CASE a.m WHEN 'fts' THEN :wf ELSE :wv END / (:k + a.r)) AS rrf,
           MIN(CASE a.m WHEN 'fts' THEN a.r END) AS fts_rank,
           MIN(CASE a.m WHEN 'vec' THEN a.r END) AS vec_rank
    FROM all_ranks a
    GROUP BY a.eid
    ORDER BY rrf DESC, eid ASC
    LIMIT :lim
    """
    rows = conn.execute(sql, {"wf": w_fts, "wv": w_vec, "k": k, "lim": limit}).fetchall()
    sim_map = dict(vec)
    score_map = dict(fts)
    out = []
    for r in rows:
        out.append({
            "eid": r["eid"],
            "rrf": r["rrf"],
            "fts_rank": r["fts_rank"],
            "vec_rank": r["vec_rank"],
            "fts_score": score_map.get(r["eid"]),
            "vec_sim": sim_map.get(r["eid"]),
        })
    return out


def recency_score(recorded_at: str, now: str = None) -> float:
    try:
        t0 = datetime.fromisoformat(recorded_at)
        t1 = datetime.fromisoformat(now or now_iso())
        days = max(0.0, (t1 - t0).total_seconds() / 86400.0)
    except Exception:
        return 0.5
    return 1.0 / (1.0 + days / 30.0)


def budgeted_selection(order: list, rows: dict, scores: dict, budget: int,
                       top: int, has_query: bool, header_overhead: int = 40):
    """
    Working memory: seleção gulosa de excertos por relevância+recência dentro do
    orçamento de tokens estimados. Entra quem couber; um excerto grande nunca
    bloqueia os seguintes (usa-se `continue`, não `break`).
    Devolve [(combinado, recência, relevância, row), ...].
    """
    max_score = max(scores.values()) if scores else 1.0
    chosen, used = [], 0
    for eid in order:
        r = rows.get(eid)
        if r is None:
            continue
        rel = (scores.get(eid, 0.0) / max_score) if (has_query and max_score) else 0.5
        rec = recency_score(r["recorded_at"])
        combined = 0.7 * rel + 0.3 * rec
        cost = est_tokens(r["content"]) + header_overhead
        if used + cost > budget:
            continue
        used += cost
        chosen.append((combined, rec, rel, r))
        if len(chosen) >= top:
            break
    return chosen


# ---------------------------------------------------------------- apresentação
def fmt_validity(row, now: str = None) -> str:
    now = now or now_iso()
    if row["superseded_by"]:
        return f"SUPERADO por #{row['superseded_by']}"
    if row["valid_until"]:
        if row["valid_until"] <= now:
            return f"EXPIRADO ({row['valid_until']})"
        return f"válido até {row['valid_until']}"
    if row["valid_from"]:
        return f"válido desde {row['valid_from']}"
    return "válido"


def entry_lines(row, extra: str = None) -> str:
    tags = row["tags"] or ""
    head = (f"── #{row['id']} · {row['memory_type']} · origem:{row['origin_class']}"
            f" · {fmt_validity(row)}")
    if row["source"]:
        head += f" · fonte: {row['source']}"
    if tags:
        head += f" · tags: {tags}"
    if extra:
        head += f"\n   {extra}"
    return head + "\n" + row["content"].strip()


# ------------------------------------------------------------ ingestão (material)
INGEST_MODES = ("markdown", "text", "whole", "pdf")
DEFAULT_EXCLUDES = [".git/**", "**/.git/**", "node_modules/**", "**/node_modules/**",
                    ".agents/**", ".claude/**", "dist/**", "build/**", ".next/**", "out/**",
                    "**/__pycache__/**"]
TEXT_MAX_BYTES = 2 * 1024 * 1024
MAX_SEG_CHARS = CHUNK_MAX_CHARS   # segmentos de material alinhados com os chunks


def split_paragraphs(text: str) -> list:
    return [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]


def pack(paras: list, max_chars: int = MAX_SEG_CHARS, repeat_head: str = "") -> list:
    """Empacota parágrafos em segmentos ≤ max_chars; parte parágrafos gigantes."""
    segs, cur = [], repeat_head
    for p in paras:
        while len(p) > max_chars:                      # parágrafo maior que o segmento
            if cur.strip():
                segs.append(cur.strip())
                cur = repeat_head
            cut = p.rfind(" ", 0, max_chars)
            cut = cut if cut > 0 else max_chars
            segs.append((repeat_head + p[:cut]).strip())
            p = p[cut:].strip()
        if len(cur) + len(p) + 2 > max_chars and cur.strip():
            segs.append(cur.strip())
            cur = repeat_head
        cur += p + "\n\n"
    if cur.strip():
        segs.append(cur.strip())
    return segs


def segments_markdown(text: str, head_prefix: str = "") -> list:
    """Segmenta markdown por cabeçalhos, mantendo o cabeçalho em cada segmento."""
    lines = text.splitlines()
    blocks, head, buf = [], None, []
    for ln in lines:
        if re.match(r"^#{1,4}\s", ln):
            blocks.append((head, "\n".join(buf).strip()))
            head, buf = ln.strip(), []
        else:
            buf.append(ln)
    blocks.append((head, "\n".join(buf).strip()))
    segs = []
    for head, body in blocks:
        if not body and not head:
            continue
        repeat = (head + "\n\n") if head else head_prefix
        content_head = (head_prefix + "\n\n" if head_prefix else "")
        for s in pack(split_paragraphs(body), repeat_head=repeat):
            segs.append((content_head + s).strip())
    return segs


def segments_pdf(pdf_path: str, rel: str, max_pages: int = 0, warn=None) -> list:
    """Extrai texto (pdftotext) e segmenta por páginas, com marcador de página."""
    cmd = ["pdftotext", "-q", "-enc", "UTF-8", pdf_path, "-"]
    if max_pages:
        cmd[2:2] = ["-f", "1", "-l", str(max_pages)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as exc:
        if warn:
            warn(f"{rel}: pdftotext falhou ({exc}) — ignorado")
        return None
    if out.returncode != 0:
        if warn:
            warn(f"{rel}: pdftotext saiu com {out.returncode} — ignorado")
        return None
    segs = []
    for pageno, page in enumerate(out.stdout.split("\f"), start=1):
        paras = split_paragraphs(page)
        if not paras:
            continue
        for s in pack(paras, repeat_head=f"[{rel} · pág. {pageno}]\n\n"):
            segs.append(s)
    return segs


def read_text(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except UnicodeDecodeError:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()


def load_ingest_config(path: str) -> dict:
    """Lê e valida o ingest.json (fontes de material do projeto)."""
    try:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
    except OSError as exc:
        raise CoalaError(f"não foi possível ler {path} ({exc})", "confirma o caminho do ingest.json")
    except json.JSONDecodeError as exc:
        raise CoalaError(f"{path} não é JSON válido (linha {exc.lineno}: {exc.msg})",
                         "corrige o ficheiro (ver references/instalar.md da coala-agent-skill)")
    if not isinstance(cfg, dict) or not isinstance(cfg.get("rules"), list):
        raise CoalaError(f"{path} não tem a lista `rules`", "define `rules: [{name, include, mode, type, origin, tags}]`")
    cfg.setdefault("key_prefix", "proj")
    cfg.setdefault("path_prefix", "")
    excludes = list(DEFAULT_EXCLUDES)
    for ex in cfg.get("exclude") or []:
        if ex not in excludes:
            excludes.append(ex)
    cfg["_exclude"] = excludes
    names = set()
    for i, rule in enumerate(cfg["rules"]):
        where = f"{path}: rules[{i}]"
        if not isinstance(rule, dict):
            raise CoalaError(f"{where} não é um objeto", "cada regra é {name, include, mode, type, origin, tags}")
        name = rule.get("name")
        if not name or name in names:
            raise CoalaError(f"{where}: `name` em falta ou repetido ({name!r})", "dá um nome único a cada regra")
        names.add(name)
        inc = rule.get("include")
        if isinstance(inc, str):
            inc = [inc]
        if not inc or not all(isinstance(x, str) and x for x in inc):
            raise CoalaError(f"{where}: `include` vazio", "lista padrões glob relativos à raiz (ex.: docs/**/*.md)")
        rule["include"] = inc
        rule.setdefault("mode", "markdown")
        rule.setdefault("type", "semantic")
        rule.setdefault("origin", "agent")
        rule.setdefault("tags", "")
        if rule["mode"] not in INGEST_MODES:
            raise CoalaError(f"{where}: mode {rule['mode']!r} inválido", f"usa um de {', '.join(INGEST_MODES)}")
        if rule["type"] not in MEMORY_TYPES:
            raise CoalaError(f"{where}: type {rule['type']!r} inválido", f"usa um de {', '.join(MEMORY_TYPES)}")
        if rule["origin"] not in ORIGINS:
            raise CoalaError(f"{where}: origin {rule['origin']!r} inválido", f"usa um de {', '.join(ORIGINS)}")
    graph = cfg.get("graph") or {}
    for e in graph.get("edges") or []:
        if not (isinstance(e, list) and len(e) == 3 and all(isinstance(x, str) and x for x in e)):
            raise CoalaError(f"{path}: aresta inválida {e!r}", "usa [origem, relação, destino]")
    return cfg


def expand_sources(cfg: dict, root: str) -> list:
    """Atribui cada ficheiro à PRIMEIRA regra que o apanha. Devolve [(relpath, regra)] ordenado."""
    assigned, order = {}, []
    for rule in cfg["rules"]:
        for pat in rule["include"]:
            for p in sorted(glob.glob(os.path.join(root, pat), recursive=True)):
                if not os.path.isfile(p):
                    continue
                rel = os.path.relpath(p, root).replace(os.sep, "/")
                if rel.startswith("../") or rel in assigned:
                    continue
                if any(fnmatch.fnmatch(rel, ex) for ex in cfg["_exclude"]):
                    continue
                assigned[rel] = rule
                order.append(rel)
    return [(rel, assigned[rel]) for rel in order]


def logical_path(cfg: dict, relpath: str) -> str:
    prefix = (cfg.get("path_prefix") or "").strip("/")
    return f"{prefix}/{relpath}" if prefix else relpath


def rule_tags(rule: dict, relpath: str) -> str:
    base = os.path.basename(relpath)
    stem = os.path.splitext(base)[0]
    parent = os.path.basename(os.path.dirname(relpath))
    tags = rule.get("tags") or ""
    for k, v in (("{stem}", stem), ("{name}", base), ("{dir}", parent), ("{rule}", rule["name"])):
        tags = tags.replace(k, v)
    return tags or None


def segment_file(abs_path: str, logical: str, rule: dict, pdf_pages: int = 0, warn=None):
    """Devolve a lista de segmentos, ou None se o ficheiro foi ignorado (aviso emitido)."""
    mode = rule["mode"]
    if mode == "pdf":
        if not shutil.which("pdftotext"):
            if warn:
                warn(f"{logical}: pdftotext ausente — regra `{rule['name']}` ignorada (instala poppler)")
            return None
        return segments_pdf(abs_path, logical, max_pages=pdf_pages, warn=warn)
    limit = int(rule.get("max_bytes") or TEXT_MAX_BYTES)
    if os.path.getsize(abs_path) > limit:
        if warn:
            warn(f"{logical}: {os.path.getsize(abs_path)} bytes > max_bytes {limit} — ignorado")
        return None
    text = read_text(abs_path)
    if mode == "markdown":
        return segments_markdown(text)
    if mode == "text":
        return pack(split_paragraphs(text), repeat_head=f"[{logical}]\n\n")
    return [f"[{logical}]\n\n```\n{text}\n```"]          # whole


def segment_keys(key_base: str, mode: str, n: int) -> list:
    if mode == "whole":
        return [key_base][:n]
    width = 4 if mode == "pdf" else 3
    return [f"{key_base}#{i:0{width}d}" for i in range(n)]


def git_head(root: str):
    try:
        out = subprocess.run(["git", "-C", root, "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def live_entries_for_base(conn, key_base: str, now: str) -> list:
    return conn.execute(
        "SELECT id, supersession_key AS k FROM memory_entries WHERE superseded_by IS NULL"
        " AND (valid_until IS NULL OR valid_until > ?)"
        " AND (supersession_key = ? OR substr(supersession_key, 1, ?) = ?)",
        (now, key_base, len(key_base) + 1, key_base + "#")).fetchall()


def apply_graph(conn, graph: dict):
    """Entidades/arestas declaradas no ingest.json (idempotente)."""
    if not graph:
        return 0, 0
    new_e = new_x = 0
    for ent in graph.get("entities") or []:
        name = (ent.get("name") or "").strip() if isinstance(ent, dict) else str(ent).strip()
        if not name:
            continue
        kind = ent.get("kind") if isinstance(ent, dict) else None
        if conn.execute("SELECT 1 FROM entity_nodes WHERE name=?", (name,)).fetchone() is None:
            conn.execute("INSERT INTO entity_nodes(name, kind) VALUES (?,?)", (name, kind or "conceito"))
            new_e += 1
    for src, rel, dst in graph.get("edges") or []:
        before = conn.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0]
        a, b = ensure_entities(conn, [src, dst])
        new_e += conn.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0] - before
        if link_entities(conn, a, b, rel):
            new_x += 1
    return new_e, new_x


def run_ingest(conn, backend, cfg: dict, root: str, only: list = None, pdf_pages: int = 0,
               dry_run: bool = False, verbose: bool = False) -> dict:
    """
    Ingestão idempotente guiada pelo ingest.json:
      conteúdo e metadados iguais = NO-OP · conteúdo ou tipo/origem/tags/fonte mudaram =
      nova versão por supersessão (mesma chave) ·
      segmento/ficheiro desapareceu = o registo EXPIRA (valid_until), nunca é apagado.
    """
    now = now_iso()
    warnings, details = [], []
    warn = warnings.append
    rule_names = [r["name"] for r in cfg["rules"]]
    if only:
        bad = [n for n in only if n not in rule_names]
        if bad:
            raise UsageError(f"regra(s) desconhecida(s): {', '.join(bad)}",
                             f"usa --only com {', '.join(rule_names)}")
    assignment = expand_sources(cfg, root)
    assigned_paths = {p for p, _ in assignment}
    selected = [(p, r) for p, r in assignment if not only or r["name"] in only]
    has_sources = "ingest_sources" in table_names(conn)
    head = git_head(root)
    stats = {n: dict(files=0, segments=0, new=0, same=0, updated=0, expired=0, skipped=0)
             for n in rule_names}

    for relpath, rule in selected:
        st = stats[rule["name"]]
        abs_path = os.path.join(root, relpath)
        logical = logical_path(cfg, relpath)
        key_base = f"{cfg['key_prefix']}/{logical}"
        segs = segment_file(abs_path, logical, rule, pdf_pages=pdf_pages, warn=warn)
        if segs is None:
            st["skipped"] += 1
            continue
        st["files"] += 1
        st["segments"] += len(segs)
        tags = rule_tags(rule, relpath)
        keys = segment_keys(key_base, rule["mode"], len(segs))
        f_new = f_upd = f_same = f_exp = 0
        for key, content in zip(keys, segs):
            row = conn.execute(
                "SELECT id, content, valid_until, memory_type, origin_class, tags, source"
                " FROM memory_entries WHERE supersession_key=? AND superseded_by IS NULL"
                " ORDER BY id DESC LIMIT 1", (key,)).fetchone()
            live = row is not None and (row["valid_until"] is None or row["valid_until"] > now)
            same_meta = row is not None and (row["memory_type"], row["origin_class"], row["tags"] or None,
                                             row["source"]) == (rule["type"], rule["origin"], tags, logical)
            if live and row["content"] == content and same_meta:
                f_same += 1
                continue
            if dry_run:
                if row is None:
                    f_new += 1
                else:
                    f_upd += 1
                continue
            entry_id, sup = insert_entry(conn, backend, rule["type"], content, rule["origin"],
                                         key=key, source=logical, tags=tags)
            add_provenance(conn, entry_id, f"Ingerido de {logical} (regra {rule['name']}) em {now}")
            if sup:
                f_upd += 1
            else:
                f_new += 1
        keyset = set(keys)
        for r in live_entries_for_base(conn, key_base, now):
            if r["k"] in keyset:
                continue
            f_exp += 1
            if not dry_run:
                expire_entry(conn, r["id"], now, f"segmento já não existe em {logical}")
        st["new"] += f_new
        st["updated"] += f_upd
        st["same"] += f_same
        st["expired"] += f_exp
        if verbose:
            details.append(f"  · {logical}: {len(segs)} seg · novos={f_new} iguais={f_same}"
                           f" atualizados={f_upd} expirados={f_exp}")
        if not dry_run:
            sha = file_sha256(abs_path)
            size = os.path.getsize(abs_path)
            prev = conn.execute("SELECT * FROM ingest_sources WHERE path=?", (relpath,)).fetchone()
            if prev is None or (prev["sha256"], prev["segments"], prev["key_base"], prev["rule"]) != (
                    sha, len(segs), key_base, rule["name"]):
                conn.execute(
                    "INSERT OR REPLACE INTO ingest_sources(path, sha256, size, segments, key_base,"
                    " rule, ingested_at, git_commit) VALUES (?,?,?,?,?,?,?,?)",
                    (relpath, sha, size, len(segs), key_base, rule["name"], now, head))
            conn.commit()

    removed = []
    if has_sources:
        rows = conn.execute("SELECT * FROM ingest_sources WHERE segments > 0").fetchall()
        for r in rows:
            if r["path"] in assigned_paths:
                continue
            if only and r["rule"] not in only:
                continue
            n_exp = 0
            for e in live_entries_for_base(conn, r["key_base"], now):
                n_exp += 1
                if not dry_run:
                    expire_entry(conn, e["id"], now, f"fonte {r['path']} removida/excluída")
            removed.append({"path": r["path"], "expired": n_exp})
            if r["rule"] in stats:
                stats[r["rule"]]["expired"] += n_exp
            if not dry_run:
                conn.execute("UPDATE ingest_sources SET segments=0, sha256='(removido)', ingested_at=?"
                             " WHERE path=?", (now, r["path"]))
        if not dry_run:
            conn.commit()

    ent_new = edge_new = 0
    if not dry_run and not only:
        ent_new, edge_new = apply_graph(conn, cfg.get("graph"))
        conn.commit()
    totals = {k: sum(s[k] for s in stats.values())
              for k in ("files", "segments", "new", "same", "updated", "expired", "skipped")}
    return {"root": root, "dry_run": dry_run, "rules": stats, "removed": removed,
            "graph": {"entities_new": ent_new, "edges_new": edge_new},
            "totals": totals, "warnings": warnings, "details": details, "git_commit": head}


def freshness_report(conn, cfg: dict, root: str) -> dict:
    """Compara os ficheiros atuais com a última ingestão (sha256 por ficheiro)."""
    assignment = expand_sources(cfg, root)
    assigned = {p for p, _ in assignment}
    rows = {}
    if "ingest_sources" in table_names(conn):
        rows = {r["path"]: r for r in conn.execute("SELECT * FROM ingest_sources").fetchall()}
    fresh, stale, new, removed = [], [], [], []
    for p, _rule in assignment:
        row = rows.get(p)
        if row is None or row["segments"] == 0:
            new.append(p)
        elif file_sha256(os.path.join(root, p)) != row["sha256"]:
            stale.append(p)
        else:
            fresh.append(p)
    for p, row in rows.items():
        if p not in assigned and row["segments"] > 0:
            removed.append(p)
    return {"fresh": fresh, "stale": stale, "new": new, "removed": removed}


# ------------------------------------------------------------------------ doctor
def _mode(path: str):
    try:
        return stat.S_IMODE(os.stat(path).st_mode)
    except OSError:
        return None


def doctor_report(path: str, how: str, skill: str = None, deep: bool = False,
                  freshness: bool = True) -> list:
    checks = []

    def add(level, name, detail, solution=None):
        checks.append({"level": level, "check": name, "detail": str(detail), "solution": solution})

    ok_py = sys.version_info >= (3, 8)
    add("OK" if ok_py else "FAIL", "python3", ".".join(map(str, sys.version_info[:3])),
        None if ok_py else "usa Python ≥ 3.8")
    add("OK", "sqlite", sqlite3.sqlite_version)
    try:
        m = sqlite3.connect(":memory:")
        m.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        m.close()
        add("OK", "fts5", "disponível")
    except sqlite3.Error as exc:
        add("FAIL", "fts5", f"indisponível ({exc})", "usa um python3/SQLite com FTS5")
    try:
        import sqlite_vec  # type: ignore  # noqa: F401
        add("OK", "sqlite-vec", "disponível (kNN nativo)")
    except Exception:
        add("INFO", "sqlite-vec", "ausente — fallback vetorial hashing-256 (degradação graciosa)")
    pdft = shutil.which("pdftotext")
    add("OK" if pdft else "INFO", "pdftotext",
        pdft or "ausente — regras `pdf` do ingest são ignoradas (instala poppler)")
    add("INFO", "base", f"{path} ({how})")
    if skill:
        add("INFO", "skill local", skill)
    if not os.path.isfile(path):
        add("FAIL", "base existe", f"{path} não existe",
            "corre `coala.py init` ou reinstala com o instalador")
        return checks

    parent = os.path.dirname(path)
    dm = _mode(parent)
    ok = dm is not None and dm & 0o077 == 0
    add("OK" if ok else "WARN", "permissões da pasta", oct(dm) if dm is not None else "?",
        None if ok else f"chmod 700 {parent}")
    for suffix in ("", "-wal", "-shm"):
        p = path + suffix
        if os.path.exists(p):
            fm = _mode(p)
            ok = fm is not None and fm & 0o077 == 0
            add("OK" if ok else "WARN", f"permissões {os.path.basename(p)}", oct(fm),
                None if ok else f"chmod 600 {p}")

    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        jm = conn.execute("PRAGMA journal_mode").fetchone()[0]
        add("OK" if jm.lower() == "wal" else "WARN", "journal_mode", jm,
            None if jm.lower() == "wal" else "abre a base com o motor (ativa WAL)")
        chk = conn.execute("PRAGMA integrity_check" if deep else "PRAGMA quick_check").fetchone()[0]
        add("OK" if chk == "ok" else "FAIL",
            "integridade (" + ("integrity_check" if deep else "quick_check") + ")", chk,
            None if chk == "ok" else "restaura um backup: `coala.py restore --from <ficheiro> --yes`")
        tables = table_names(conn)
        missing = [t for t in REQUIRED_TABLES if t not in tables]
        add("OK" if not missing else "FAIL", "esquema",
            "tabelas completas" if not missing else "faltam: " + ", ".join(missing),
            None if not missing else "corre `coala.py init` (aditivo, idempotente)")
        if "memory_entries" not in tables or "chunks" not in tables:
            return checks
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
        add("OK" if uv == SCHEMA_VERSION else "WARN", "versão do esquema",
            f"v{uv} (motor v{ENGINE_VERSION} espera v{SCHEMA_VERSION})",
            None if uv == SCHEMA_VERSION else "corre `coala.py init` para migrar (aditivo)")

        def q1(sql, *a):
            return conn.execute(sql, a).fetchone()[0]

        try:
            conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('integrity-check')")
            add("OK", "índice FTS5", "integrity-check ok")
        except sqlite3.Error as exc:
            add("FAIL", "índice FTS5", str(exc),
                "reconstrói com `INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')` ou restaura um backup")
        n_chunks = q1("SELECT COUNT(*) FROM chunks")
        n_doc = q1("SELECT COUNT(*) FROM chunks_fts_docsize") if "chunks_fts_docsize" in tables else n_chunks
        add("OK" if n_doc == n_chunks else "WARN", "chunks ↔ FTS5", f"{n_chunks} chunks · {n_doc} indexados",
            None if n_doc == n_chunks else "reconstrói o índice (`rebuild`)")
        orphans = q1("SELECT COUNT(*) FROM chunks c LEFT JOIN memory_entries e ON e.id=c.entry_id"
                     " WHERE e.id IS NULL")
        add("OK" if orphans == 0 else "FAIL", "chunks órfãos", orphans,
            None if orphans == 0 else "restaura um backup (não apagues à mão)")
        nochunk = q1("SELECT COUNT(*) FROM memory_entries e WHERE NOT EXISTS"
                     " (SELECT 1 FROM chunks c WHERE c.entry_id=e.id)")
        add("OK" if nochunk == 0 else "WARN", "registos sem chunks", nochunk,
            None if nochunk == 0 else "esses registos não aparecem na busca — reinsere-os com `supersede`")
        dangling = q1("SELECT COUNT(*) FROM memory_entries e WHERE e.superseded_by IS NOT NULL"
                      " AND NOT EXISTS (SELECT 1 FROM memory_entries x WHERE x.id=e.superseded_by)")
        add("OK" if dangling == 0 else "FAIL", "cadeia de supersessão", "íntegra" if dangling == 0
            else f"{dangling} apontam para ids inexistentes",
            None if dangling == 0 else "restaura um backup")
        dups = conn.execute(
            "SELECT supersession_key AS k, COUNT(*) AS n FROM memory_entries WHERE superseded_by IS NULL"
            " AND supersession_key IS NOT NULL GROUP BY 1 HAVING n > 1 LIMIT 5").fetchall()
        add("OK" if not dups else "FAIL", "uma versão ativa por chave",
            "ok" if not dups else ", ".join(f"{r['k']}×{r['n']}" for r in dups),
            None if not dups else "suplanta as versões a mais com `supersede <id>`")
        noprov = q1("SELECT COUNT(*) FROM memory_entries e WHERE NOT EXISTS"
                    " (SELECT 1 FROM provenance p WHERE p.entry_id=e.id)")
        add("OK" if noprov == 0 else "WARN", "proveniência", "todos os registos têm nota" if noprov == 0
            else f"{noprov} registos sem nota")
        if "content_id" in table_columns(conn, "memory_entries"):
            nocid = q1("SELECT COUNT(*) FROM memory_entries WHERE content_id IS NULL")
            add("OK" if nocid == 0 else "WARN", "ids por conteúdo",
                "todos os registos têm id" if nocid == 0 else f"{nocid} registos sem id por conteúdo",
                None if nocid == 0 else "corre `coala.py init` (preenche os ids; idempotente)")
        if "entry_entities" in tables:
            bad_links = q1("SELECT COUNT(*) FROM entry_entities l WHERE NOT EXISTS"
                           " (SELECT 1 FROM memory_entries e WHERE e.id=l.entry_id)"
                           " OR NOT EXISTS (SELECT 1 FROM entity_nodes n WHERE n.id=l.entity_id)")
            add("OK" if bad_links == 0 else "FAIL", "ligações registo↔entidade",
                "íntegras" if bad_links == 0 else f"{bad_links} apontam para registos/entidades inexistentes",
                None if bad_links == 0 else "restaura um backup (não apagues à mão)")
        if {"entity_edges", "entity_nodes"} <= tables:
            bad_edges = q1("SELECT COUNT(*) FROM entity_edges x WHERE NOT EXISTS"
                           " (SELECT 1 FROM entity_nodes n WHERE n.id=x.src)"
                           " OR NOT EXISTS (SELECT 1 FROM entity_nodes n WHERE n.id=x.dst)")
            add("OK" if bad_edges == 0 else "FAIL", "arestas do grafo",
                "íntegras" if bad_edges == 0 else f"{bad_edges} apontam para entidades inexistentes",
                None if bad_edges == 0 else "restaura um backup (não apagues à mão)")
        now = now_iso()
        total = q1("SELECT COUNT(*) FROM memory_entries")
        sup = q1("SELECT COUNT(*) FROM memory_entries WHERE superseded_by IS NOT NULL")
        exp = q1("SELECT COUNT(*) FROM memory_entries WHERE superseded_by IS NULL"
                 " AND valid_until IS NOT NULL AND valid_until <= ?", now)
        by_type = ", ".join(f"{r[0]}={r[1]}" for r in conn.execute(
            "SELECT memory_type, COUNT(*) FROM memory_entries GROUP BY 1 ORDER BY 1"))
        by_origin = ", ".join(f"{r[0]}={r[1]}" for r in conn.execute(
            "SELECT origin_class, COUNT(*) FROM memory_entries GROUP BY 1 ORDER BY 1"))
        add("INFO", "contagens", f"{total} registos ({total - sup - exp} ativos · {sup} suplantados ·"
            f" {exp} expirados) · tipos: {by_type or '—'} · origens: {by_origin or '—'} ·"
            f" entidades {q1('SELECT COUNT(*) FROM entity_nodes')} ·"
            f" arestas {q1('SELECT COUNT(*) FROM entity_edges')}")

        if freshness and skill:
            cfgp = os.path.join(skill, INGEST_CONFIG_NAME)
            if not os.path.isfile(cfgp):
                add("INFO", "frescura", "sem ingest.json — nada a comparar")
            else:
                try:
                    cfg = load_ingest_config(cfgp)
                    fr = freshness_report(conn, cfg, project_root_of(skill))
                    detail = (f"{len(fr['fresh'])} atuais · {len(fr['stale'])} alterados ·"
                              f" {len(fr['new'])} por ingerir · {len(fr['removed'])} removidos")
                    pend = fr["stale"] + fr["new"] + fr["removed"]
                    if pend:
                        detail += " (ex.: " + ", ".join(pend[:3]) + ")"
                    add("OK" if not pend else "WARN", "frescura (material × memória)", detail,
                        None if not pend else "corre `coala.py ingest` (idempotente)")
                except CoalaError as exc:
                    add("WARN", "frescura", exc.what, exc.solution)
        if skill:
            man_p = os.path.join(skill, MANIFEST_NAME)
            local_engine = os.path.join(skill, "scripts", "coala.py")
            if os.path.isfile(man_p) and os.path.isfile(local_engine):
                try:
                    with open(man_p, encoding="utf-8") as fh:
                        man = json.load(fh)
                    expected = (man.get("engine") or {}).get("sha256")
                except (OSError, ValueError):
                    man, expected = None, None
                if man is None:
                    add("WARN", "manifesto", f"{man_p} ilegível", "reinstala com o instalador")
                else:
                    cur = file_sha256(local_engine)
                    add("OK" if expected == cur else "WARN", "motor local",
                        f"v{engine_version_of(local_engine)} · sha256 {cur[:12]}…"
                        + ("" if expected == cur else " (difere do manifesto)"),
                        None if expected == cur else f"reinstala: `{INSTALL_HINT}`")
    finally:
        conn.close()
    if os.path.exists(LEGACY_GLOBAL_DB) and os.path.abspath(path) != os.path.abspath(LEGACY_GLOBAL_DB):
        add("WARN", "memória global legada", f"{LEGACY_GLOBAL_DB} existe (não é usada por este motor)",
            "arquiva-a — ver references/migrar.md da coala-agent-skill")
    return checks


# ------------------------------------------------------------- backup/import
def backup_db(path: str, out: str = None, label: str = "coala"):
    """Snapshot consistente (API de backup do SQLite) → ficheiro autónomo 0600. Devolve (out, check, n)."""
    if not os.path.isfile(path):
        raise CoalaError(f"a base {path} não existe", "nada a copiar — corre `init` primeiro")
    out = (os.path.abspath(os.path.expanduser(out)) if out
           else os.path.join(os.path.dirname(path), "backups", f"{label}-{stamp()}.sqlite"))
    if os.path.exists(out):
        raise CoalaError(f"o destino {out} já existe", "escolhe outro --out (backups nunca sobrescrevem)")
    os.makedirs(os.path.dirname(out), mode=0o700, exist_ok=True)
    src = sqlite3.connect(path)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=DELETE")   # ficheiro autónomo (sem -wal/-shm)
    finally:
        dst.close()
        src.close()
    os.chmod(out, 0o600)
    chk = sqlite3.connect(out)
    try:
        res = chk.execute("PRAGMA quick_check").fetchone()[0]
        n = chk.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
    finally:
        chk.close()
    return out, res, n


def parse_ids(spec: str) -> set:
    out = set()
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out.update(range(min(a, b), max(a, b) + 1))
        elif part.isdigit():
            out.add(int(part))
        else:
            raise UsageError(f"--ids inválido: {part!r}", "usa ids e intervalos, ex.: --ids 5-20,23")
    return out


def _chunks(seq, n=500):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def import_entries(dest, backend, src_path: str, key_prefixes=None, source_prefixes=None,
                   tags=None, ids=None, all_rows=False, with_graph=False, graph_entities=None,
                   dry_run=False) -> dict:
    """
    Copia registos de outra base CoALA preservando tipo, conteúdo, origem, chave,
    recorded_at, validade, fonte, tags, proveniência e a cadeia de supersessão
    (fecho transitivo: versões anteriores/posteriores dos selecionados vêm juntas).
    Idempotente: um registo com o mesmo (tipo, conteúdo, recorded_at, chave) é "já presente".
    """
    src = open_ro(src_path)
    try:
        if "memory_entries" not in table_names(src):
            raise CoalaError(f"{src_path} não é uma base CoALA (sem memory_entries)",
                             "indica a base de origem correta em --from")
        conds, params = [], []
        for p in key_prefixes or []:
            conds.append("substr(COALESCE(supersession_key,''),1,?) = ?")
            params += [len(p), p]
        for p in source_prefixes or []:
            conds.append("substr(COALESCE(source,''),1,?) = ?")
            params += [len(p), p]
        for t in tags or []:
            conds.append(tag_clause("tags"))
            params.append(tag_param(t))
        if all_rows:
            conds.append("1=1")
        sel = set()
        if conds:
            sel |= {r[0] for r in src.execute(
                "SELECT id FROM memory_entries WHERE " + " OR ".join(conds), params)}
        if ids:
            for chunk in _chunks(ids):
                q = ",".join("?" * len(chunk))
                sel |= {r[0] for r in src.execute(f"SELECT id FROM memory_entries WHERE id IN ({q})", chunk)}
        direct = len(sel)
        frontier = set(sel)
        while frontier:                                   # fecho da cadeia de supersessão
            found = set()
            for chunk in _chunks(frontier):
                q = ",".join("?" * len(chunk))
                found |= {r[0] for r in src.execute(
                    f"SELECT superseded_by FROM memory_entries WHERE id IN ({q})"
                    " AND superseded_by IS NOT NULL", chunk)}
                found |= {r[0] for r in src.execute(
                    f"SELECT id FROM memory_entries WHERE superseded_by IN ({q})", chunk)}
            frontier = found - sel
            sel |= frontier
        prov = {}
        if "provenance" in table_names(src):
            for chunk in _chunks(sel):
                q = ",".join("?" * len(chunk))
                for r in src.execute(f"SELECT entry_id, note FROM provenance WHERE entry_id IN ({q})", chunk):
                    prov[r[0]] = r[1]
        ts = now_iso()
        mapping, imported, present = {}, [], 0
        rows = []
        for chunk in _chunks(sorted(sel)):
            q = ",".join("?" * len(chunk))
            rows += src.execute(f"SELECT * FROM memory_entries WHERE id IN ({q}) ORDER BY id", chunk).fetchall()
        dest_cid = "content_id" in table_columns(dest, "memory_entries")
        for r in rows:
            # o id por conteúdo viaja com o registo (base v3) ou é calculado (base v2)
            cid = (r["content_id"] if "content_id" in r.keys() and r["content_id"] else row_content_id(r))
            hit = (dest.execute("SELECT id FROM memory_entries WHERE content_id=? LIMIT 1", (cid,)).fetchone()
                   if dest_cid else None)
            if hit is None and r["supersession_key"] is not None:
                hit = dest.execute(
                    "SELECT id FROM memory_entries WHERE supersession_key=? AND recorded_at=?"
                    " AND memory_type=? AND content=? LIMIT 1",
                    (r["supersession_key"], r["recorded_at"], r["memory_type"], r["content"])).fetchone()
            elif hit is None:
                hit = dest.execute(
                    "SELECT id FROM memory_entries WHERE supersession_key IS NULL AND recorded_at=?"
                    " AND memory_type=? AND content=? LIMIT 1",
                    (r["recorded_at"], r["memory_type"], r["content"])).fetchone()
            if hit is not None:
                mapping[r["id"]] = hit[0]
                present += 1
                continue
            if dry_run:
                imported.append(r["id"])
                continue
            cur = dest.execute(
                "INSERT INTO memory_entries(memory_type, content, origin_class, supersession_key,"
                " recorded_at, valid_from, valid_until, source, tags, content_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?)",
                (r["memory_type"], r["content"], r["origin_class"], r["supersession_key"],
                 r["recorded_at"], r["valid_from"], r["valid_until"], r["source"], r["tags"], cid))
            new_id = cur.lastrowid
            index_content(dest, backend, new_id, r["content"])
            note = prov.get(r["id"])
            dest.execute("INSERT OR REPLACE INTO provenance(entry_id, note) VALUES (?,?)",
                         (new_id, (note + " | " if note else "")
                          + f"Importado de {os.path.abspath(src_path)}#{r['id']} em {ts}"))
            mapping[r["id"]] = new_id
            imported.append(r["id"])
        relinked = conflicts = 0
        if not dry_run:
            for r in rows:
                sb = r["superseded_by"]
                if sb is not None and sb in mapping and r["id"] in mapping:
                    cur = dest.execute("UPDATE memory_entries SET superseded_by=? WHERE id=?"
                                       " AND superseded_by IS NULL", (mapping[sb], mapping[r["id"]]))
                    relinked += cur.rowcount
            keys = {r["supersession_key"] for r in rows if r["supersession_key"]}
            for k in sorted(keys):                         # invariante: ≤ 1 versão ativa por chave
                act = dest.execute("SELECT id FROM memory_entries WHERE supersession_key=? AND"
                                   " superseded_by IS NULL ORDER BY recorded_at, id", (k,)).fetchall()
                for old in act[:-1]:
                    if supersede_entry(dest, old[0], act[-1][0], ts):
                        conflicts += 1
        ent_new = edge_new = 0
        if with_graph and not dry_run and "entity_nodes" in table_names(src):
            wanted = {n.strip() for n in (graph_entities or []) if n.strip()}
            nodes = {r["id"]: (r["name"], r["kind"]) for r in src.execute("SELECT id, name, kind FROM entity_nodes")}
            keep = {i: v for i, v in nodes.items() if not wanted or v[0] in wanted}
            for name, kind in keep.values():
                if dest.execute("SELECT 1 FROM entity_nodes WHERE name=?", (name,)).fetchone() is None:
                    dest.execute("INSERT INTO entity_nodes(name, kind) VALUES (?,?)", (name, kind))
                    ent_new += 1
            for e in src.execute("SELECT src, dst, rel FROM entity_edges"):
                if e["src"] in keep and e["dst"] in keep:
                    a, b = ensure_entities(dest, [keep[e["src"]][0], keep[e["dst"]][0]])
                    edge_new += 1 if link_entities(dest, a, b, e["rel"]) else 0
        if not dry_run:
            dest.commit()
        return {"from": os.path.abspath(src_path), "selected": len(sel), "selected_direct": direct,
                "chain_added": len(sel) - direct, "imported": len(imported), "already_present": present,
                "relinked": relinked, "conflicts_resolved": conflicts,
                "entities_new": ent_new, "edges_new": edge_new, "dry_run": dry_run}
    finally:
        src.close()


# ------------------------------------------------------ import JSONL (ids por conteúdo)
RECORD_TAG_FIELDS = ("site", "page", "kind", "status", "origin", "run")   # tags do registo canónico (plano §8.1)


def merge_tags(*groups):
    """Junta grupos de tags (CSV ou listas) sem repetir (sem distinguir maiúsculas), pela ordem. None se vazio."""
    out, seen = [], set()
    for g in groups:
        for t in (g.split(",") if isinstance(g, str) else (g or [])):
            t = str(t).strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                out.append(t)
    return ",".join(out) or None


def record_tags(rec: dict) -> list:
    """Tags derivadas de um registo canónico: site:, page:, kind:, status:, origin:, run: (vírgula → ';')."""
    out = []
    for f in RECORD_TAG_FIELDS:
        v = rec.get(f)
        if isinstance(v, (str, int)) and not isinstance(v, bool) and str(v).strip():
            out.append(f"{f}:{str(v).strip().replace(',', ';')}")
    return out


def _cid_arg(value, where: str, field: str) -> str:
    if isinstance(value, str) and CID_RE.match(value):
        return value
    raise UsageError(f"{where}: `{field}` inválido ({value!r})",
                     "usa um id por conteúdo (ex.: 16 hex) ou omite o campo para o motor o calcular")


def _opt_str(obj: dict, field: str, where: str):
    v = obj.get(field)
    if v is None or isinstance(v, str):
        return v
    raise UsageError(f"{where}: `{field}` tem de ser texto ou null", "corrige a linha do JSONL")


def _str_list(obj: dict, field: str, where: str) -> list:
    v = obj.get(field)
    if v is None:
        return []
    if isinstance(v, list) and all(isinstance(x, str) and x.strip() for x in v):
        return [x.strip() for x in v]
    raise UsageError(f"{where}: `{field}` tem de ser uma lista de textos", "corrige a linha do JSONL")


def jsonl_item(obj: dict, where: str, origin: str = "agent") -> dict:
    """
    Normaliza uma linha de import num item. Dois formatos:
      - linha do `export --format jsonl` (tem `content`): tipo/origem/chave/datas/fonte/tags/proveniência
        da linha; id = `cid`, ou `id` se for texto; `id` inteiro = id local da base de origem (só serve para
        religar o `superseded_by` inteiro de um export antigo); supersessão por `superseded_by_cid`;
      - registo canónico (tem `schema` e `body`, ex.: sitemem/1): id = `id` da linha se vier, senão o
        contrato de CID_FIELDS; conteúdo = o próprio registo em JSON (com o id); tags derivadas
        (record_tags) + `tags` da linha; supersessão pela lista `supersedes`; origem = `origin` do import.
    """
    mtype = obj.get("type")
    if mtype not in MEMORY_TYPES:
        raise UsageError(f"{where}: `type` {mtype!r} inválido", f"usa um de {', '.join(MEMORY_TYPES)}")
    item = {"type": mtype, "supersedes": [], "superseded_by": None, "superseded_by_int": None,
            "int_id": None, "provenance": None, "entities": _str_list(obj, "entities", where)}
    for f in ("key", "recorded_at", "valid_from", "valid_until", "source"):
        item[f] = _opt_str(obj, f, where)
    if "content" in obj:                                            # export do motor
        content = obj.get("content")
        if not isinstance(content, str) or not content.strip():
            raise UsageError(f"{where}: `content` vazio", "cada linha de registo precisa de conteúdo")
        org = obj.get("origin") or "agent"
        if org not in ORIGINS:
            raise UsageError(f"{where}: `origin` {org!r} inválido", f"usa um de {', '.join(ORIGINS)}")
        tags = _opt_str(obj, "tags", where)            # tal e qual (entra no id calculado de um export antigo)
        rid = obj.get("id")
        if obj.get("cid") is not None:
            cid = _cid_arg(obj["cid"], where, "cid")
        elif isinstance(rid, str):
            cid = _cid_arg(rid, where, "id")
        else:
            cid = entry_content_id(mtype, content, item["key"], org, item["source"], tags,
                                   item["recorded_at"], item["valid_from"])
        if isinstance(rid, int) and not isinstance(rid, bool):
            item["int_id"] = rid
        sb = obj.get("superseded_by_cid")
        if sb is not None:
            item["superseded_by"] = _cid_arg(sb, where, "superseded_by_cid")
        else:
            sb = obj.get("superseded_by")
            if isinstance(sb, str):
                item["superseded_by"] = _cid_arg(sb, where, "superseded_by")
            elif isinstance(sb, int) and not isinstance(sb, bool):
                item["superseded_by_int"] = sb
            elif sb is not None:
                raise UsageError(f"{where}: `superseded_by` inválido ({sb!r})", "usa o id do sucessor ou null")
        item.update(content=content, origin=org, tags=tags, provenance=_opt_str(obj, "provenance", where))
    else:                                                           # registo canónico
        if not isinstance(obj.get("schema"), str) or not obj["schema"].strip() or "body" not in obj:
            raise UsageError(f"{where}: registo canónico sem `schema`/`body`",
                             "usa uma linha do `export --format jsonl` (com `content`)"
                             " ou um registo com schema e body")
        if not item["key"]:
            raise UsageError(f"{where}: registo canónico sem `key`",
                             "dá a cada registo a sua chave (ex.: site/<s>/<pág>/<kind>/<nome>)")
        cid = _cid_arg(obj["id"], where, "id") if obj.get("id") is not None else content_id(obj)
        sup = obj.get("supersedes")
        if sup is not None and not isinstance(sup, list):
            raise UsageError(f"{where}: `supersedes` tem de ser uma lista de ids", "corrige a linha do JSONL")
        extra = obj.get("tags")
        if extra is not None and not isinstance(extra, (str, list)):
            raise UsageError(f"{where}: `tags` tem de ser CSV ou lista", "corrige a linha do JSONL")
        rec = dict(obj)
        rec["id"] = cid
        item.update(content=json.dumps(rec, ensure_ascii=False, sort_keys=True), origin=origin,
                    tags=merge_tags(record_tags(obj), extra),
                    supersedes=[_cid_arg(s, where, "supersedes") for s in (sup or [])])
    item["cid"] = cid
    return item


def load_jsonl(path: str, origin: str = "agent"):
    """Lê e valida TODO o ficheiro (ou `-` = stdin) antes de escrever. Devolve (itens, arestas, rótulo)."""
    if path == "-":
        text, label = sys.stdin.read(), "<stdin>"
    else:
        label = os.path.abspath(os.path.expanduser(path))
        try:
            with open(label, encoding="utf-8") as fh:
                text = fh.read()
        except OSError as exc:
            raise CoalaError(f"não foi possível ler {label} ({exc})", "confirma o caminho do ficheiro JSONL")
        except UnicodeDecodeError:
            raise UsageError(f"{label} não é UTF-8", "exporta/grava o JSONL em UTF-8")
    items, edges = [], []
    # split("\n"), não splitlines(): U+2028/U+2029 podem vir crus dentro das strings (ensure_ascii=False)
    for n, line in enumerate(text.split("\n"), 1):
        s = line.strip()
        if not s:
            continue
        where = f"{label}:{n}"
        try:
            obj = json.loads(s)
        except ValueError as exc:
            raise UsageError(f"{where}: JSON inválido ({exc})", "um objeto JSON por linha")
        if not isinstance(obj, dict):
            raise UsageError(f"{where}: a linha não é um objeto JSON", "um objeto JSON por linha")
        if "edge" in obj:
            e = obj["edge"]
            if not (isinstance(e, list) and len(e) == 3 and all(isinstance(x, str) and x.strip() for x in e)):
                raise UsageError(f"{where}: aresta inválida {e!r}", "usa {\"edge\": [origem, relação, destino]}")
            edges.append(tuple(x.strip() for x in e))
            continue
        if "content" not in obj and "schema" not in obj:
            raise UsageError(f"{where}: formato desconhecido (nem `content` nem `schema`)",
                             "usa linhas do `export --format jsonl` ou registos canónicos (schema/key/type/body)")
        try:
            item = jsonl_item(obj, where, origin)
        except UnicodeEncodeError as exc:
            raise UsageError(f"{where}: texto com caracteres inválidos ({exc.reason})",
                             "corrige a codificação da linha")
        item["line"] = n
        items.append(item)
    return items, edges, label


def _chain_reaches(conn, start: int, target: int) -> bool:
    """A cadeia superseded_by que parte de `start` chega a `target`? (evita ciclos ao religar)"""
    seen, cur = set(), start
    while cur is not None and cur not in seen:
        if cur == target:
            return True
        seen.add(cur)
        row = conn.execute("SELECT superseded_by FROM memory_entries WHERE id=?", (cur,)).fetchone()
        cur = row[0] if row else None
    return False


def import_jsonl(dest, backend, items: list, edges: list, label: str, add_tags=None) -> dict:
    """
    Importa os itens de load_jsonl numa ligação aberta, SEM commit (quem chama decide — o --dry-run corre
    isto numa cópia em memória e descarta):
      1. id por conteúdo já existe na base, ou repete-se no ficheiro → já presente, nada muda;
      2. registo novo entra com o id, o tipo, a origem, a chave, as datas, a fonte, as tags (+ --add-tags) e a
         proveniência da linha (+ nota `Importado de …`), indexado (FTS5/vetores) e ligado às suas entidades;
      3. a supersessão é refeita pelas relações de id por conteúdo (`supersedes` dos registos canónicos,
         `superseded_by_cid`/`superseded_by` do export), também contra registos que já estavam na base;
      4. invariante do motor: ≤1 versão ativa por chave (a mais recente ganha, como no `import --from`);
      5. as arestas `{"edge": [src, rel, dst]}` entram no grafo (idempotente).
    """
    ts = now_iso()
    extra = merge_tags(add_tags)
    res = {"from": label, "format": "jsonl", "records": len(items), "imported": 0, "already_present": 0,
           "duplicates_in_file": 0, "superseded": 0, "relations_missing": 0, "conflicts_resolved": 0,
           "entities_new": 0, "edges_new": 0}
    n_ent0 = dest.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0]
    seen = set()
    for it in items:
        cid = it["cid"]
        if cid in seen:
            res["duplicates_in_file"] += 1
            continue
        seen.add(cid)
        if dest.execute("SELECT 1 FROM memory_entries WHERE content_id=? LIMIT 1", (cid,)).fetchone():
            res["already_present"] += 1
            continue
        cur = dest.execute(
            "INSERT INTO memory_entries(memory_type, content, origin_class, supersession_key, recorded_at,"
            " valid_from, valid_until, source, tags, content_id) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (it["type"], it["content"], it["origin"], it["key"], it["recorded_at"] or ts, it["valid_from"],
             it["valid_until"], it["source"], merge_tags(it["tags"], extra) if extra else it["tags"], cid))
        new_id = cur.lastrowid
        index_content(dest, backend, new_id, it["content"])
        dest.execute("INSERT OR REPLACE INTO provenance(entry_id, note) VALUES (?,?)",
                     (new_id, (it["provenance"] + " | " if it["provenance"] else "")
                      + f"Importado de {label} (linha {it['line']}) em {ts}; id {cid}"))
        if it["entities"]:
            link_entry_entities(dest, new_id, ensure_entities(dest, it["entities"]))
        res["imported"] += 1

    int_cid = {it["int_id"]: it["cid"] for it in items if it["int_id"] is not None}
    rels = []
    for it in items:
        rels += [(s, it["cid"]) for s in it["supersedes"]]
        sb = it["superseded_by"] or int_cid.get(it["superseded_by_int"])
        if sb:
            rels.append((it["cid"], sb))
        elif it["superseded_by_int"] is not None:
            res["relations_missing"] += 1
    for old_cid, new_cid in rels:
        if old_cid == new_cid:
            continue
        new = dest.execute("SELECT id FROM memory_entries WHERE content_id=?"
                           " ORDER BY (superseded_by IS NULL) DESC, id DESC LIMIT 1", (new_cid,)).fetchone()
        olds = [r[0] for r in dest.execute("SELECT id FROM memory_entries WHERE content_id=?", (old_cid,))]
        if new is None or not olds:
            res["relations_missing"] += 1
            continue
        for oid in olds:
            if oid != new[0] and not _chain_reaches(dest, new[0], oid) and supersede_entry(dest, oid, new[0], ts):
                res["superseded"] += 1

    for k in sorted({it["key"] for it in items if it["key"]}):     # invariante: ≤1 versão ativa por chave
        act = dest.execute("SELECT id FROM memory_entries WHERE supersession_key=? AND superseded_by IS NULL"
                           " ORDER BY recorded_at, id", (k,)).fetchall()
        for old in act[:-1]:
            if supersede_entry(dest, old[0], act[-1][0], ts):
                res["conflicts_resolved"] += 1

    for s, rel, d in edges:
        a, b = ensure_entities(dest, [s, d])
        if link_entities(dest, a, b, rel):
            res["edges_new"] += 1
    res["entities_new"] = dest.execute("SELECT COUNT(*) FROM entity_nodes").fetchone()[0] - n_ent0
    return res


# ------------------------------------------------------------- forget (apagar de verdade)
def forget_result(tags: list) -> dict:
    return {"tags": list(tags), "entries": 0, "chunks": 0, "fts_rows": 0, "vectors": 0, "provenance": 0,
            "entity_links": 0, "entities": 0, "edges": 0, "relinked": 0, "conflicts_resolved": 0}


def forget_by_tags(conn, backend, tags: list, optimize: bool = True) -> dict:
    """
    APAGA de verdade (sem expirar, sem backup) todos os registos com QUALQUER das tags — todas as versões
    (ativas, suplantadas, expiradas) — e o que lhes pertence em todas as tabelas: chunks (texto e vetor
    `embedding`), índice FTS5, `chunks_vec` (sqlite-vec), proveniência, ligações registo↔entidade e as
    entidades que só eles citavam (com as arestas delas). Um sobrevivente cujo sucessor é apagado passa a
    apontar para o sucessor seguinte que sobrevive (ou fica sem sucessor, com a validade já fechada).
    Sem commit (quem chama decide); quem chama liga `PRAGMA secure_delete` para zerar as páginas libertadas.
    """
    ts = now_iso()
    res = forget_result(tags)
    if not tags:
        return res
    where = " OR ".join(tag_clause("tags") for _ in tags)
    ids = [r[0] for r in conn.execute(f"SELECT id FROM memory_entries WHERE {where}",
                                      [tag_param(t) for t in tags])]
    if not ids:
        return res
    tables = table_names(conn)
    if "chunks_vec" in tables and backend != "sqlite-vec":
        raise DependencyError("a base tem vetores sqlite-vec (chunks_vec) mas a extensão não carregou",
                              "instala o sqlite-vec (`pip install sqlite-vec`) e repete o forget — sem ela os"
                              " vetores ficariam para trás")
    doomed = set(ids)
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS forget_ids(id INTEGER PRIMARY KEY)")
    conn.execute("DELETE FROM temp.forget_ids")
    conn.executemany("INSERT INTO temp.forget_ids(id) VALUES (?)", [(i,) for i in ids])
    in_d = "IN (SELECT id FROM temp.forget_ids)"

    # 1. índice FTS5 (conteúdo externo: o 'delete' leva o texto indexado) e vetores
    chunks = conn.execute("SELECT id, text, embedding IS NOT NULL AS emb FROM chunks"
                          f" WHERE entry_id {in_d}").fetchall()
    for c in chunks:
        conn.execute("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)", (c[0], c[1]))
    res["fts_rows"] = len(chunks)
    res["vectors"] = sum(1 for c in chunks if c[2])
    if "chunks_vec" in tables:
        for part in _chunks([c[0] for c in chunks]):
            q = ",".join("?" * len(part))
            res["vectors"] += conn.execute(f"DELETE FROM chunks_vec WHERE rowid IN ({q})", part).rowcount

    # 2. sobreviventes cujo sucessor vai ser apagado → sucessor seguinte que sobrevive (ou nenhum)
    succ = {r[0]: r[1] for r in conn.execute(f"SELECT id, superseded_by FROM memory_entries WHERE id {in_d}")}
    keys = set()
    for r in conn.execute(f"SELECT id, superseded_by, supersession_key FROM memory_entries"
                          f" WHERE superseded_by {in_d} AND id NOT {in_d}").fetchall():
        nxt, hops = r[1], set()
        while nxt in doomed and nxt not in hops:
            hops.add(nxt)
            nxt = succ.get(nxt)
        nxt = None if nxt in doomed else nxt
        conn.execute("UPDATE memory_entries SET superseded_by=? WHERE id=?", (nxt, r[0]))
        add_provenance(conn, r[0], f"Sucessor apagado por `forget` em {ts}"
                       + (f"; passa a suplantado por #{nxt}" if nxt else ""))
        res["relinked"] += 1
        if r[2]:
            keys.add(r[2])
    conn.execute(f"UPDATE memory_entries SET superseded_by=NULL WHERE id {in_d}")

    # 3. linhas de todas as tabelas (a ordem respeita as chaves estrangeiras)
    ents = []
    if "entry_entities" in tables:
        ents = [r[0] for r in conn.execute(f"SELECT DISTINCT entity_id FROM entry_entities WHERE entry_id {in_d}")]
        res["entity_links"] = conn.execute(f"DELETE FROM entry_entities WHERE entry_id {in_d}").rowcount
    res["provenance"] = conn.execute(f"DELETE FROM provenance WHERE entry_id {in_d}").rowcount
    res["chunks"] = conn.execute(f"DELETE FROM chunks WHERE entry_id {in_d}").rowcount
    res["entries"] = conn.execute(f"DELETE FROM memory_entries WHERE id {in_d}").rowcount

    # 4. grafo: entidades que só os apagados citavam saem, com as arestas delas
    for e in ents:
        if conn.execute("SELECT 1 FROM entry_entities WHERE entity_id=? LIMIT 1", (e,)).fetchone():
            continue
        res["edges"] += conn.execute("DELETE FROM entity_edges WHERE src=? OR dst=?", (e, e)).rowcount
        res["entities"] += conn.execute("DELETE FROM entity_nodes WHERE id=?", (e,)).rowcount

    for k in sorted(keys):                                   # invariante: ≤1 versão ativa por chave
        act = conn.execute("SELECT id FROM memory_entries WHERE supersession_key=? AND superseded_by IS NULL"
                           " ORDER BY recorded_at, id", (k,)).fetchall()
        for old in act[:-1]:
            if supersede_entry(conn, old[0], act[-1][0], ts):
                res["conflicts_resolved"] += 1
    if optimize:   # funde os segmentos do FTS5: os termos apagados deixam de existir no índice
        conn.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('optimize')")
    conn.execute("DELETE FROM temp.forget_ids")
    return res


# ----------------------------------------------------------------------- comandos
def cmd_init(args) -> int:
    path, how, skill = resolve_db(args.db)
    conn, path, backend = connect_path(path)
    conn.commit()
    wal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    uv = conn.execute("PRAGMA user_version").fetchone()[0]
    emit(f"OK: esquema CoALA v{uv} pronto em {path} ({how})\n"
         f"  modo journal={wal} · backend vetorial={backend} · FTS5 disponível · motor v{ENGINE_VERSION}")
    conn.close()
    return 0


def cmd_where(args) -> int:
    path, how, skill = resolve_db(args.db)
    root = project_root_of(skill) if skill else None
    exists = os.path.isfile(path)
    payload = {"db": path, "resolved_by": how, "skill": skill, "project_root": root,
               "exists": exists, "engine": os.path.abspath(__file__), "engine_version": ENGINE_VERSION}
    if args.json:
        emit(json.dumps(payload, ensure_ascii=False))
    else:
        lines = [f"Base: {path}" + ("" if exists else " (ainda não criada — corre `init`)"),
                 f"  resolvida por: {how}"]
        if skill:
            lines.append(f"  skill local: {skill}")
            lines.append(f"  projeto: {root}")
        lines.append(f"  motor: {os.path.abspath(__file__)} (v{ENGINE_VERSION})")
        emit("\n".join(lines))
    return 0


def cmd_add(args) -> int:
    if not (args.content or "").strip():
        raise UsageError("--content vazio", "fornece o texto do conhecimento a guardar")
    conn, path, backend = connect(args.db)
    ts = now_iso()
    origin = args.origin
    if origin != "untrusted" and args.source:
        # conteúdo web marcado automaticamente como não-confiável
        if re.match(r"^https?://", args.source.strip(), re.I) and args.origin == "agent":
            origin = "untrusted"

    entry_id, superseded = insert_entry(conn, backend, args.type, args.content.strip(), origin,
                                        key=args.key, source=args.source, tags=args.tags,
                                        valid_from=args.valid_from, valid_until=args.valid_until)

    add_provenance(conn, entry_id,
                   f"Registado via CLI em {ts}"
                   + (f"; suplanta {', '.join('#%d' % i for i in superseded)}" if superseded else ""))

    entity_ids = []
    if args.entities:
        names = [n.strip() for n in re.split(r"[,;]", args.entities) if n.strip()]
        entity_ids = ensure_entities(conn, names)
        for a, b in zip(entity_ids, entity_ids[1:]):
            link_entities(conn, a, b, "co-ocorre")
        link_entry_entities(conn, entry_id, entity_ids)

    conn.commit()
    conn.close()
    payload = {"ok": True, "id": entry_id, "type": args.type, "origin": origin,
               "superseded": superseded, "entities": entity_ids,
               "supersession_key": args.key, "db": path}
    if args.json:
        emit(json.dumps(payload, ensure_ascii=False))
    else:
        msg = f"OK: registo #{entry_id} ({args.type}, origem:{origin}) gravado em {path}"
        if superseded:
            msg += f"\n  supersessão: suplantou {', '.join('#%d' % i for i in superseded)} (chave: {args.key})"
        if entity_ids:
            msg += f"\n  entidades ligadas: {len(entity_ids)}"
        emit(msg)
    return 0


def cmd_search(args) -> int:
    conn, path, backend = connect(args.db)
    tags = [t for t in (args.tags or "").split(",") if t.strip()]
    any_tags = [t for t in (getattr(args, "any_tags", None) or "").split(",") if t.strip()]
    where, params = build_filter(args.type, tags, args.include_superseded, args.include_expired,
                                 any_tags=any_tags)
    w_fts = args.w_fts if args.w_fts is not None else float(os.environ.get(W_FTS_ENV, "1.0"))
    w_vec = args.w_vec if args.w_vec is not None else float(os.environ.get(W_VEC_ENV, "1.0"))
    fused = hybrid_search(conn, args.query, where, params, limit=args.limit,
                          w_fts=w_fts, w_vec=w_vec)
    ids = [f["eid"] for f in fused]
    rows = {}
    if ids:
        qmarks = ",".join("?" * len(ids))
        for r in conn.execute(f"SELECT * FROM memory_entries WHERE id IN ({qmarks})", ids).fetchall():
            rows[r["id"]] = r

    if args.json:
        out = []
        for f in fused:
            r = rows[f["eid"]]
            out.append({
                "id": r["id"], "type": r["memory_type"], "origin": r["origin_class"],
                "content": redact(r["content"]), "source": r["source"], "tags": r["tags"],
                "valid_from": r["valid_from"], "valid_until": r["valid_until"],
                "superseded_by": r["superseded_by"], "recorded_at": r["recorded_at"],
                "score": {"rrf": round(f["rrf"], 6), "fts_rank": f["fts_rank"],
                          "vec_rank": f["vec_rank"], "fts_bm25": f["fts_score"],
                          "vec_cosine": f["vec_sim"]},
            })
        emit(json.dumps({"query": args.query, "backend": backend, "results": out},
                        ensure_ascii=False))
    else:
        if not fused:
            emit(f"Sem resultados para {args.query!r} (base: {path}).\n"
                 "  Dicas: termos diferentes, `--include-superseded`, ou `add` primeiro.")
        else:
            blocks = [f"Resultados para {args.query!r} — fusão RRF (k={RRF_K}, w_fts={w_fts},"
                      f" w_vec={w_vec}) · backend vetorial: {backend}"]
            for f in fused:
                r = rows[f["eid"]]
                extra = (f"score rrf={f['rrf']:.4f} · "
                         f"fts#{f['fts_rank'] if f['fts_rank'] is not None else '–'}")
                if f["vec_rank"] is not None and f["vec_sim"] is not None:
                    extra += f" · vec#{f['vec_rank']} (cos={f['vec_sim']:.3f})"
                blocks.append(entry_lines(r, extra))
            emit("\n\n".join(blocks))
    conn.close()
    return 0


def cmd_recall(args) -> int:
    conn, path, backend = connect(args.db)
    tags = [t for t in (args.tags or "").split(",") if t.strip()]
    any_tags = [t for t in (getattr(args, "any_tags", None) or "").split(",") if t.strip()]
    where, params = build_filter(args.type, tags, args.include_superseded, args.include_expired,
                                 any_tags=any_tags)
    budget = max(1, args.budget)

    if args.query:
        fused = hybrid_search(conn, args.query, where, params, limit=max(args.top * 4, 40),
                              w_fts=1.0, w_vec=1.0)
        scores = {f["eid"]: f["rrf"] for f in fused}
        order = [f["eid"] for f in fused]
    else:
        rows = conn.execute(
            "SELECT e.id AS id FROM memory_entries e WHERE 1=1" + where +
            " ORDER BY e.recorded_at DESC LIMIT ?", list(params) + [max(args.top * 6, 60)]).fetchall()
        order = [r["id"] for r in rows]
        scores = {}

    if not order:
        emit("(working memory vazia — nada recuperado com estes filtros)")
        conn.close()
        return 0

    qmarks = ",".join("?" * len(order))
    rows = {r["id"]: r for r in conn.execute(
        f"SELECT * FROM memory_entries WHERE id IN ({qmarks})", order).fetchall()}

    chosen = budgeted_selection(order, rows, scores, budget, args.top, bool(args.query))
    used = sum(est_tokens(r["content"]) + 40 for _, _, _, r in chosen)

    if args.json:
        payload = {
            "budget_tokens": budget, "used_tokens": used, "count": len(chosen),
            "items": [{"id": r["id"], "type": r["memory_type"], "origin": r["origin_class"],
                       "content": redact(r["content"]), "tags": r["tags"], "source": r["source"],
                       "recorded_at": r["recorded_at"], "valid_until": r["valid_until"],
                       "score": {"combined": round(c, 4), "relevance": round(rel, 4),
                                 "recency": round(rec, 4)}}
                      for c, rec, rel, r in chosen],
        }
        emit(json.dumps(payload, ensure_ascii=False))
    else:
        blocks = [f"# Working Memory (coala recall) — {used}/{budget} tokens estimados,"
                  f" {len(chosen)} excertos · {path}"]
        for c, rec, rel, r in chosen:
            blocks.append(entry_lines(r, f"score={c:.3f} (rel={rel:.2f} · rec={rec:.2f})"))
        emit("\n\n".join(blocks))
    conn.close()
    return 0


def cmd_graph(args) -> int:
    conn, path, backend = connect(args.db)
    node = conn.execute("SELECT id, name, kind FROM entity_nodes WHERE LOWER(name)=LOWER(?)",
                        (args.entity,)).fetchone()
    if node is None:
        raise CoalaError(f"a entidade {args.entity!r} não existe em {path}",
                         "cria-a com `add --entities \"nome,...\"` ou `link <src> <rel> <dst>`")
    depth = max(1, args.depth)
    sql = """
    WITH RECURSIVE reach(id, name, kind, depth, path, rel_from) AS (
        SELECT id, name, kind, 0, ',' || id || ',', NULL
        FROM entity_nodes WHERE id = :root
        UNION ALL
        SELECT n.id, n.name, n.kind, r.depth + 1, r.path || n.id || ',', e.rel
        FROM reach r
        JOIN (
            SELECT src AS a, dst AS b, rel FROM entity_edges
            UNION
            SELECT dst AS a, src AS b, rel FROM entity_edges
        ) e ON e.a = r.id
        JOIN entity_nodes n ON n.id = e.b
        WHERE r.depth < :depth
          AND instr(r.path, ',' || n.id || ',') = 0
    )
    SELECT id, name, kind, depth, rel_from FROM reach ORDER BY depth, name
    """
    rows = conn.execute(sql, {"root": node["id"], "depth": depth}).fetchall()
    if args.json:
        emit(json.dumps({"entity": args.entity, "depth": depth,
                         "nodes": [{"name": r["name"], "kind": r["kind"], "depth": r["depth"],
                                    "via_rel": r["rel_from"]} for r in rows]},
                        ensure_ascii=False))
    else:
        lines = [f"Grafo a partir de {node['name']!r} (profundidade ≤ {depth}, travessia CTE recursiva):"]
        for r in rows:
            pad = "  " * r["depth"]
            via = f"  ← rel: {r['rel_from']}" if r["rel_from"] else "  (raiz)"
            lines.append(f"{pad}{'└─ ' if r['depth'] else ''}{r['name']}"
                         f" [{r['kind'] or 'entidade'}] d={r['depth']}{via}")
        emit("\n".join(lines))
    conn.close()
    return 0


def cmd_link(args) -> int:
    conn, path, backend = connect(args.db)
    ids = ensure_entities(conn, [args.src, args.dst])
    created = link_entities(conn, ids[0], ids[1], args.rel)
    conn.commit()
    conn.close()
    if args.json:
        emit(json.dumps({"ok": True, "src": args.src, "rel": args.rel, "dst": args.dst,
                         "created": created}, ensure_ascii=False))
    else:
        estado = "criada" if created else "já existia"
        emit(f"OK: aresta {args.src} --{args.rel}--> {args.dst} ({estado})")
    return 0


def cmd_supersede(args) -> int:
    if not (args.content or "").strip():
        raise UsageError("--content vazio", "fornece o novo conteúdo do facto")
    conn, path, backend = connect(args.db)
    old = conn.execute("SELECT * FROM memory_entries WHERE id=?", (args.id,)).fetchone()
    if old is None:
        raise CoalaError(f"o registo #{args.id} não existe em {path}",
                         "confirma o id com `search` ou `stats`")
    if old["superseded_by"] is not None:
        raise CoalaError(
            f"o registo #{args.id} já foi suplantado por #{old['superseded_by']}",
            "suplanta a versão ativa ou usa `search --include-superseded` para ver o histórico")
    ts = now_iso()
    new_id, _auto = insert_entry(conn, backend, old["memory_type"], args.content.strip(),
                                 args.origin or old["origin_class"], key=old["supersession_key"],
                                 source=args.source or old["source"], tags=args.tags or old["tags"],
                                 valid_from=args.valid_from or ts, valid_until=args.valid_until)
    supersede_entry(conn, old["id"], new_id, ts)
    add_provenance(conn, new_id, f"Substituição explícita de #{old['id']} via `supersede` em {ts}")
    conn.commit()
    conn.close()
    if args.json:
        emit(json.dumps({"ok": True, "old_id": old["id"], "new_id": new_id,
                         "supersession_key": old["supersession_key"]}, ensure_ascii=False))
    else:
        emit(f"OK: #{old['id']} suplantado por #{new_id} ({old['memory_type']}).\n"
             f"  o antigo ficou com superseded_by=#{new_id} e valid_until={ts}")
    return 0


def cmd_stats(args) -> int:
    conn, path, backend = connect(args.db)
    now = now_iso()
    total = conn.execute("SELECT COUNT(*) AS n FROM memory_entries").fetchone()["n"]
    by_type = {r["memory_type"]: r["n"] for r in conn.execute(
        "SELECT memory_type, COUNT(*) AS n FROM memory_entries GROUP BY memory_type").fetchall()}
    by_origin = {r["origin_class"]: r["n"] for r in conn.execute(
        "SELECT origin_class, COUNT(*) AS n FROM memory_entries GROUP BY origin_class").fetchall()}
    superseded = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_entries WHERE superseded_by IS NOT NULL").fetchone()["n"]
    expired = conn.execute(
        "SELECT COUNT(*) AS n FROM memory_entries WHERE superseded_by IS NULL"
        " AND valid_until IS NOT NULL AND valid_until <= ?", (now,)).fetchone()["n"]
    active = total - superseded - expired
    chunks_n = conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"]
    ents = conn.execute("SELECT COUNT(*) AS n FROM entity_nodes").fetchone()["n"]
    edges = conn.execute("SELECT COUNT(*) AS n FROM entity_edges").fetchone()["n"]
    sources = conn.execute("SELECT COUNT(*) AS n FROM ingest_sources WHERE segments > 0").fetchone()["n"]
    schema_v = conn.execute("PRAGMA user_version").fetchone()[0]
    size = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            size += os.path.getsize(path + suffix)
        except OSError:
            pass
    conn.close()
    data = {
        "db": path, "size_bytes": size, "backend_vector": backend,
        "schema_version": schema_v, "engine_version": ENGINE_VERSION,
        "entries": {"total": total, "active": active, "superseded": superseded,
                    "expired": expired},
        "by_type": by_type, "by_origin": by_origin,
        "chunks": chunks_n, "entities": ents, "edges": edges, "ingested_files": sources,
    }
    if args.json:
        emit(json.dumps(data, ensure_ascii=False))
    else:
        human = f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / (1024 * 1024):.2f} MB"
        lines = [
            f"Base de dados: {path} ({human}, WAL ativo)",
            f"Esquema v{schema_v} · motor v{ENGINE_VERSION} · backend vetorial: {backend}",
            f"Registos: {total} total · {active} ativos · {superseded} superados · {expired} expirados",
            "  por tipo:    " + ("  ".join(f"{k}={v}" for k, v in sorted(by_type.items())) or "—"),
            "  por origem:  " + ("  ".join(f"{k}={v}" for k, v in sorted(by_origin.items())) or "—"),
            f"Chunks indexados: {chunks_n} · entidades: {ents} · arestas: {edges}"
            f" · ficheiros ingeridos: {sources}",
        ]
        emit("\n".join(lines))
    return 0


def _write_text_atomic(path: str, text: str, mode: int = 0o644) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def cmd_export(args) -> int:
    conn, path, backend = connect(args.db)
    entries = conn.execute("SELECT * FROM memory_entries ORDER BY id").fetchall()
    prov = {r["entry_id"]: r["note"] for r in
            conn.execute("SELECT * FROM provenance").fetchall()}
    edges = conn.execute(
        "SELECT s.name AS src, e.rel AS rel, d.name AS dst"
        " FROM entity_edges e JOIN entity_nodes s ON s.id=e.src"
        " JOIN entity_nodes d ON d.id=e.dst").fetchall()
    cids = {r["id"]: r["content_id"] for r in entries}
    ents = entry_entity_names(conn)

    def jsonl_row(r) -> dict:
        # `cid`/`superseded_by_cid` = ids por conteúdo: é por eles que o `import --jsonl` refaz a base
        # (os `id` inteiros são locais desta base e ficam só como referência)
        d = {"id": r["id"], "cid": r["content_id"], "type": r["memory_type"], "origin": r["origin_class"],
             "key": r["supersession_key"], "superseded_by": r["superseded_by"],
             "superseded_by_cid": cids.get(r["superseded_by"]),
             "recorded_at": r["recorded_at"], "valid_from": r["valid_from"],
             "valid_until": r["valid_until"], "source": r["source"], "tags": r["tags"],
             "content": redact(r["content"]), "provenance": redact(prov.get(r["id"]) or "")}
        if ents.get(r["id"]):
            d["entities"] = ents[r["id"]]
        return d

    if args.format == "jsonl":
        # dump CANÓNICO: determinístico (sem carimbo de geração), um objeto por linha — p/ diff git
        lines = [json.dumps(jsonl_row(r), ensure_ascii=False, sort_keys=True) for r in entries]
        lines += [json.dumps({"edge": [e["src"], e["rel"], e["dst"]]}, ensure_ascii=False)
                  for e in sorted(edges, key=lambda e: (e["src"], e["rel"], e["dst"]))]
        text = "\n".join(lines) + ("\n" if lines else "")
    elif args.format == "json":
        payload = {
            "db": path, "generated_at": now_iso(), "backend_vector": backend,
            "entries": [{
                "id": r["id"], "cid": r["content_id"], "type": r["memory_type"], "origin": r["origin_class"],
                "content": redact(r["content"]), "supersession_key": r["supersession_key"],
                "superseded_by": r["superseded_by"], "superseded_by_cid": cids.get(r["superseded_by"]),
                "recorded_at": r["recorded_at"],
                "valid_from": r["valid_from"], "valid_until": r["valid_until"],
                "source": r["source"], "tags": r["tags"], "entities": ents.get(r["id"], []),
                "provenance": redact(prov.get(r["id"]) or ""),
            } for r in entries],
            "edges": [dict(e) for e in edges],
        }
        text = json.dumps(payload, ensure_ascii=False)
    else:
        groups = {"episodic": "Memória Episódica", "semantic": "Memória Semântica",
                  "procedural": "Memória Procedimental"}
        lines = [f"# Exportação CoALA — {path} — {now_iso()}",
                 "> Valores com aspeto de segredo aparecem mascarados.", ""]
        for mtype, title in groups.items():
            rows = [r for r in entries if r["memory_type"] == mtype]
            lines.append(f"## {title} ({len(rows)})")
            for r in rows:
                lines.append(f"### #{r['id']} · {r['origin_class']} · {fmt_validity(r)}"
                             f" · registado {r['recorded_at']} · id `{r['content_id']}`")
                if r["source"]:
                    lines.append(f"- fonte: {r['source']}")
                if r["tags"]:
                    lines.append(f"- tags: {r['tags']}")
                if r["supersession_key"]:
                    sup = f"- chave de supersessão: `{r['supersession_key']}`"
                    sup += (f" · suplantado por #{r['superseded_by']}" if r["superseded_by"]
                            else " · versão ativa")
                    lines.append(sup)
                if r["id"] in prov:
                    lines.append(f"- proveniência: {redact(prov[r['id']])}")
                lines.append("")
                lines.append(redact(r["content"].strip()))
                lines.append("")
            lines.append("")
        if edges:
            lines.append("## Grafo de entidades")
            for e in edges:
                lines.append(f"- {e['src']} --{e['rel']}--> {e['dst']}")
        text = "\n".join(lines)
    conn.close()
    if args.out:
        out = os.path.abspath(os.path.expanduser(args.out))
        _write_text_atomic(out, redact(text))
        emit(f"OK: {len(entries)} registos exportados ({args.format}) para {out}")
    else:
        emit(text)
    return 0


def cmd_doctor(args) -> int:
    path, how, skill = resolve_db(args.db)
    checks = doctor_report(path, how, skill, deep=args.deep, freshness=not args.no_freshness)
    fails = sum(1 for c in checks if c["level"] == "FAIL")
    warns = sum(1 for c in checks if c["level"] == "WARN")
    if args.json:
        emit(json.dumps({"db": path, "resolved_by": how, "skill": skill, "ok": fails == 0,
                         "fails": fails, "warns": warns, "checks": checks}, ensure_ascii=False))
    else:
        lines = [f"coala doctor — {path}"]
        for c in checks:
            line = f"  {c['level']:<5} {c['check']}: {c['detail']}"
            if c["solution"] and c["level"] in ("FAIL", "WARN"):
                line += f"\n        → {c['solution']}"
            lines.append(line)
        lines.append(f"RESULTADO: {'SAUDÁVEL' if fails == 0 else 'COM FALHAS'} · {fails} falha(s) · {warns} aviso(s)")
        emit("\n".join(lines))
    if fails:
        raise CoalaError(f"o doctor encontrou {fails} falha(s)", "segue a Solução de cada linha FAIL acima")
    return 0


def cmd_backup(args) -> int:
    path, how, skill = resolve_db(args.db)
    out, res, n = backup_db(path, args.out)
    if args.json:
        emit(json.dumps({"ok": res == "ok", "backup": out, "entries": n, "check": res}, ensure_ascii=False))
    else:
        emit(f"OK: backup consistente em {out} ({n} registos · quick_check={res} · 0600)")
    if res != "ok":
        raise CoalaError(f"o backup {out} não passou o quick_check ({res})", "repete o backup e corre `doctor --deep`")
    return 0


def cmd_restore(args) -> int:
    path, how, skill = resolve_db(args.db)
    src_path = os.path.abspath(os.path.expanduser(args.from_db))
    if os.path.abspath(src_path) == os.path.abspath(path):
        raise UsageError("--from aponta para a própria base", "indica um ficheiro de backup diferente")
    src = open_ro(src_path)
    try:
        res = src.execute("PRAGMA quick_check").fetchone()[0]
        if "memory_entries" not in table_names(src) or res != "ok":
            raise CoalaError(f"{src_path} não é uma base CoALA íntegra (quick_check={res})",
                             "escolhe outro backup (lista em memory/backups/)")
        n_src = src.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
    finally:
        src.close()
    if not args.yes:
        raise UsageError(f"restore substitui {path} pelo conteúdo de {src_path} ({n_src} registos)",
                         "repete com --yes (antes é feito backup automático da base atual)")
    pre = None
    if os.path.isfile(path):
        pre, _, _ = backup_db(path, label="coala-pre-restore")
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    src = sqlite3.connect(ro_uri(src_path), uri=True)
    dst = sqlite3.connect(path)
    try:
        src.backup(dst)
        dst.execute("PRAGMA journal_mode=WAL")
    finally:
        dst.close()
        src.close()
    conn, path, backend = connect_path(path)
    n = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
    conn.close()
    if args.json:
        emit(json.dumps({"ok": True, "db": path, "from": src_path, "entries": n,
                         "pre_restore_backup": pre}, ensure_ascii=False))
    else:
        emit(f"OK: {path} restaurada a partir de {src_path} ({n} registos)"
             + (f"\n  a base anterior ficou em {pre}" if pre else ""))
    return 0


def cmd_import_jsonl(args) -> int:
    """`import --jsonl FICHEIRO|-`: o ficheiro inteiro, numa transação (tudo ou nada), com ids por conteúdo."""
    if args.key_prefix or args.source_prefix or args.tags or args.ids or args.all or args.graph_entities:
        raise UsageError("--jsonl importa o ficheiro inteiro (os seletores são do --from)",
                         "tira --key-prefix/--source-prefix/--tags/--ids/--all/--graph-entities"
                         " (para acrescentar tags usa --add-tags)")
    items, edges, label = load_jsonl(args.jsonl, args.origin or "agent")
    path, how, skill = resolve_db(args.db)
    if args.dry_run:
        dest, backend = open_scratch_copy(path)          # mesmo algoritmo numa cópia em memória
    else:
        dest, path, backend = connect_path(path)
    try:
        res = import_jsonl(dest, backend, items, edges, label, add_tags=args.add_tags)
        if args.dry_run:
            dest.rollback()
        else:
            dest.commit()
    except BaseException:
        dest.rollback()
        raise
    finally:
        dest.close()
    res.update(db=path, dry_run=bool(args.dry_run))
    if args.json:
        emit(json.dumps(res, ensure_ascii=False))
    else:
        emit(f"{'DRY-RUN: ' if args.dry_run else 'OK: '}import de {label} → {path}\n"
             f"  registos={res['records']} · importados={res['imported']} · já presentes={res['already_present']}"
             f" · repetidos no ficheiro={res['duplicates_in_file']}\n"
             f"  supersessões refeitas={res['superseded']} · relações sem par={res['relations_missing']}"
             f" · conflitos de chave resolvidos={res['conflicts_resolved']}"
             f" · entidades novas={res['entities_new']} · arestas novas={res['edges_new']}")
    return 0


def cmd_import(args) -> int:
    if bool(getattr(args, "jsonl", None)) == bool(args.from_db):
        raise UsageError("import precisa de UMA origem", "usa --from <base> (outra base CoALA) ou --jsonl <ficheiro|->")
    if args.jsonl:
        return cmd_import_jsonl(args)
    if args.add_tags or args.origin:
        raise UsageError("--add-tags/--origin só valem com --jsonl", "o --from preserva as tags e a origem da base")
    key_prefixes = args.key_prefix or []
    source_prefixes = args.source_prefix or []
    tags = [t for t in (args.tags or "").split(",") if t.strip()]
    ids = parse_ids(args.ids) if args.ids else set()
    if not (key_prefixes or source_prefixes or tags or ids or args.all):
        raise UsageError("import sem seletor", "usa --key-prefix, --source-prefix, --tags, --ids ou --all")
    path, how, skill = resolve_db(args.db)
    src_path = os.path.abspath(os.path.expanduser(args.from_db))
    if src_path == os.path.abspath(path):
        raise UsageError("--from é a própria base de destino", "indica a base de origem")
    if args.dry_run:
        if os.path.isfile(path):
            dest, backend = open_ro(path), None
        else:
            dest, backend = sqlite3.connect(":memory:"), None
            dest.row_factory = sqlite3.Row
            init_schema(dest)
    else:
        dest, path, backend = connect_path(path)
    try:
        res = import_entries(dest, backend, src_path, key_prefixes, source_prefixes, tags, ids,
                             args.all, args.with_graph,
                             [n for n in (args.graph_entities or "").split(",") if n.strip()],
                             args.dry_run)
    finally:
        dest.close()
    res["db"] = path
    if args.json:
        emit(json.dumps(res, ensure_ascii=False))
    else:
        emit(f"{'DRY-RUN: ' if args.dry_run else 'OK: '}import de {res['from']} → {path}\n"
             f"  selecionados={res['selected']} (diretos={res['selected_direct']} + cadeia={res['chain_added']})"
             f" · importados={res['imported']} · já presentes={res['already_present']}\n"
             f"  cadeias religadas={res['relinked']} · conflitos de chave resolvidos={res['conflicts_resolved']}"
             f" · entidades novas={res['entities_new']} · arestas novas={res['edges_new']}")
    return 0


def cmd_forget(args) -> int:
    tags = []
    for t in args.tag or []:
        t = (t or "").strip().lower()
        if "," in t:
            raise UsageError(f"--tag {t!r} tem vírgula", "uma tag por --tag (repetível: basta UMA para apagar)")
        if t and t not in tags:
            tags.append(t)
    if not tags:
        raise UsageError("forget sem --tag", "indica a tag, ex.: `forget --tag site:exemplo --dry-run`")
    path, how, skill = resolve_db(args.db)
    if not os.path.isfile(path):                       # nada a esquecer — e não cria uma base para isso
        res = forget_result(tags)
    elif args.dry_run:
        conn, backend = open_scratch_copy(path)       # mesmo algoritmo numa cópia em memória
        try:
            res = forget_by_tags(conn, backend, tags, optimize=False)
        finally:
            conn.close()
    else:
        conn, path, backend = connect_path(path)
        try:
            conn.execute("PRAGMA secure_delete=ON")    # páginas libertadas são zeradas, não só marcadas
            res = forget_by_tags(conn, backend, tags)
            conn.commit()
            if res["entries"]:                         # o WAL ainda guarda as páginas antigas: devolve-as à base
                ck = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                res["wal_checkpoint"] = "ok" if ck and ck[0] == 0 else "ocupado"
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()
    res.update(db=path, dry_run=bool(args.dry_run), db_exists=os.path.isfile(path))
    if args.json:
        emit(json.dumps(res, ensure_ascii=False))
    else:
        verbo = "apagaria" if args.dry_run else "apagou"
        msg = (f"{'DRY-RUN: ' if args.dry_run else 'OK: '}forget --tag {', '.join(tags)} → {path}\n"
               f"  {verbo} registos={res['entries']} (todas as versões) · chunks={res['chunks']}"
               f" · FTS={res['fts_rows']} · vetores={res['vectors']} · proveniência={res['provenance']}"
               f" · ligações={res['entity_links']} · entidades={res['entities']} · arestas={res['edges']}\n"
               f"  sobreviventes religados={res['relinked']}"
               f" · conflitos de chave resolvidos={res['conflicts_resolved']}")
        if res.get("wal_checkpoint") == "ocupado":
            msg += ("\n  aviso: WAL ocupado por outro processo — as páginas antigas saem no próximo checkpoint"
                    " (fecha os outros acessos e corre `doctor`)")
        emit(msg)
    return 0


def cmd_ingest(args) -> int:
    path, how, skill = resolve_db(args.db)
    if args.config:
        cfg_path = os.path.abspath(os.path.expanduser(args.config))
    elif skill:
        cfg_path = os.path.join(skill, INGEST_CONFIG_NAME)
    else:
        cfg_path = None
    if not cfg_path or not os.path.isfile(cfg_path):
        raise CoalaError(f"sem configuração de ingestão ({cfg_path or 'skill local desconhecida'})",
                         "cria ingest.json na skill local (o instalador gera um predefinido) ou passa --config")
    cfg = load_ingest_config(cfg_path)
    if args.root:
        root = os.path.abspath(os.path.expanduser(args.root))
    elif skill:
        root = project_root_of(skill)
    elif os.path.basename(os.path.dirname(cfg_path)).endswith(SKILL_SUFFIX):
        root = project_root_of(os.path.dirname(cfg_path))
    else:
        root = os.getcwd()
    only = [s.strip() for s in args.only.split(",") if s.strip()] if args.only else None
    if args.dry_run:
        if os.path.isfile(path):
            conn, backend = open_ro(path), None
        else:
            conn, backend = sqlite3.connect(":memory:"), None
            conn.row_factory = sqlite3.Row
            init_schema(conn)
    else:
        conn, path, backend = connect_path(path)
    try:
        rep = run_ingest(conn, backend, cfg, root, only=only, pdf_pages=args.pdf_pages,
                         dry_run=args.dry_run, verbose=args.verbose)
    finally:
        conn.close()
    rep["db"] = path
    rep["config"] = cfg_path
    if args.json:
        emit(json.dumps(rep, ensure_ascii=False))
    else:
        tag = "  (dry-run)" if args.dry_run else ""
        lines = [f"Ingestão de {root} → {path}{tag}", f"  config: {cfg_path}"]
        for name, s in rep["rules"].items():
            lines.append(f"  {name:22s} ficheiros={s['files']:4d} segmentos={s['segments']:5d}"
                         f" novos={s['new']:5d} iguais={s['same']:5d} atualizados={s['updated']:4d}"
                         f" expirados={s['expired']:4d}" + (f" ignorados={s['skipped']}" if s["skipped"] else ""))
        lines += rep["details"]
        for r in rep["removed"]:
            lines.append(f"  removido: {r['path']} ({r['expired']} registos expirados)")
        if not args.dry_run:
            lines.append(f"  grafo: entidades novas={rep['graph']['entities_new']}"
                         f" arestas novas={rep['graph']['edges_new']}")
        t = rep["totals"]
        lines.append(f"TOTAL novos={t['new']} iguais={t['same']} atualizados={t['updated']}"
                     f" expirados={t['expired']}" + tag
                     + ("  → NO-OP (nada mudou)" if not args.dry_run and t['new'] + t['updated'] + t['expired'] == 0 else ""))
        for w in rep["warnings"]:
            lines.append(f"  aviso: {w}")
        emit("\n".join(lines))
    return 0


# --------------------------------------------------------------------- selftest
def _tiny_pdf(text: str) -> bytes:
    """PDF mínimo válido (1 página, Helvetica) para testar o modo `pdf` sem dependências."""
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>"]
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1")
    objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return out


def _selftest_v3(tmp: str, check, run_cmd) -> None:
    """Casos 44–58 (esquema v3). Só bases dentro de `tmp`; dados sintéticos (example.invalid, SYNTH)."""
    def jl(path, objs):
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("".join(json.dumps(o, ensure_ascii=False) + "\n" for o in objs))

    def imp(db, jsonl, **kw):
        a = dict(db=db, json=True, from_db=None, jsonl=jsonl, key_prefix=None, source_prefix=None, tags=None,
                 ids=None, all=False, with_graph=False, graph_entities=None, dry_run=False, add_tags=None,
                 origin=None)
        a.update(kw)
        return json.loads(run_cmd(cmd_import, **a)[1])

    def forget(db, *tags, dry=False):
        return json.loads(run_cmd(cmd_forget, db=db, json=True, tag=list(tags), dry_run=dry)[1])

    def add(db, content, **kw):
        a = dict(db=db, json=True, type="semantic", content=content, origin="agent", key=None, source=None,
                 tags=None, valid_from=None, valid_until=None, entities=None)
        a.update(kw)
        return json.loads(run_cmd(cmd_add, **a)[1])["id"]

    def export(db, out):
        run_cmd(cmd_export, db=db, format="jsonl", out=out)

    def fails(db):
        return [c for c in doctor_report(db, "--db", None, deep=True, freshness=False) if c["level"] == "FAIL"]

    def sha(path):
        return file_sha256(path) if os.path.isfile(path) else None

    def snap(db):
        """Estado comparável entre bases: tudo pelos ids por conteúdo (os ids inteiros são locais)."""
        c = sqlite3.connect(db)
        try:
            rows = c.execute("SELECT id, content_id, superseded_by, valid_until FROM memory_entries").fetchall()
            cid = {r[0]: r[1] for r in rows}
            now = now_iso()
            return {"all": sorted(cid.values()),
                    "active": sorted(r[1] for r in rows if r[2] is None and (r[3] is None or r[3] > now)),
                    "pairs": sorted((cid[r[0]], cid[r[2]]) for r in rows if r[2] is not None),
                    "ents": sorted((cid[r[0]], r[1]) for r in c.execute(
                        "SELECT l.entry_id, n.name FROM entry_entities l JOIN entity_nodes n ON n.id=l.entity_id")),
                    "edges": sorted(tuple(r) for r in c.execute(
                        "SELECT s.name, x.rel, d.name FROM entity_edges x JOIN entity_nodes s ON s.id=x.src"
                        " JOIN entity_nodes d ON d.id=x.dst"))}
        finally:
            c.close()

    def q(db, sql, *a):
        c = sqlite3.connect(db)
        try:
            return c.execute(sql, a).fetchall()
        finally:
            c.close()

    # 44. vetor fixo do contrato (o "ç" prende o ensure_ascii=False; campos ausentes contam como null)
    vec = {"schema": "sitemem/1", "key": "site/example.invalid/home/action/definir_endereço",
           "type": "procedural", "site": "example.invalid", "page": "home|busca", "kind": "action",
           "body": {"name": "definir_endereço", "params": {"cep": "{{cep}}"},
                    "steps": [{"op": "click", "sel": "[data-synth=cep]"},
                              {"op": "type", "sel": "[data-synth=cep]", "value": "{{cep}}"}],
                    "irreversible": False, "requires": []},
           "status": "hypothesis", "supersedes": [], "valid_from": "2026-09-29", "ttl_days": 30,
           "evidence": {"offline": True, "shadow": ">=3"}, "origin": "local"}
    promoted = dict(vec, status="validated", evidence={"live": ">=3", "walls_after": 0}, valid_from="2026-10-01",
                    ttl_days=90, supersedes=["0123456789abcdef"], origin="curated", runs=99, last_used="2026-10-02")
    minimal = {"schema": "sitemem/1", "key": "site/example.invalid/x/fact/lang", "type": "semantic",
               "body": {"lang": "pt-BR"}}
    check("44 id por conteúdo = contrato (vetor fixo; promover/status/evidence/datas/só-locais não mudam o id)",
          content_id(vec) == "522df88d33117fee" and content_id(promoted) == "522df88d33117fee"
          and entry_content_id("procedural", json.dumps(vec, ensure_ascii=False)) == "522df88d33117fee"
          and content_id(minimal) == "2fd0f7067870e6e8"
          and content_id(dict(vec, body={"name": "outro"})) != "522df88d33117fee",
          f"{content_id(vec)} {content_id(minimal)}")

    # 45. import --jsonl de registos canónicos: `id` da linha vale; supersedes refeito; tags derivadas
    src = os.path.join(tmp, "v3-src.sqlite")
    v2rec = dict(vec, body=dict(vec["body"], steps=vec["body"]["steps"][:1]), supersedes=["522df88d33117fee"])
    recs = [vec,
            {"schema": "sitemem/1", "id": "feedfacecafebeef", "key": "site/example.invalid/home/wait/lista",
             "type": "procedural", "site": "example.invalid", "page": "home", "kind": "wait",
             "body": {"list_min_items": {"sel": "[data-synth=lista]", "n": 1}}, "status": "validated"},
            v2rec,
            {"schema": "sitemem/1", "key": "site/outro.example.invalid/busca/fact/lang", "type": "semantic",
             "site": "outro.example.invalid", "page": "busca", "kind": "fact", "body": {"lang": "pt-BR"},
             "entities": ["SYNTH outro site"]}]
    f_recs = os.path.join(tmp, "v3-recs.jsonl")
    jl(f_recs, recs)
    r1 = imp(src, f_recs, add_tags="origin:curated")
    v2cid = content_id(v2rec)
    rows = {r[0]: r for r in q(src, "SELECT content_id, superseded_by, tags, id FROM memory_entries")}
    ok45 = (r1["imported"] == 4 and "feedfacecafebeef" in rows and "522df88d33117fee" in rows
            and rows["522df88d33117fee"][1] == rows[v2cid][3] and rows[v2cid][1] is None
            and {"site:example.invalid", "kind:action", "status:hypothesis", "origin:curated"}
            <= set(rows[v2cid][2].split(",")))
    check("45 import --jsonl: `id` da linha vale, supersedes refeito pelos ids, tags derivadas + --add-tags",
          ok45, str(r1))

    # 46–47. export → import numa base nova reproduz ativos, supersessões e grafo; reimportar = 0
    add(src, "SYNTH facto v1 da porta", key="synth-porta", tags="synth,api", entities="SYNTH api,SYNTH porta")
    add(src, "SYNTH facto v2 da porta", key="synth-porta", tags="synth,api")
    add(src, "SYNTH facto v3 da porta — com separador unicode", key="synth-porta", tags="synth,api")
    add(src, "SYNTH episódio sem chave", type="episodic", origin="system")
    add(src, "SYNTH facto já expirado", key="synth-exp", valid_until="2000-01-01T00:00:00+00:00")
    run_cmd(cmd_link, db=src, src="SYNTH outro site", rel="usa", dst="SYNTH lib")
    f_exp, dst = os.path.join(tmp, "v3-export.jsonl"), os.path.join(tmp, "v3-dst.sqlite")
    export(src, f_exp)
    r2 = imp(dst, f_exp)
    s_src, s_dst = snap(src), snap(dst)
    check("46 export → import numa base nova: mesmos ids, ativos, supersessões e grafo",
          s_src == s_dst and r2["imported"] == len(s_src["all"]) and len(s_src["pairs"]) == 3
          and len(s_src["edges"]) >= 2 and len(s_src["ents"]) >= 3,
          f"{r2} diff={[k for k in s_src if s_src[k] != s_dst[k]]}")
    r3 = imp(dst, f_exp)
    check("47 reimportar o mesmo JSONL cria 0 registos (e não mexe na base)",
          r3["imported"] == 0 and r3["already_present"] == len(s_src["all"]) and snap(dst) == s_dst, str(r3))

    # 48. export antigo (v2: ids inteiros, sem cid) — religa pelos inteiros do ficheiro, ids calculados
    legacy = []
    with open(f_exp, encoding="utf-8") as fh:
        for ln in fh.read().split("\n"):
            if ln.strip():
                o = json.loads(ln)
                o.pop("cid", None)
                o.pop("superseded_by_cid", None)
                o.pop("entities", None)
                legacy.append(o)
    f_leg, leg = os.path.join(tmp, "v3-legacy.jsonl"), os.path.join(tmp, "v3-leg.sqlite")
    jl(f_leg, legacy)
    r4 = imp(leg, f_leg)
    r5 = imp(leg, f_leg)
    s_leg = snap(leg)
    check("48 export antigo (ids inteiros) → mesmos ids por conteúdo e cadeias; reimport = 0",
          s_leg["all"] == s_src["all"] and s_leg["pairs"] == s_src["pairs"] and s_leg["active"] == s_src["active"]
          and r5["imported"] == 0, f"{r4} {r5}")

    # 49. --dry-run: mesmas contagens do import real e disco intocado (nem cria a base)
    dry_db = os.path.join(tmp, "v3-dry.sqlite")
    d1 = imp(dry_db, f_exp, dry_run=True)
    before = sha(dst)
    d2 = imp(dst, f_exp, dry_run=True)
    check("49 import --jsonl --dry-run conta como o real e não escreve",
          not os.path.exists(dry_db) and d1["imported"] == r2["imported"] and d2["imported"] == 0
          and sha(dst) == before, f"{d1} {d2}")

    # 50. tudo ou nada: uma linha inválida no fim → erro e a base fica como estava
    f_bad = os.path.join(tmp, "v3-bad.jsonl")
    jl(f_bad, [{"schema": "sitemem/1", "key": "site/example.invalid/z/fact/a", "type": "semantic",
                "site": "example.invalid", "body": {"a": 1}},
               {"schema": "sitemem/1", "key": "site/example.invalid/z/fact/b", "type": "nao-existe", "body": {}}])
    n_before = q(dst, "SELECT COUNT(*) FROM memory_entries")[0][0]
    try:
        imp(dst, f_bad)
        refused = False
    except UsageError as exc:
        refused = ":2:" in exc.what
    check("50 import --jsonl é tudo ou nada (linha inválida → erro com a linha, base intocada)",
          refused and q(dst, "SELECT COUNT(*) FROM memory_entries")[0][0] == n_before)

    # 51. LIKE com ESCAPE: `_`, `%` e `\` nas tags são literais
    esc = os.path.join(tmp, "v3-esc.sqlite")
    for t in ("site:a_b", "site:axb", "site:a%c", "site:azzc", "site:a\\b"):
        add(esc, f"SYNTH registo {t}", tags=t)
    ec = sqlite3.connect(esc)
    try:
        def n_tag(**kw):
            w, p = build_filter(**kw)
            return [r[0] for r in ec.execute("SELECT e.tags FROM memory_entries e WHERE 1=1" + w, p)]
        hits = (n_tag(tags=["site:a_b"]), n_tag(tags=["site:a%c"]), n_tag(tags=["site:a\\b"]),
                n_tag(any_tags=["site:a_b", "site:a%c"]), n_tag(tags=["site:a%"]))
    finally:
        ec.close()
    esc_dst = os.path.join(tmp, "v3-esc-dst.sqlite")
    dconn, _, dbk = connect_path(esc_dst)
    try:
        ri = import_entries(dconn, dbk, esc, tags=["site:a_b"])
    finally:
        dconn.close()
    fz = forget(esc, "site:a%c", dry=True)
    check("51 tag com `_`/`%`/`\\` casa só literalmente (filtros, import --tags, forget)",
          hits == (["site:a_b"], ["site:a%c"], ["site:a\\b"], ["site:a_b", "site:a%c"], [])
          and ri["imported"] == 1 and fz["entries"] == 1, f"{hits} import={ri['imported']} forget={fz['entries']}")

    # 52. forget --dry-run conta exatamente o que o real apaga e não escreve
    before = sha(dst)
    fd = forget(dst, "site:outro.example.invalid", dry=True)
    same_disk = sha(dst) == before
    fr = forget(dst, "site:outro.example.invalid")
    keys = ("entries", "chunks", "fts_rows", "vectors", "provenance", "entity_links", "entities", "edges")
    check("52 forget --dry-run = contagens do real e disco intocado",
          same_disk and fd["entries"] == 1 and all(fd[k] == fr[k] for k in keys), f"{fd} {fr}")

    # 53. forget apagou de todas as tabelas (principal, FTS, vetores, proveniência, grafo) e o doctor fica OK
    left = q(dst, "SELECT COUNT(*) FROM memory_entries WHERE tags LIKE '%site:outro.example.invalid%'")[0][0]
    fts = q(dst, "SELECT COUNT(*) FROM chunks_fts WHERE chunks_fts MATCH 'outro'")[0][0]
    orphan = q(dst, "SELECT (SELECT COUNT(*) FROM chunks c WHERE NOT EXISTS (SELECT 1 FROM memory_entries e"
                    " WHERE e.id=c.entry_id)) + (SELECT COUNT(*) FROM provenance p WHERE NOT EXISTS"
                    " (SELECT 1 FROM memory_entries e WHERE e.id=p.entry_id))")[0][0]
    ents = [r[0] for r in q(dst, "SELECT name FROM entity_nodes")]
    edges_left = q(dst, "SELECT COUNT(*) FROM entity_edges x JOIN entity_nodes n ON n.id=x.src"
                        " WHERE n.name='SYNTH outro site'")[0][0]
    check("53 forget apaga de todas as tabelas (principal, FTS, vetores, grafo) e o doctor fica OK",
          left == 0 and fts == 0 and orphan == 0 and "SYNTH outro site" not in ents and "SYNTH lib" in ents
          and edges_left == 0 and fr["entities"] == 1 and fr["edges"] == 1 and fr["vectors"] >= 1
          and not fails(dst), f"left={left} fts={fts} orphan={orphan} ents={ents} fails={fails(dst)}")

    # 54. sobreviventes religados: A → B(apagado) → C vira A → C; D → E(apagado, cabeça) deixa D sem sucessor
    ch = os.path.join(tmp, "v3-chain.sqlite")
    a = add(ch, "SYNTH A", key="synth-k")
    add(ch, "SYNTH B", key="synth-k", tags="site:apagar.example.invalid")
    c_id = add(ch, "SYNTH C", key="synth-k")
    d = add(ch, "SYNTH D", key="synth-h")
    add(ch, "SYNTH E", key="synth-h", tags="site:apagar.example.invalid")
    fc = forget(ch, "site:apagar.example.invalid")
    sup = dict(q(ch, "SELECT id, superseded_by FROM memory_entries"))
    d_exp = q(ch, "SELECT valid_until FROM memory_entries WHERE id=?", d)[0][0]
    check("54 forget religa a cadeia dos sobreviventes (sem ponteiros pendurados; doctor OK)",
          fc["entries"] == 2 and fc["relinked"] == 2 and sup.get(a) == c_id and sup.get(d, 0) is None
          and d_exp is not None and not fails(ch), f"{fc} {sup} fails={fails(ch)}")

    # 55. apagar de verdade: o texto esquecido já não está nos bytes do ficheiro (secure_delete + optimize + WAL)
    fb = os.path.join(tmp, "v3-bytes.sqlite")

    def disk(path):
        blob = b""
        for suffix in ("", "-wal"):
            if os.path.isfile(path + suffix):
                with open(path + suffix, "rb") as fh:
                    blob += fh.read()
        return blob

    add(fb, "SYNTH registo que fica")
    add(fb, "SYNTHSEGREDOFORGET marcador que tem de sumir", tags="site:bytes.example.invalid")
    pre = disk(fb)
    forget(fb, "site:bytes.example.invalid")
    blob = disk(fb)
    # `segredoforget` minúsculo só existe no índice FTS5 (termos com prefixo comprimido: `synth` + sufixo)
    gone = (b"SYNTHSEGREDOFORGET", b"segredoforget", b"marcador", b"sumir", b"bytes.example.invalid")
    check("55 forget não deixa o texto apagado no ficheiro (nem no WAL nem no índice FTS5)",
          all(g in pre for g in gone) and not any(g in blob for g in gone) and b"registo que fica" in blob,
          f"antes={[g for g in gone if g not in pre]} depois={[g for g in gone if g in blob]}")

    # 56. forget sem nada a esquecer: 0 e nenhuma base criada
    ghost = os.path.join(tmp, "v3-nao-existe.sqlite")
    f0 = forget(ghost, "site:x.example.invalid")
    f1 = forget(dst, "site:nada.example.invalid")
    check("56 forget sem alvo = 0 (e não cria a base)", f0["entries"] == 0 and not os.path.exists(ghost)
          and f1["entries"] == 0)

    # 57. migração v2 → v3 (aditiva): coluna + ids preenchidos + tabela de ligações; doctor OK
    old = os.path.join(tmp, "v3-migra.sqlite")
    ddl = re.sub(r"CREATE TABLE IF NOT EXISTS entry_entities \(.*?\);\n", "", SCHEMA_SQL, flags=re.S)
    ddl = re.sub(r"CREATE INDEX IF NOT EXISTS idx_entry_entities_entity[^\n]*\n", "", ddl)
    ddl = ddl.replace(",\n  content_id TEXT", "")
    oc = sqlite3.connect(old)
    oc.executescript(ddl)
    oc.execute("INSERT INTO memory_entries(memory_type, content, origin_class, supersession_key, recorded_at, tags)"
               " VALUES ('semantic', 'SYNTH facto da base v2', 'owner', 'synth-v2', '2026-01-01T00:00:00+00:00',"
               " 'site:example.invalid')")
    oc.execute("INSERT INTO chunks(entry_id, text) VALUES (1, 'SYNTH facto da base v2')")
    oc.execute("INSERT INTO chunks_fts(rowid, text) VALUES (1, 'SYNTH facto da base v2')")
    oc.execute("INSERT INTO provenance(entry_id, note) VALUES (1, 'base v2')")
    oc.execute("PRAGMA user_version = 2")
    oc.commit()
    oc.close()
    mc, _, _ = connect_path(old)
    try:
        mrow = mc.execute("SELECT * FROM memory_entries WHERE id=1").fetchone()
        mig = (mrow["content_id"] == row_content_id(mrow) and mc.execute("PRAGMA user_version").fetchone()[0] == 3
               and "entry_entities" in table_names(mc))
    finally:
        mc.close()
    check("57 migração v2 → v3 é aditiva (ids preenchidos, user_version 3) e o doctor fica OK",
          mig and not fails(old), str(fails(old)))

    # 58. o forget de um site importado da curada leva todas as versões dele
    f58 = forget(src, "site:example.invalid", dry=True)
    check("58 forget --tag site:x --dry-run conta todas as versões do site (ativas e suplantadas)",
          f58["entries"] == 3, str(f58))


def run_selftest() -> int:
    tmp = tempfile.mkdtemp(prefix="coala-selftest-")
    fake_db = os.path.join(tmp, "coala.sqlite")
    real_env = os.environ.get(DB_ENV)
    os.environ[DB_ENV] = fake_db
    results = []

    def check(name: str, cond: bool, detail: str = ""):
        results.append((name, bool(cond), detail))
        estado = "PASS" if cond else "FAIL"
        print(f"  [{estado}] {name}" + (f" — {detail}" if detail and not cond else ""))

    class _Args:
        pass

    def run_cmd(func, **kw):
        a = _Args()
        a.db, a.json = kw.pop("db", fake_db), kw.pop("json", False)
        for k, v in kw.items():
            setattr(a, k, v)
        buf = io.StringIO()
        old = sys.stdout
        sys.stdout = buf
        try:
            code = func(a)
        finally:
            sys.stdout = old
        return code, buf.getvalue()

    print(f"— coala selftest v{ENGINE_VERSION} (DB temporário: {fake_db}; nenhuma base real é tocada)")
    try:
        conn, path, backend = connect(fake_db)

        # 1. init idempotente + esquema
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')").fetchall()}
        needed = {"memory_entries", "entity_nodes", "entity_edges", "chunks",
                  "chunks_fts", "provenance"}
        check("01 init cria o esquema completo", needed <= tables, f"faltam: {needed - tables}")
        init_schema(conn)  # segunda vez: idempotente
        check("02 init é idempotente", True)

        # 3. inserção episódica
        ep_id, _ = insert_entry(conn, backend, "episodic",
                                "2026-09-25: deploy do worker de cache concluído sem erros.",
                                "agent", tags="deploy,cache", source="sessao-42")
        row = conn.execute("SELECT * FROM memory_entries WHERE id=?", (ep_id,)).fetchone()
        check("03 add episódico insere registo", row is not None and row["memory_type"] == "episodic")

        # 4. semântica com proveniência
        s1, _ = insert_entry(conn, backend, "semantic", "A API de pagamentos usa a porta 8080.",
                             "owner", key="api-porta", tags="api", source="decisao-reuniao")
        check("04 add semântico regista proveniência",
              conn.execute("SELECT 1 FROM provenance WHERE entry_id=?", (s1,)).fetchone() is not None)

        # 5. supersessão por chave
        s2, sup_ids = insert_entry(conn, backend, "semantic",
                                   "A API de pagamentos passou a usar a porta 8081.",
                                   "owner", key="api-porta", tags="api")
        old = conn.execute("SELECT * FROM memory_entries WHERE id=?", (s1,)).fetchone()
        check("05 supersessão por chave marca o anterior",
              old["superseded_by"] == s2 and old["valid_until"] is not None and s1 in sup_ids)
        check("06 proveniência da supersessão registada",
              "Suplantado" in (conn.execute("SELECT note FROM provenance WHERE entry_id=?",
                                            (s1,)).fetchone() or {"note": ""})["note"])

        # 7. supersede explícito (caminho do comando `supersede`)
        pr_id, _ = insert_entry(conn, backend, "procedural",
                                "Deploy: `wrangler deploy --env prod` a partir de /srv/api.",
                                "agent", tags="deploy,cloudflare")
        pr2_id, _ = insert_entry(conn, backend, "procedural",
                                 "Deploy: `wrangler deploy --env prod` (v2).",
                                 "agent", tags="deploy,cloudflare", source="revisao")
        fez = supersede_entry(conn, pr_id, pr2_id, now_iso())
        old_pr = conn.execute("SELECT * FROM memory_entries WHERE id=?", (pr_id,)).fetchone()
        check("07 supersede explícito substitui o facto",
              fez and old_pr["superseded_by"] == pr2_id)

        # 8. embeddings determinísticos e normalizados
        v1 = embed_text("memória persistente do agente")
        v2 = embed_text("memória persistente do agente")
        norm = sum(x * x for x in v1) ** 0.5
        check("08 embedding determinístico e normalizado",
              v1 == v2 and abs(norm - 1.0) < 1e-6 and len(v1) == EMBED_DIMS,
              f"norm={norm:.6f} dims={len(v1)}")

        # 9. busca vetorial isola o ranking (sem depender do FTS)
        va = embed_text("a api de pagamentos usa a porta")
        sa = cosine(va, embed_text("A API de pagamentos usa a porta 8080."))
        sb = cosine(va, embed_text("deploy do worker de cache"))
        check("09 busca vetorial ordena por semelhança de cosseno", sa > sb > 0,
              f"cos(alvo)={sa:.3f} cos(ruído)={sb:.3f}")
        vres = vector_search(conn, "api pagamentos porta", "", [], limit=5)
        check("10 vector_search devolve pares (eid, sim)",
              len(vres) >= 2 and all(0.0 <= s <= 1.0 for _, s in vres))

        # 11. busca FTS (léxico)
        fres = fts_search(conn, "porta 8081", "", [], limit=5)
        check("11 busca FTS5 encontra o facto ativo por termo",
              any(eid == s2 for eid, _ in fres), f"resultados: {[e for e, _ in fres]}")

        # 12. fusão RRF via SQL/janela
        fused = hybrid_search(conn, "api pagamentos porta 8081", "", [], limit=5)
        check("12 RRF funde FTS+vetor e ranqueia",
              len(fused) >= 1 and fused[0]["eid"] == s2 and fused[0]["rrf"] > 0,
              f"ordem: {[f['eid'] for f in fused]}")

        # 13. canal vetorial isolado (w_fts=0: só o cosseno decide)
        vec_only = hybrid_search(conn, "worker cache", "", [], limit=5, w_fts=0.0, w_vec=1.0)
        vec_order = [eid for eid, _ in vector_search(conn, "worker cache", "", [], limit=5)]
        check("13 canal vetorial decide sozinho quando w_fts=0",
              [f["eid"] for f in vec_only] == vec_order and ep_id in vec_order,
              f"rrf={[f['eid'] for f in vec_only]} vec={vec_order}")

        # 14. orçamento do recall (função real de seleção)
        long_ids = []
        for i in range(6):
            eid, _ = insert_entry(conn, backend, "episodic",
                                  f"nota de teste para o orçamento nº{i} " * 8, "system")
            long_ids.append(eid)
        qmarks = ",".join("?" * len(long_ids))
        lrows = {r["id"]: r for r in conn.execute(
            f"SELECT * FROM memory_entries WHERE id IN ({qmarks})", long_ids).fetchall()}
        chosen = budgeted_selection(long_ids, lrows, {}, budget=200, top=99, has_query=False)
        used = sum(est_tokens(r["content"]) + 40 for _, _, _, r in chosen)
        check("14 orçamento do recall respeita o limite de tokens",
              used <= 200 and 0 < len(chosen) < 6, f"used={used} escolhidos={len(chosen)}")

        # 15. redação de segredos
        secret_id, _ = insert_entry(conn, backend, "procedural",
                                    "Chave Stripe: sk-teste1234567890abcd; webhook whsec_abcdef123456;"
                                    " auth cfut_zzzzzzzzzzzz", "untrusted")
        row = conn.execute("SELECT content FROM memory_entries WHERE id=?", (secret_id,)).fetchone()
        masked = redact(row["content"])
        leak_free = all(tok not in masked for tok in
                        ("sk-teste1234567890abcd", "whsec_abcdef123456", "cfut_zzzzzzzzzzzz"))
        check("15 redação mascara segredos (sk-/whsec_/cfut_)",
              leak_free and masked.count("[REDACTADO]") == 3, masked[:80])

        # 16. redação aplicada na SAÍDA real dos comandos (export md/json/jsonl + recall)
        conn.commit()  # o export/recall abrem outra ligação: os dados têm de estar gravados
        saida = run_cmd(cmd_export, format="md", out=None)[1]
        saida += run_cmd(cmd_export, format="json", out=None)[1]
        saida += run_cmd(cmd_export, format="jsonl", out=None)[1]
        saida += run_cmd(cmd_recall, query="chave stripe webhook", type=None, tags=None, budget=2000,
                         top=20, include_superseded=True, include_expired=True)[1]
        secretos = ("sk-teste1234567890abcd", "whsec_abcdef123456", "cfut_zzzzzzzzzzzz")
        check("16 export(md/json/jsonl)+recall nunca imprimem segredos",
              all(s not in saida for s in secretos) and "[REDACTADO]" in saida,
              f"vazamento: {[s for s in secretos if s in saida]}")

        # 17. esquema v2: meta + ingest_sources + user_version
        uv = conn.execute("PRAGMA user_version").fetchone()[0]
        t2 = table_names(conn)
        check("17 esquema v2 (coala_meta, ingest_sources, user_version)",
              uv == SCHEMA_VERSION and {"coala_meta", "ingest_sources"} <= t2, f"uv={uv}")
        created = conn.execute("SELECT value FROM coala_meta WHERE key='created_at'").fetchone()[0]
        init_schema(conn)
        check("18 migração de esquema é idempotente (meta estável)",
              conn.execute("SELECT value FROM coala_meta WHERE key='created_at'").fetchone()[0] == created)

        # 19–22. resolução da base LOCAL (sem memória global)
        proj = os.path.join(tmp, "proj")
        skill = os.path.join(proj, ".agents", "demo" + SKILL_SUFFIX)
        os.makedirs(os.path.join(skill, "scripts"))
        os.makedirs(os.path.join(proj, "src", "deep"))
        with open(os.path.join(skill, MANIFEST_NAME), "w", encoding="utf-8") as fh:
            json.dump({"skill": os.path.basename(skill)}, fh)
        p1, h1, _ = resolve_db("/x/y.sqlite", env={DB_ENV: "/z.sqlite"})
        p2, h2, _ = resolve_db(None, env={DB_ENV: "/z.sqlite"})
        check("19 precedência --db > COALA_DB", p1 == "/x/y.sqlite" and p2 == "/z.sqlite" and "COALA_DB" in h2)
        p3, h3, sk3 = resolve_db(None, env={}, engine_file=os.path.join(skill, "scripts", "coala.py"))
        check("20 motor vendorizado usa a memória da sua skill",
              p3 == os.path.join(skill, "memory", "coala.sqlite") and sk3 == skill, p3)
        p4, h4, sk4 = resolve_db(None, cwd=os.path.join(proj, "src", "deep"), env={},
                                 engine_file=os.path.join(tmp, "coala.py"))
        check("21 descoberta sobe a partir do diretório atual", sk4 == skill and "descoberta" in h4, p4)
        try:
            resolve_db(None, cwd=tmp, env={}, engine_file=os.path.join(tmp, "coala.py"))
            sem_global = False
        except DependencyError as exc:
            sem_global = "Solução" in exc.render() and exc.exit_code == 3
        check("22 sem instalação → erro exit 3 (nunca base global)", sem_global)
        other = os.path.join(proj, ".agents", "outro" + SKILL_SUFFIX)
        os.makedirs(other)
        with open(os.path.join(other, MANIFEST_NAME), "w", encoding="utf-8") as fh:
            fh.write("{}")
        try:
            find_project_skill(proj)
            ambig = False
        except UsageError:
            ambig = True
        check("23 duas memórias no mesmo .agents → erro de ambiguidade", ambig)
        shutil.rmtree(other)

        # 24–25. motor vendorizado real (subprocesso, sem COALA_DB)
        vend = os.path.join(skill, "scripts", "coala.py")
        shutil.copy2(os.path.abspath(__file__), vend)
        env2 = {k: v for k, v in os.environ.items() if k != DB_ENV}
        out = subprocess.run([sys.executable, vend, "--json", "where"], capture_output=True,
                             text=True, env=env2, cwd=tmp, timeout=60)
        w = json.loads(out.stdout or "{}")
        check("24 `where` do motor vendorizado aponta para memory/ da skill",
              out.returncode == 0 and w.get("db") == os.path.join(skill, "memory", "coala.sqlite"),
              out.stderr[-200:])
        plain = os.path.join(tmp, "plain")
        os.makedirs(plain)
        shutil.copy2(os.path.abspath(__file__), os.path.join(plain, "coala.py"))
        out = subprocess.run([sys.executable, os.path.join(plain, "coala.py"), "stats"],
                             capture_output=True, text=True, env=env2, cwd=plain, timeout=60)
        check("25 CLI sem memória local → exit 3 com Erro/Solução",
              out.returncode == 3 and "Erro:" in out.stderr and "Solução:" in out.stderr,
              f"rc={out.returncode} {out.stderr[-160:]}")

        # 26–27. backup/restore
        conn.commit()
        bk, res_bk, n_bk = backup_db(fake_db)
        n_now = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
        check("26 backup consistente (0600, quick_check ok, mesma contagem)",
              res_bk == "ok" and n_bk == n_now and _mode(bk) == 0o600, f"{res_bk} {n_bk}/{n_now}")
        insert_entry(conn, backend, "episodic", "registo depois do backup", "agent")
        conn.commit()
        try:
            run_cmd(cmd_restore, from_db=bk, yes=False)
            guarded = False
        except UsageError:
            guarded = True
        run_cmd(cmd_restore, from_db=bk, yes=True)
        conn.close()
        conn, path, backend = connect(fake_db)
        n_after = conn.execute("SELECT COUNT(*) FROM memory_entries").fetchone()[0]
        pre = [f for f in os.listdir(os.path.dirname(bk)) if f.startswith("coala-pre-restore")]
        check("27 restore exige --yes, repõe o backup e guarda a base anterior",
              guarded and n_after == n_bk and len(pre) == 1, f"{n_after}/{n_bk} pre={pre}")

        # 28–30. import com cadeia de supersessão, idempotência e invariante por chave
        dest_db = os.path.join(tmp, "dest.sqlite")
        dconn, _, dbackend = connect_path(dest_db)
        r1 = import_entries(dconn, dbackend, fake_db, key_prefixes=["api-porta"])
        rows = dconn.execute("SELECT * FROM memory_entries WHERE supersession_key='api-porta'"
                             " ORDER BY recorded_at, id").fetchall()
        src_rows = conn.execute("SELECT * FROM memory_entries WHERE supersession_key='api-porta'"
                                " ORDER BY recorded_at, id").fetchall()
        chain_ok = (len(rows) == 2 and rows[0]["superseded_by"] == rows[1]["id"]
                    and rows[1]["superseded_by"] is None
                    and [r["recorded_at"] for r in rows] == [r["recorded_at"] for r in src_rows])
        pnote = dconn.execute("SELECT note FROM provenance WHERE entry_id=?", (rows[1]["id"],)).fetchone()[0]
        check("28 import preserva cadeia, recorded_at e proveniência",
              r1["imported"] == 2 and chain_ok and "Importado de" in pnote, str(r1))
        r2 = import_entries(dconn, dbackend, fake_db, key_prefixes=["api-porta"])
        check("29 import é idempotente (2.ª vez = 0 importados)",
              r2["imported"] == 0 and r2["already_present"] == 2, str(r2))
        insert_entry(dconn, dbackend, "semantic", "A API passou para a porta 9090.", "owner", key="k-conf")
        dconn.commit()
        insert_entry(conn, backend, "semantic", "Versão antiga do facto k-conf.", "owner", key="k-conf")
        conn.execute("UPDATE memory_entries SET recorded_at='2000-01-01T00:00:00+00:00'"
                     " WHERE supersession_key='k-conf'")
        conn.commit()
        r3 = import_entries(dconn, dbackend, fake_db, key_prefixes=["k-conf"])
        act = dconn.execute("SELECT content FROM memory_entries WHERE supersession_key='k-conf'"
                            " AND superseded_by IS NULL").fetchall()
        check("30 import mantém ≤1 versão ativa por chave (a mais recente ganha)",
              len(act) == 1 and "9090" in act[0][0] and r3["conflicts_resolved"] == 1, str(r3))
        dconn.close()

        # 31–37. ingestão guiada por ingest.json
        (proj_root, skill_dir) = (proj, skill)
        os.makedirs(os.path.join(proj_root, "docs", "sub"))
        with open(os.path.join(proj_root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# Projeto demo\n\nIntrodução ao projeto demo.\n\n## Arquitetura\n\nUsa SQLite local.\n")
        with open(os.path.join(proj_root, "docs", "sub", "guia.md"), "w", encoding="utf-8") as fh:
            fh.write("# Guia\n\nPasso um.\n\n## Detalhe\n\nPasso dois.\n")
        with open(os.path.join(proj_root, "run.sh"), "w", encoding="utf-8") as fh:
            fh.write("#!/bin/sh\necho ok\n")
        cfg = {"key_prefix": "proj", "path_prefix": "demo",
               "rules": [{"name": "readme", "include": ["README.md"], "mode": "markdown",
                          "type": "semantic", "origin": "agent", "tags": "docs,readme"},
                         {"name": "docs", "include": ["docs/**/*.md"], "mode": "markdown",
                          "type": "semantic", "origin": "agent", "tags": "docs,{dir},{stem}"},
                         {"name": "script", "include": ["*.sh"], "mode": "whole",
                          "type": "procedural", "origin": "agent", "tags": "script"}],
               "graph": {"entities": [{"name": "demo", "kind": "projeto"}],
                         "edges": [["demo", "usa", "SQLite"]]}}
        pdf_ok = bool(shutil.which("pdftotext"))
        if pdf_ok:
            with open(os.path.join(proj_root, "docs", "artigo.pdf"), "wb") as fh:
                fh.write(_tiny_pdf("Hello CoALA memory"))
            cfg["rules"].append({"name": "pdfs", "include": ["docs/**/*.pdf"], "mode": "pdf",
                                 "type": "semantic", "origin": "untrusted", "tags": "pdf,doc:{stem}"})
        cfg_path = os.path.join(skill_dir, INGEST_CONFIG_NAME)
        with open(cfg_path, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh)
        idb = os.path.join(skill_dir, "memory", "coala.sqlite")
        iconn, _, ibackend = connect_path(idb)
        loaded = load_ingest_config(cfg_path)
        rep1 = run_ingest(iconn, ibackend, loaded, proj_root)
        keys = {r[0] for r in iconn.execute("SELECT supersession_key FROM memory_entries")}
        whole = iconn.execute("SELECT tags, memory_type FROM memory_entries WHERE supersession_key='proj/demo/run.sh'").fetchone()
        guia = iconn.execute("SELECT tags FROM memory_entries WHERE supersession_key='proj/demo/docs/sub/guia.md#000'").fetchone()
        check("31 ingest cria segmentos com chaves estáveis e tags {dir}/{stem}",
              rep1["totals"]["new"] >= 5 and "proj/demo/README.md#000" in keys and whole is not None
              and whole[1] == "procedural" and guia is not None and "sub" in guia[0] and "guia" in guia[0],
              str(rep1["totals"]))
        if pdf_ok:
            prow = iconn.execute("SELECT content, origin_class, tags FROM memory_entries"
                                 " WHERE supersession_key='proj/demo/docs/artigo.pdf#0000'").fetchone()
            check("32 modo pdf (pdftotext) marca página e origem untrusted",
                  prow is not None and "pág. 1" in prow[0] and "Hello" in prow[0]
                  and prow[1] == "untrusted" and "doc:artigo" in prow[2], str(prow and prow[0][:60]))
        else:
            segs = segment_file(os.path.join(proj_root, "README.md"), "demo/x.pdf",
                                {"name": "pdfs", "mode": "pdf"}, warn=rep1["warnings"].append)
            check("32 sem pdftotext a regra pdf é ignorada com aviso (degradação graciosa)",
                  segs is None and any("pdftotext" in w for w in rep1["warnings"]))
        rep2 = run_ingest(iconn, ibackend, loaded, proj_root)
        check("33 re-ingestão sem mudanças é NO-OP",
              rep2["totals"]["new"] == 0 and rep2["totals"]["updated"] == 0
              and rep2["totals"]["expired"] == 0 and rep2["totals"]["same"] == rep1["totals"]["new"],
              str(rep2["totals"]))
        with open(os.path.join(proj_root, "README.md"), "w", encoding="utf-8") as fh:
            fh.write("# Projeto demo\n\nIntrodução ao projeto demo (revista).\n")
        rep3 = run_ingest(iconn, ibackend, loaded, proj_root)
        old_ver = iconn.execute("SELECT superseded_by FROM memory_entries WHERE supersession_key="
                                "'proj/demo/README.md#000' ORDER BY id LIMIT 1").fetchone()[0]
        check("34 ficheiro alterado → supersessão; segmentos que sumiram → expiram",
              rep3["totals"]["updated"] == 1 and rep3["totals"]["expired"] == 1 and old_ver is not None,
              str(rep3["totals"]))
        os.remove(os.path.join(proj_root, "run.sh"))
        rep4 = run_ingest(iconn, ibackend, loaded, proj_root)
        gone = iconn.execute("SELECT valid_until, superseded_by FROM memory_entries"
                             " WHERE supersession_key='proj/demo/run.sh'").fetchone()
        check("35 ficheiro removido → registos expiram (nunca apagados)",
              len(rep4["removed"]) == 1 and gone is not None and gone[0] is not None and gone[1] is None,
              str(rep4["removed"]))
        g = iconn.execute("SELECT COUNT(*) FROM entity_edges e JOIN entity_nodes s ON s.id=e.src"
                          " WHERE s.name='demo'").fetchone()[0]
        check("36 grafo declarado no ingest.json é aplicado de forma idempotente", g == 1)
        loaded["rules"][1]["origin"] = "owner"            # mudar só a proveniência de uma regra
        rep5 = run_ingest(iconn, ibackend, loaded, proj_root)
        orig = iconn.execute("SELECT origin_class FROM memory_entries WHERE supersession_key="
                             "'proj/demo/docs/sub/guia.md#000' AND superseded_by IS NULL").fetchone()[0]
        check("37 mudar origem/tags de uma regra gera nova versão (supersessão), não reescrita",
              rep5["totals"]["updated"] == 2 and rep5["totals"]["new"] == 0 and orig == "owner",
              str(rep5["totals"]))
        iconn.close()
        _, out1 = run_cmd(cmd_export, db=idb, format="jsonl", out=None)
        _, out2 = run_cmd(cmd_export, db=idb, format="jsonl", out=None)
        check("38 export jsonl canónico é determinístico", out1 == out2 and out1.count("\n") >= 5)

        # 38–39. doctor
        code, dout = run_cmd(cmd_doctor, db=idb, deep=True, no_freshness=False)
        dj = json.loads(run_cmd(cmd_doctor, db=idb, json=True, deep=False, no_freshness=False)[1])
        check("39 doctor numa base saudável não tem FAIL e mede a frescura",
              code == 0 and dj["fails"] == 0 and any(c["check"].startswith("frescura") for c in dj["checks"]),
              str([c for c in dj["checks"] if c["level"] == "FAIL"]))
        bad = sqlite3.connect(idb)
        bad.execute("INSERT INTO memory_entries(memory_type, content, origin_class, supersession_key,"
                    " recorded_at) VALUES ('semantic','dup','agent','proj/demo/README.md#000', ?)", (now_iso(),))
        bad.commit()
        bad.close()
        try:
            run_cmd(cmd_doctor, db=idb, deep=False, no_freshness=True)
            caught = False
        except CoalaError:
            caught = True
        check("40 doctor deteta >1 versão ativa por chave (FAIL, exit 1)", caught)

        # 41. filtros de tags: --tags (AND) vs --any-tags (OR)
        w_and, p_and = build_filter(tags=["api", "cache"])
        w_or, p_or = build_filter(any_tags=["api", "cache"])
        n_and = conn.execute("SELECT COUNT(*) FROM memory_entries e WHERE 1=1" + w_and, p_and).fetchone()[0]
        n_or = conn.execute("SELECT COUNT(*) FROM memory_entries e WHERE 1=1" + w_or, p_or).fetchone()[0]
        check("41 --tags exige todas (AND) e --any-tags basta uma (OR)", n_and == 0 and n_or >= 2,
              f"and={n_and} or={n_or}")

        # 44–58. esquema v3: ids por conteúdo, import --jsonl, forget, LIKE com ESCAPE, migração v2→v3
        _selftest_v3(tmp, check, run_cmd)

        conn.commit()
        conn.close()

        # 42. isolamento: o DB temporário existe e a limpeza remove tudo
        check("42 DB temporário isolado de qualquer base real", os.path.exists(fake_db))
    except Exception as exc:  # pragma: no cover - rede de segurança do selftest
        check("erro inesperado no selftest", False, repr(exc))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        if real_env is None:
            os.environ.pop(DB_ENV, None)
        else:
            os.environ[DB_ENV] = real_env
        check("43 limpeza completa da fixture (nada fica em /tmp)", not os.path.exists(tmp))

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print(f"\nSELFTEST: {passed}/{total} PASS" + ("" if passed == total else " — HÁ FALHAS"))
    return 0 if passed == total else 1


# ------------------------------------------------------------------------ CLI
class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise UsageError(f"uso inválido ({message})",
                         "executa `python3 scripts/coala.py --help` para ver a sintaxe correta")


def build_parser() -> argparse.ArgumentParser:
    p = _Parser(
        prog="coala.py",
        description="Motor de memória persistente CoALA (SQLite) LOCAL por projeto: episódica, "
                    "semântica, procedimental + working memory orçamentada. Sem memória global.",
        epilog="Flags globais (em qualquer posição): --json · --db <caminho>. "
               "Env: COALA_DB, COALA_RRF_W_FTS, COALA_RRF_W_VEC. "
               f"Instalar num projeto: {INSTALL_HINT}")
    p.add_argument("--selftest", action="store_true",
                   help="corre testes determinísticos OFFLINE em DB temporário")
    sub = p.add_subparsers(dest="cmd", metavar="<comando>")

    sp = sub.add_parser("init", help="cria/migra o esquema (idempotente, aditivo)")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("where", help="mostra que base local é usada e porquê")
    sp.set_defaults(func=cmd_where)

    sp = sub.add_parser("add", help="insere um registo de memória")
    sp.add_argument("--type", required=True, choices=list(MEMORY_TYPES),
                    help="tipo de memória CoALA")
    sp.add_argument("--content", required=True, help="texto do conhecimento")
    sp.add_argument("--origin", default="agent", choices=list(ORIGINS),
                    help="classe de proveniência (web → use untrusted)")
    sp.add_argument("--key", dest="key", default=None,
                    help="chave de supersessão: repetir a chave suplanta o registo ativo anterior")
    sp.add_argument("--source", default=None, help="URL/caminho/conversa de origem")
    sp.add_argument("--tags", default=None, help="tags separadas por vírgula")
    sp.add_argument("--valid-from", default=None, help="quando o facto passou a valer (ISO)")
    sp.add_argument("--valid-until", default=None, help="quando deixa de valer (ISO; NULL=ainda válido)")
    sp.add_argument("--entities", default=None,
                    help="entidades separadas por vírgula a criar/ligar no grafo")
    sp.set_defaults(func=cmd_add)

    sp = sub.add_parser("search", help="busca híbrida FTS5+vetorial com fusão RRF")
    sp.add_argument("query", help="consulta em linguagem natural")
    sp.add_argument("--type", default=None, choices=list(MEMORY_TYPES))
    sp.add_argument("--tags", default=None, help="filtra por tags (CSV): TODAS têm de estar presentes")
    sp.add_argument("--any-tags", dest="any_tags", default=None, help="filtra por tags (CSV): basta UMA")
    sp.add_argument("--limit", type=int, default=10, help="máximo de resultados (predef: 10)")
    sp.add_argument("--w-fts", dest="w_fts", type=float, default=None, help="peso RRF do canal FTS")
    sp.add_argument("--w-vec", dest="w_vec", type=float, default=None, help="peso RRF do canal vetorial")
    sp.add_argument("--include-superseded", action="store_true", help="inclui registos suplantados")
    sp.add_argument("--include-expired", action="store_true", help="inclui registos expirados")
    sp.set_defaults(func=cmd_search)

    sp = sub.add_parser("recall", help="materializa working memory orçamentada para um prompt")
    sp.add_argument("query", nargs="?", default=None, help="consulta opcional de relevância")
    sp.add_argument("--type", default=None, choices=list(MEMORY_TYPES))
    sp.add_argument("--tags", default=None, help="filtra por tags (CSV): TODAS têm de estar presentes")
    sp.add_argument("--any-tags", dest="any_tags", default=None, help="filtra por tags (CSV): basta UMA")
    sp.add_argument("--budget", type=int, default=DEFAULT_BUDGET,
                    help="orçamento em tokens estimados (predef: 2000)")
    sp.add_argument("--top", type=int, default=12, help="máximo de excertos (predef: 12)")
    sp.add_argument("--include-superseded", action="store_true")
    sp.add_argument("--include-expired", action="store_true")
    sp.set_defaults(func=cmd_recall)

    sp = sub.add_parser("graph", help="travessia de grafo de entidades (CTE recursiva)")
    sp.add_argument("entity", help="nome da entidade raiz")
    sp.add_argument("--depth", type=int, default=2, help="profundidade máxima (predef: 2)")
    sp.set_defaults(func=cmd_graph)

    sp = sub.add_parser("link", help="cria uma aresta explícita entre entidades")
    sp.add_argument("src", help="entidade de origem")
    sp.add_argument("rel", help="relação (ex.: usa, depende_de, substitui)")
    sp.add_argument("dst", help="entidade de destino")
    sp.set_defaults(func=cmd_link)

    sp = sub.add_parser("supersede", help="substitui explicitamente um facto por outro")
    sp.add_argument("id", type=int, help="id do registo a suplantar")
    sp.add_argument("--content", required=True, help="novo conteúdo")
    sp.add_argument("--origin", default=None, choices=list(ORIGINS))
    sp.add_argument("--source", default=None)
    sp.add_argument("--tags", default=None)
    sp.add_argument("--valid-from", default=None)
    sp.add_argument("--valid-until", default=None)
    sp.set_defaults(func=cmd_supersede)

    sp = sub.add_parser("stats", help="contagens por tipo/proveniência/validade + tamanho do DB")
    sp.set_defaults(func=cmd_stats)

    sp = sub.add_parser("export", help="dump para revisão humana ou git (segredos mascarados)")
    sp.add_argument("--format", default="md", choices=["md", "json", "jsonl"],
                    help="md (humano) · json · jsonl (canónico, determinístico, p/ diff git)")
    sp.add_argument("--out", default=None, help="escreve num ficheiro em vez do stdout (sem corte de 48 KB)")
    sp.set_defaults(func=cmd_export)

    sp = sub.add_parser("doctor", help="saúde da base: esquema, FTS5, integridade, contagens, frescura")
    sp.add_argument("--deep", action="store_true", help="integrity_check completo (mais lento)")
    sp.add_argument("--no-freshness", dest="no_freshness", action="store_true",
                    help="não compara o material do projeto com a memória")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser("backup", help="snapshot consistente (API de backup) em memory/backups/")
    sp.add_argument("--out", default=None, help="caminho do ficheiro de backup (nunca sobrescreve)")
    sp.set_defaults(func=cmd_backup)

    sp = sub.add_parser("restore", help="repõe a base a partir de um backup (faz backup da atual antes)")
    sp.add_argument("--from", dest="from_db", required=True, help="ficheiro de backup")
    sp.add_argument("--yes", action="store_true", help="confirma a substituição")
    sp.set_defaults(func=cmd_restore)

    sp = sub.add_parser("import", help="copia registos de outra base CoALA (--from) ou de um JSONL (--jsonl),"
                                       " preservando histórico; idempotente")
    sp.add_argument("--from", dest="from_db", default=None, help="base de origem (aberta só-leitura)")
    sp.add_argument("--jsonl", default=None,
                    help="ficheiro JSONL (`-` = stdin): linhas do `export --format jsonl` ou registos canónicos"
                         " (schema/key/type/body); id por conteúdo, supersessão pelos ids; tudo ou nada")
    sp.add_argument("--add-tags", dest="add_tags", default=None,
                    help="só --jsonl: tags (CSV) acrescentadas a cada registo importado (ex.: origin:curated)")
    sp.add_argument("--origin", default=None, choices=list(ORIGINS),
                    help="só --jsonl: origem dos registos canónicos (predef: agent; as linhas do export trazem a sua)")
    sp.add_argument("--key-prefix", action="append", default=None, help="prefixo de supersession_key (repetível)")
    sp.add_argument("--source-prefix", action="append", default=None, help="prefixo de source (repetível)")
    sp.add_argument("--tags", default=None, help="tags (CSV; basta uma)")
    sp.add_argument("--ids", default=None, help="ids e intervalos, ex.: 5-20,23")
    sp.add_argument("--all", action="store_true", help="todos os registos")
    sp.add_argument("--with-graph", dest="with_graph", action="store_true", help="copia também entidades/arestas")
    sp.add_argument("--graph-entities", dest="graph_entities", default=None,
                    help="restringe o grafo copiado a estas entidades (CSV)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true", help="só conta, não escreve")
    sp.set_defaults(func=cmd_import)

    sp = sub.add_parser("forget", help="APAGA de verdade os registos com a tag (todas as versões e tabelas;"
                                       " irreversível — experimenta antes com --dry-run)")
    sp.add_argument("--tag", action="append", default=None,
                    help="tag exata (repetível: basta UMA); `%%`/`_` são literais, sem curingas")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true",
                    help="conta o que apagaria (numa cópia em memória), sem escrever")
    sp.set_defaults(func=cmd_forget)

    sp = sub.add_parser("ingest", help="ingere o material do projeto segundo o ingest.json (idempotente)")
    sp.add_argument("--config", default=None, help="caminho do ingest.json (predef: o da skill local)")
    sp.add_argument("--root", default=None, help="raiz do projeto (predef: a da skill local)")
    sp.add_argument("--only", default=None, help="regras a ingerir (CSV)")
    sp.add_argument("--pdf-pages", dest="pdf_pages", type=int, default=0,
                    help="limita páginas por PDF (0 = todas; útil em testes)")
    sp.add_argument("--dry-run", dest="dry_run", action="store_true", help="compara com a base sem escrever")
    sp.add_argument("--verbose", action="store_true", help="detalhe por ficheiro")
    sp.set_defaults(func=cmd_ingest)
    return p


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    json_mode = False
    db_override = None
    selftest = False
    rest = []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--json":
            json_mode = True
        elif a == "--selftest":
            selftest = True
        elif a == "--db":
            i += 1
            if i >= len(argv):
                raise UsageError("--db exige um caminho",
                                 "usa `--db /caminho/coala.sqlite` ou a env COALA_DB")
            db_override = argv[i]
        elif a.startswith("--db="):
            db_override = a.split("=", 1)[1]
        else:
            rest.append(a)
        i += 1

    if selftest:
        return run_selftest()

    parser = build_parser()
    args = parser.parse_args(rest)
    args.json = json_mode
    args.db = db_override

    if not getattr(args, "cmd", None):
        parser.print_help()
        raise UsageError("nenhum comando indicado",
                         "usa `where`, `init`, `add`, `search`, `recall`, `graph`, `supersede`, `stats`,"
                         " `export`, `doctor`, `backup`, `restore`, `import`, `forget`, `ingest` ou `--selftest`")
    return args.func(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CoalaError as exc:
        sys.stderr.write(exc.render() + "\n")
        sys.exit(exc.exit_code)
    except KeyboardInterrupt:
        sys.stderr.write("Erro: interrompido pelo utilizador — Solução: reexecuta o comando\n")
        sys.exit(1)
    except BrokenPipeError:
        sys.exit(1)
    except sqlite3.Error as exc:
        sys.stderr.write(f"Erro: falha de SQLite ({exc}) — "
                         "Solução: verifica o estado do ficheiro DB e as permissões\n")
        sys.exit(1)
