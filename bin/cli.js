#!/usr/bin/env node
'use strict';
/*
 * df-skills — instala as Dwarf Fortress Agent Skills no diretório atual.
 *
 * Copia as 16 skills (13 da wiki v50 + ponte DFHack, copiloto adventure e central) para o local de
 * descoberta do agente de código escolhido, gera a "cola" específica de cada
 * agente (.cursor/rules, AGENTS.md, INSTRUCTIONS.md) e mostra como referenciar
 * a skill inicial. Zero dependências — só Node >= 16.
 */

const fs = require('fs');
const path = require('path');
const os = require('os');
const readline = require('readline');

const pkg = require(path.join(__dirname, '..', 'package.json'));

/* ───────── cores ───────── */
const tty = process.stdout.isTTY;
const paint = (n) => (s) => (tty ? `\x1b[${n}m${s}\x1b[0m` : String(s));
const bold = paint('1');
const dim = paint('2');
const cyan = paint('36');
const green = paint('32');
const yellow = paint('33');
const red = paint('31');

function die(msg) {
  console.error(`${red('✖')} ${msg}`);
  process.exit(1);
}

/* ───────── argumentos ───────── */
const flags = { agent: null, global: false, force: false };
{
  const argv = process.argv.slice(2);
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--agent') flags.agent = argv[++i];
    else if (a.startsWith('--agent=')) flags.agent = a.slice('--agent='.length);
    else if (a === '--global' || a === '-g') flags.global = true;
    else if (a === '--force' || a === '-f') flags.force = true;
    else if (a === '--version' || a === '-v') { console.log(pkg.version); process.exit(0); }
    else if (a === '--help' || a === '-h') { help(); process.exit(0); }
    else if (a === 'init') { /* alias do comando padrão */ }
    else { console.error(red(`argumento desconhecido: ${a}\n`)); help(); process.exit(2); }
  }
}

function help() {
  console.log(`
${bold('🏰 df-skills')} — Dwarf Fortress Agent Skills (wiki v50) para agentes de código

${bold('Uso:')}
  df-skills                      setup interativo (seletor de agente)
  df-skills --agent <id>         não-interativo: claude | pi | cursor | codex | generic
  df-skills --agent claude -g    Claude Code em ~/.claude/skills (global, todos os projetos)
  df-skills --force              sobrescreve instalação existente sem perguntar

${bold('O que faz:')}
  Copia 16 skills (~3.7k artigos da wiki + ponte DFHack, copiloto ao vivo e central) para o
  local de descoberta do seu agente + busca local FTS5 (Python stdlib, sem deps),
  e mostra como referenciar a skill inicial no agente escolhido.
`);
}

/* ───────── lista de skills (p/ textos) ───────── */
const SKILL_LIST =
  'df-fortress-geral · df-criaturas · df-combate · df-materiais · df-geologia · ' +
  'df-saude · df-dwarves · df-comercio · df-interface · df-adventure · ' +
  'df-modding · df-fortress-industria · df-fortress-construcao · df-live-bridge · ' +
  'df-adventure-live · df-central';

/* Texto-roteador compartilhado (Cursor rule, AGENTS.md, INSTRUCTIONS.md). */
function routerMarkdown(root) {
  return `Quando a pergunta for sobre **Dwarf Fortress** (mecânicas, criaturas, combate,
materiais, construção, modding, interface, fortaleza ou adventure mode):

1. Escolha a skill mais específica em \`${root}/\` — 16 disponíveis (em dúvida sobre
   qual usar, leia \`${root}/df-central/SKILL.md\`, o mapa do ecossistema):
   ${SKILL_LIST}.
2. Leia o \`SKILL.md\` dela (description, índice e instruções) e depois **apenas**
   o artigo relevante em \`references/\` — nunca carregue tudo.
3. Busca full-text local (traduza a pergunta para termos EN do jogo antes):
   \`python3 ${root}/scripts/search.py "steel smelting"\`
4. Os artigos são em inglês — **responda no idioma do usuário**.
5. Para perguntas gerais ou de iniciante, comece por
   \`${root}/df-fortress-geral/SKILL.md\`.
6. Para o jogo AO VIVO (ler eventos, pausar, enviar comandos DFHack no Linux), use
   \`${root}/df-live-bridge/SKILL.md\` + \`python3 ${root}/scripts/df_bridge.py status\`.
7. Para COPILOTAR uma sessão de Adventure Mode (narrar, aconselhar, ler estado em JSON):
   \`${root}/df-adventure-live/SKILL.md\`.
8. Para retomar a MISSÃO do copiloto (jogar, aprender e criar skills) ou combinar
   várias skills: \`${root}/df-central/SKILL.md\` + \`PROMPT.md\` na raiz do repo.`;
}

