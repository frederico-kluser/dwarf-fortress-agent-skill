"""Guardas da configuração do repositório: import do pacote + higiene do git.

Este ficheiro é deliberadamente **independente das outras peças de teste**: não
usa `conftest.py` nenhum, não importa `pytest` para as asserções (só `tomllib`,
`subprocess`, `json`, `pathlib` e `os` da stdlib) e corre em menos de um segundo.

Trava a regressão que já enganou o verificador uma vez. O pacote é *src-layout*
(`src/system_one_dwarf/`) e **não está instalado** no venv; quem o torna
importável sob pytest é a chave `pythonpath = ["src"]` de
`[tool.pytest.ini_options]` no `pyproject.toml`. Hoje isso está **mascarado** por
duas béquulas:

* os `sys.path.insert(…/"src")` dentro de `tests/*/conftest.py` (cada peça
  remenda o caminho antes de importar o pacote);
* o `.pth` que o venv tem em
  `.venv/lib/python3.14/site-packages/system_one_dwarf_src.pth`, que aponta
  para `<repo>/src` e é processado pelo `site` em qualquer arranque do Python.

Apagar `pythonpath` do `pyproject.toml` não parte nenhuma das duas — parte a CI
limpa, outro venv e qualquer máquina que não tenha o `.pth`.

Por isso a prova é feita **fora do pytest**, num subprocesso `python -S -c`:

* `-S` desliga o módulo `site` — logo não há `.pth`, nem `sitecustomize`, nem
  `site-packages` no `sys.path` do filho (é a béquula do venv, neutralizada);
* a única coisa que entra no `sys.path` do filho é o `PYTHONPATH` construído a
  partir do `pythonpath` do `pyproject.toml`, com as entradas resolvidas contra
  a raiz do repositório — exatamente o que o pytest faria a partir do `rootdir`;
* o `conftest.py` das peças nunca é carregado, porque não há pytest nenhum
  neste subprocesso.

O controlo negativo (`PYTHONPATH` ausente ⇒ a importação **tem** de falhar) é o
que torna o teste *load-bearing*: sem ele, um `system_one_dwarf/` na raiz do
repositório faria o teste passar pela razão errada. Esse controlo corre com
`cwd` = raiz do repositório, que é **mais permissivo** do que o pytest (que só
acrescenta a pasta do ficheiro de teste, `tests/`): se a importação falha aqui,
falha com certeza sob pytest sem `pythonpath`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT = REPO_ROOT / "pyproject.toml"
GITIGNORE = REPO_ROOT / ".gitignore"
PACKAGE_DIR = REPO_ROOT / "src" / "system_one_dwarf"

#: Regras de higiene que não podem voltar a desaparecer (ver `git log`: `*.cover`
#: e `campanhas/` foram decisões explícitas; `.venv/` nunca é versionado).
REQUIRED_IGNORES = ("*.cover", "campanhas/", ".venv/")

_PROBE_TIMEOUT_S = 20
_PROBE_MARKER = "SOD_PROBE="

#: Importa o pacote num interpretador cru e devolve um JSON de uma linha.
#: `sys.dont_write_bytecode` fica ligado **antes** do import (nada de `__pycache__`
#: novo na árvore) e `sys.path` vem no payload para se provar o isolamento.
_PROBE = (
    "import json, os, sys;"
    "sys.dont_write_bytecode = True;"
    "import system_one_dwarf as mod;"
    "print('" + _PROBE_MARKER + "' + json.dumps({"
    "'file': os.path.realpath(mod.__file__),"
    "'version': getattr(mod, '__version__', None),"
    "'sys_path': list(sys.path),"
    "}))"
)


def _pytest_ini() -> dict:
    """A tabela `[tool.pytest.ini_options]` do `pyproject.toml`, via `tomllib`."""
    with PYPROJECT.open("rb") as handle:
        data = tomllib.load(handle)
    assert "pytest" in data.get("tool", {}), (
        "o pyproject.toml deixou de ter [tool.pytest.ini_options]: sem essa tabela o pytest "
        "não sabe onde está o pacote (src-layout) nem onde estão os testes"
    )
    ini = data["tool"]["pytest"].get("ini_options")
    assert ini is not None, "[tool.pytest] existe mas sem a sub-tabela `ini_options`"
    return ini


def _run_probe(pythonpath_entries: list[str] | None) -> subprocess.CompletedProcess[str]:
    """Corre o import num `python -S` hermético (sem `site`, sem `conftest`).

    `pythonpath_entries=None` omite o `PYTHONPATH` do ambiente do filho — é o
    cenário "alguém apagou a chave do pyproject".
    """
    env = {
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
    }
    if pythonpath_entries is not None:
        roots = [(REPO_ROOT / entry).resolve() for entry in pythonpath_entries]
        env["PYTHONPATH"] = os.pathsep.join(str(root) for root in roots)
    return subprocess.run(
        [sys.executable, "-S", "-c", _PROBE],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=_PROBE_TIMEOUT_S,
    )


def _probe_payload(proc: subprocess.CompletedProcess[str]) -> dict:
    for line in proc.stdout.splitlines():
        if line.startswith(_PROBE_MARKER):
            return json.loads(line[len(_PROBE_MARKER):])
    raise AssertionError(
        "o subprocesso de sondagem não devolveu o payload esperado\n"
        f"returncode={proc.returncode}\nstdout={proc.stdout!r}\nstderr={proc.stderr!r}"
    )


# --------------------------------------------------------------------------- #
# 1. A configuração do pytest
# --------------------------------------------------------------------------- #


def test_pyproject_declares_pytest_pythonpath_and_testpaths():
    ini = _pytest_ini()

    assert "pythonpath" in ini, (
        "[tool.pytest.ini_options] não declara `pythonpath` — o pacote é src-layout e não está "
        "instalado no venv: sem esta chave só importa por causa dos shims de tests/*/conftest.py "
        "e do .pth do venv (era exatamente esta a misconfiguração sinalizada)"
    )
    assert "testpaths" in ini, (
        "[tool.pytest.ini_options] não declara `testpaths` — sem esta chave a descoberta de "
        "testes deixa de estar limitada a `tests/`"
    )

    pythonpath = ini["pythonpath"]
    testpaths = ini["testpaths"]
    assert isinstance(pythonpath, list) and "src" in pythonpath, (
        f"`pythonpath` tem de incluir \"src\" (valor atual: {pythonpath!r})"
    )
    assert isinstance(testpaths, list) and "tests" in testpaths, (
        f"`testpaths` tem de incluir \"tests\" (valor atual: {testpaths!r})"
    )

    # As entradas são relativas à raiz do repositório (é assim que o pytest as
    # resolve a partir do rootdir) e têm de apontar para pastas que existem.
    for entry in pythonpath:
        assert (REPO_ROOT / entry).is_dir(), f"`pythonpath` aponta para algo que não existe: {entry!r}"
    for entry in testpaths:
        assert (REPO_ROOT / entry).is_dir(), f"`testpaths` aponta para algo que não existe: {entry!r}"
    assert (PACKAGE_DIR / "__init__.py").is_file(), f"pacote em falta: {PACKAGE_DIR}"


# --------------------------------------------------------------------------- #
# 2. O import funciona só com o `pythonpath` do pyproject (sem shims, sem .pth)
# --------------------------------------------------------------------------- #


def test_import_works_from_the_pyproject_pythonpath_alone():
    """`import system_one_dwarf` num `python -S` cujo `sys.path` só tem o `pythonpath`."""
    ini = _pytest_ini()
    assert "pythonpath" in ini, "sem `pythonpath` não há nada para provar (ver o teste 1)"

    proc = _run_probe(ini["pythonpath"])
    assert proc.returncode == 0, (
        "`import system_one_dwarf` falhou num interpretador isolado alimentado apenas pelo "
        f"`pythonpath` do pyproject.toml ({ini['pythonpath']!r})\n"
        f"stderr={proc.stderr!r}"
    )

    payload = _probe_payload(proc)
    assert Path(payload["file"]).parent == PACKAGE_DIR, (
        f"o pacote importado não veio de {PACKAGE_DIR} (veio de {payload['file']}) — algum "
        "caminho escondido está a ganhar ao `pythonpath`"
    )
    assert not [entry for entry in payload["sys_path"] if "site-packages" in entry], (
        "a sondagem não está isolada: `site-packages` apareceu no sys.path do filho, logo o "
        f"`.pth` do venv voltou a mascarar o teste ({payload['sys_path']!r})"
    )


def test_the_probe_is_load_bearing_without_pythonpath_the_import_fails():
    """Controlo negativo: sem `pythonpath`, o mesmo import **tem** de falhar.

    É este teste que faz o anterior valer: se um dia aparecer um
    `system_one_dwarf/` na raiz do repositório (ou uma instalação global
    equivalente), o positivo passaria sem a chave do pyproject e a regressão
    voltaria a passar despercebida.
    """
    proc = _run_probe(None)
    assert proc.returncode != 0, (
        "`import system_one_dwarf` passou **sem** `pythonpath` nenhum: o teste positivo deixou "
        "de provar o que diz provar (há um `system_one_dwarf/` na raiz do repositório ou o "
        f"pacote passou a estar instalado)\nstdout={proc.stdout!r}"
    )
    assert "system_one_dwarf" in (proc.stdout + proc.stderr), (
        "a sondagem falhou por outra razão que não a importação do pacote: "
        f"{proc.stderr!r}"
    )


# --------------------------------------------------------------------------- #
# 3. Higiene do git
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("pattern", REQUIRED_IGNORES)
def test_gitignore_keeps_the_hygiene_rule(pattern: str):
    lines = {
        line.strip()
        for line in GITIGNORE.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    assert pattern in lines, (
        f"`.gitignore` perdeu a regra {pattern!r} (decisão de higiene do repositório); "
        f"regras atuais: {sorted(lines)!r}"
    )