/* ───────── agentes suportados ───────── */
const AGENTS = [
  {
    id: 'claude',
    label: 'Claude Code',
    hint: '.claude/skills/',
    detect: ['.claude'],
    target: () => (flags.global
      ? path.join(os.homedir(), '.claude', 'skills')
      : path.join(process.cwd(), '.claude', 'skills')),
    root: () => (flags.global ? '~/.claude/skills' : '.claude/skills'),
  },
  {
    id: 'pi',
    label: 'pi',
    hint: '.agents/skills/',
    detect: ['.agents'],
    target: () => path.join(process.cwd(), '.agents', 'skills'),
    root: () => '.agents/skills',
  },
  {
    id: 'cursor',
    label: 'Cursor',
    hint: '.agents/skills/ + .cursor/rules',
    detect: ['.cursor'],
    target: () => path.join(process.cwd(), '.agents', 'skills'),
    root: () => '.agents/skills',
  },
  {
    id: 'codex',
    label: 'Codex (OpenAI)',
    hint: '.agents/skills/ + AGENTS.md',
    detect: ['AGENTS.md'],
    target: () => path.join(process.cwd(), '.agents', 'skills'),
    root: () => '.agents/skills',
  },
  {
    id: 'generic',
    label: 'Outro / OpenRouter (custom)',
    hint: '.agents/skills/ + snippet de system prompt',
    detect: [],
    target: () => path.join(process.cwd(), '.agents', 'skills'),
    root: () => '.agents/skills',
  },
];

/* ───────── seletor interativo (setas + Enter; fallback numerado) ───────── */
function select(question, options, initial = 0) {
  return new Promise((resolve) => {
    if (!process.stdin.isTTY || !process.stdout.isTTY) {
      // fallback p/ pipes/CI: lista numerada
      console.log(bold(question));
      options.forEach((o, i) => console.log(`  ${i + 1}) ${o.label}${o.hint ? dim('  → ' + o.hint) : ''}`));
      const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
      rl.question(`Escolha [1-${options.length}]: `, (ans) => {
        rl.close();
        const n = parseInt(ans, 10);
        resolve(options[Number.isInteger(n) && n >= 1 && n <= options.length ? n - 1 : initial]);
      });
      return;
    }

    let idx = initial;
    const render = (first) => {
      if (!first) process.stdout.write(`\x1b[${options.length}A`);
      options.forEach((o, i) => {
        const sel = i === idx;
        const mark = sel ? cyan('❯') : ' ';
        const label = sel ? bold(o.label) : o.label;
        const hint = o.hint ? dim('  → ' + o.hint) : '';
        process.stdout.write(`\x1b[2K  ${mark} ${label}${hint}\n`);
      });
    };

    console.log(`${bold(question)} ${dim('(↑/↓ e Enter)')}`);
    process.stdout.write('\x1b[?25l'); // esconde cursor
    render(true);

    readline.emitKeypressEvents(process.stdin);
    process.stdin.setRawMode(true);
    process.stdin.resume();

    const cleanup = () => {
      process.stdin.setRawMode(false);
      process.stdin.pause();
      process.stdin.removeListener('keypress', onKey);
      process.stdout.write('\x1b[?25h'); // mostra cursor
    };
    const onKey = (str, key) => {
      if (!key) return;
      if (key.name === 'up' || key.name === 'k') { idx = (idx - 1 + options.length) % options.length; render(); }
      else if (key.name === 'down' || key.name === 'j' || key.name === 'tab') { idx = (idx + 1) % options.length; render(); }
      else if (key.name === 'return') { cleanup(); resolve(options[idx]); }
      else if ((key.ctrl && key.name === 'c') || key.name === 'escape') { cleanup(); console.log(dim('\ncancelado.')); process.exit(130); }
      else if (str >= '1' && str <= String(Math.min(options.length, 9))) { idx = Number(str) - 1; render(); }
    };
    process.stdin.on('keypress', onKey);
  });
}

/* ───────── cópia ───────── */
const EXCLUDE = new Set(['index.db', '__pycache__', '.DS_Store']);

function copyDir(src, dst) {
  let files = 0;
  let bytes = 0;
  fs.mkdirSync(dst, { recursive: true });
  for (const e of fs.readdirSync(src, { withFileTypes: true })) {
    if (EXCLUDE.has(e.name)) continue;
    const s = path.join(src, e.name);
    const d = path.join(dst, e.name);
    if (e.isDirectory()) {
      const r = copyDir(s, d);
      files += r.files; bytes += r.bytes;
    } else if (e.isFile()) {
      fs.copyFileSync(s, d);
      files += 1; bytes += fs.statSync(d).size;
    }
  }
  return { files, bytes };
}

function findSource() {
  const candidates = [
    path.join(__dirname, '..', '.agents', 'skills'),
    path.join(__dirname, '..', 'skills'),
  ];
  for (const c of candidates) {
    if (fs.existsSync(path.join(c, 'df-fortress-geral', 'SKILL.md'))) return c;
  }
  return null;
}

/* SKILL.md aponta a busca em `.agents/skills/scripts/`; reescreve p/ o destino real. */
function rewriteSkillPaths(targetDir, root) {
  if (root === '.agents/skills') return;
  for (const e of fs.readdirSync(targetDir, { withFileTypes: true })) {
    if (!e.isDirectory() || !e.name.startsWith('df-')) continue;
    const f = path.join(targetDir, e.name, 'SKILL.md');
    if (!fs.existsSync(f)) continue;
    const txt = fs.readFileSync(f, 'utf8').split('.agents/skills/scripts/').join(`${root}/scripts/`);
    fs.writeFileSync(f, txt);
  }
}

/* ───────── extras por agente ───────── */
function writeCursorRule(root) {
  const dir = path.join(process.cwd(), '.cursor', 'rules');
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, 'dwarf-fortress-skills.mdc');
  fs.writeFileSync(file, `---
description: Base de conhecimento e ponte ao vivo do Dwarf Fortress (wiki v50 + DFHack, 16 skills em ${root}/). Use sempre que o usuário perguntar qualquer coisa sobre Dwarf Fortress ou quiser controlar/copilotar o jogo rodando.
alwaysApply: false
---

${routerMarkdown(root)}
`);
  return '.cursor/rules/dwarf-fortress-skills.mdc';
}

const MD_START = '<!-- dwarf-fortress-agent-skill:start -->';
const MD_END = '<!-- dwarf-fortress-agent-skill:end -->';

function upsertAgentsMd(root) {
  const file = path.join(process.cwd(), 'AGENTS.md');
  const block = `${MD_START}
## Dwarf Fortress — base de conhecimento (Agent Skills)

${routerMarkdown(root)}
${MD_END}`;
  let txt = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : '';
  if (txt.includes(MD_START) && txt.includes(MD_END)) {
    txt = txt.slice(0, txt.indexOf(MD_START)) + block + txt.slice(txt.indexOf(MD_END) + MD_END.length);
  } else {
    txt = (txt.trim() ? txt.replace(/\s*$/, '\n\n') : '') + block + '\n';
  }
  fs.writeFileSync(file, txt);
  return 'AGENTS.md (seção adicionada/atualizada)';
}

function writeGenericInstructions(targetDir, root) {
  const file = path.join(targetDir, 'INSTRUCTIONS.md');
  fs.writeFileSync(file, `# Snippet de system prompt — Dwarf Fortress Agent Skills

Cole o bloco abaixo no system prompt do seu agente:

---

Você tem uma base de conhecimento local do Dwarf Fortress (wiki v50) em \`${root}/\`.

${routerMarkdown(root)}

---

*Gerado por df-skills v${pkg.version}.*
`);
  return `${root}/INSTRUCTIONS.md`;
}

/* ───────── mensagem final por agente ───────── */
function finalTips(agentId, root, extraNote) {
  const hr = dim('─'.repeat(64));
  const searchLine = `python3 ${root}/scripts/search.py "como fazer aço"`;
  const lines = [];
  lines.push(hr);
  lines.push(`${bold('▶ Skill inicial:')} ${cyan('df-fortress-geral')} ${dim('(visão geral, quickstart, embarks, FAQs)')}`);
  lines.push('');

  switch (agentId) {
    case 'claude':
      lines.push(bold('Como referenciar no Claude Code:'));
      lines.push(`  • ${bold('Automático')} — abra o Claude Code ${root.startsWith('~') ? 'em qualquer projeto' : 'neste diretório'} e pergunte:`);
      lines.push(`      ${cyan('"como começo minha primeira fortaleza no Dwarf Fortress?"')}`);
      lines.push(`    ${dim('(a skill dispara sozinha pela description — em PT ou EN)')}`);
      lines.push(`  • ${bold('Explícito')} — cite a skill na mensagem:`);
      lines.push(`      ${cyan('"Use a skill df-fortress-geral e me explique como escolher o local de embark"')}`);
      break;
    case 'pi':
      lines.push(bold('Como referenciar no pi:'));
      lines.push(`  • ${bold('Automático')} — o pi descobre ${root}/ sozinho; basta perguntar:`);
      lines.push(`      ${cyan('"qual a melhor arma para meu esquadrão no Dwarf Fortress?"')}`);
      lines.push(`  • ${bold('Explícito')} — cite a skill na mensagem:`);
      lines.push(`      ${cyan('"use a skill df-fortress-geral e me dê um passo a passo de embark"')}`);
      break;
    case 'cursor':
      lines.push(bold('Como referenciar no Cursor:'));
      lines.push(`  • A regra ${cyan('.cursor/rules/dwarf-fortress-skills.mdc')} direciona o agente sozinha.`);
      lines.push(`  • ${bold('Explícito')} — mencione o arquivo da skill no chat:`);
      lines.push(`      ${cyan('"@.agents/skills/df-fortress-geral/SKILL.md como faço meu primeiro embark?"')}`);
      break;
    case 'codex':
      lines.push(bold('Como referenciar no Codex:'));
      lines.push(`  • O ${cyan('AGENTS.md')} já instrui o Codex a usar as skills; basta perguntar sobre o jogo.`);
      lines.push(`  • ${bold('Explícito')} — peça pelo caminho:`);
      lines.push(`      ${cyan('"leia .agents/skills/df-fortress-geral/SKILL.md e me explique como começar"')}`);
      break;
    default:
      lines.push(bold('Como referenciar no seu agente:'));
      lines.push(`  1. Cole o conteúdo de ${cyan(root + '/INSTRUCTIONS.md')} no system prompt.`);
      lines.push(`  2. Depois pergunte citando a skill inicial:`);
      lines.push(`      ${cyan('"use df-fortress-geral e me explique como começar uma fortaleza"')}`);
      break;
  }

  if (extraNote) {
    lines.push('');
    lines.push(`${green('✚')} ${extraNote}`);
  }

  lines.push('');
  lines.push(bold('Busca direta na base (opcional):'));
  lines.push(`  ${cyan(searchLine)}`);
  lines.push(`  ${dim('(requer Python 3 — stdlib apenas; o índice FTS5 se constrói sozinho na 1ª execução)')}`);
  lines.push(hr);
  return lines.join('\n');
}

/* ───────── main ───────── */
(async function main() {
  console.log(`\n${bold('🏰 Dwarf Fortress Agent Skills')} ${dim('v' + pkg.version)}`);
  console.log(dim('Wiki v50 + DFHack ao vivo (ponte + copiloto + central) · 16 skills · busca FTS5 local\n'));

  const source = findSource();
  if (!source) die('conteúdo das skills não encontrado dentro do pacote (instalação corrompida?)');

  /* 1) agente: flag ou seletor (com pré-seleção por detecção do diretório) */
  let agent = flags.agent ? AGENTS.find((a) => a.id === flags.agent) : null;
  if (flags.agent && !agent) {
    die(`--agent inválido: ${flags.agent} ${dim('(use claude | pi | cursor | codex | generic)')}`);
  }
  if (!agent) {
    const detected = AGENTS.findIndex((a) =>
      a.detect.some((d) => fs.existsSync(path.join(process.cwd(), d))));
    agent = await select('Qual agente de código você usa?', AGENTS, Math.max(detected, 0));
  }

  /* 2) escopo do Claude Code (projeto vs global) — só no modo interativo */
  if (agent.id === 'claude' && !flags.global && !flags.agent && process.stdin.isTTY) {
    const scope = await select('Onde instalar para o Claude Code?', [
      { label: 'Neste projeto', hint: './.claude/skills (recomendado)', v: 'project' },
      { label: 'Global', hint: '~/.claude/skills (vale para todos os projetos)', v: 'global' },
    ], 0);
    if (scope.v === 'global') flags.global = true;
  }

  const targetDir = agent.target();
  const root = agent.root();

  if (path.resolve(targetDir) === path.resolve(source)) {
    die('este diretório É o repositório fonte das skills — nada a instalar aqui.');
  }

  /* 3) instalação existente? */
  const marker = path.join(targetDir, 'df-fortress-geral', 'SKILL.md');
  if (fs.existsSync(marker) && !flags.force) {
    if (!process.stdin.isTTY) die(`já existe uma instalação em ${root} — use --force para sobrescrever.`);
    const ow = await select(`Já existe uma instalação em ${root}. Sobrescrever?`, [
      { label: 'Sim, atualizar para esta versão', v: true },
      { label: 'Não, cancelar', v: false },
    ], 0);
    if (!ow.v) { console.log(dim('cancelado.')); process.exit(0); }
  }

  // 4) copia (limpando df-… e scripts/ antigos)
  process.stdout.write(`Instalando em ${bold(root)} … `);
  if (fs.existsSync(targetDir)) {
    for (const e of fs.readdirSync(targetDir)) {
      if (e.startsWith('df-') || e === 'scripts') {
        fs.rmSync(path.join(targetDir, e), { recursive: true, force: true });
      }
    }
  }
  const { files, bytes } = copyDir(source, targetDir);
  rewriteSkillPaths(targetDir, root);
  const nSkills = fs.readdirSync(targetDir).filter((n) => n.startsWith('df-')).length;
  console.log(green('ok'));

  /* 5) cola específica do agente */
  let extraNote = null;
  if (agent.id === 'cursor') extraNote = `Regra criada: ${writeCursorRule(root)}`;
  else if (agent.id === 'codex') extraNote = `Atualizado: ${upsertAgentsMd(root)}`;
  else if (agent.id === 'generic') extraNote = `Snippet salvo em: ${writeGenericInstructions(targetDir, root)}`;

  console.log(`\n${green('✅')} ${bold(`${nSkills} skills`)} · ${files.toLocaleString('pt-BR')} arquivos · ${(bytes / 1048576).toFixed(1)} MB → ${bold(root)}\n`);
  console.log(finalTips(agent.id, root, extraNote));
  console.log(dim(`\nFonte: dwarffortresswiki.org (GFDL & MIT) · snapshot 2026-06 · df-skills v${pkg.version}\n`));
})().catch((e) => die(e && e.message ? e.message : String(e)));
