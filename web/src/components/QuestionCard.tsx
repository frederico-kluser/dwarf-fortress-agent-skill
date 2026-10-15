import { useState } from 'react';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Collapse from '@mui/material/Collapse';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import ExpandMore from '@mui/icons-material/ExpandMore';
import Send from '@mui/icons-material/Send';

import {
  StaggerReveal,
  StaggerRevealHeadline,
  StaggerRevealItem,
} from '@/components/motion-ui/stagger-reveal';
import { asText, formatRelative } from '../api/format';
import {
  isDecisionBand,
  isJevAgent,
  type AnswerQuestionRequest,
  type DecisionBand,
  type Question,
  type QuestionSourceData,
} from '../api/types';
import AnswerConfirmDialog from './AnswerConfirmDialog';
import { AttemptsChip, BandChip, QuestionStatusChip } from './Chips';
import { MONO_FONT } from '../theme';

export interface QuestionCardProps {
  question: Question;
  /** Envia a resposta (opção rápida ou texto livre). */
  onAnswer: (body: AnswerQuestionRequest) => void;
  busy?: boolean;
  /** Versão compacta para o drawer da fila. */
  dense?: boolean;
  /**
   * Coalescência visual (auditoria v2, achado 1): outros pedidos PENDENTES da
   * MESMA `action_id`, agrupados neste cartão em vez de virarem cartões novos.
   */
  coalesced?: Question[];
}

interface PendingAnswer {
  text: string;
  optionId: string | null;
}

/**
 * Explicação derivada quando o produtor não mandou `why_pt` (payload antigo com
 * banda): é a mesma frase que o loop publica (`_why_pt`), nunca conteúdo novo.
 */
const BAND_WHY_PT: Record<DecisionBand, string> = {
  auto: 'a decisão ficou na banda auto: confirmar antes de executar',
  hitl: 'a confiança ficou na banda hitl (0.50–0.89): confirmar antes de executar',
  abstain: 'a confiança ficou abaixo de 0.50: o agente não age sozinho',
};

/** Ordem de leitura do diagnóstico — o que explica primeiro, o resto depois. */
const SOURCE_KEY_ORDER = [
  'reason',
  'band',
  'action_id',
  'action_en',
  'description_en',
  'action_confidence',
  'confidence',
  'answer_mode',
  'action_mode',
];

function sourceEntries(data: QuestionSourceData): [string, unknown][] {
  return Object.entries(data).sort(([a], [b]) => {
    const ia = SOURCE_KEY_ORDER.indexOf(a);
    const ib = SOURCE_KEY_ORDER.indexOf(b);
    return (ia === -1 ? SOURCE_KEY_ORDER.length : ia) - (ib === -1 ? SOURCE_KEY_ORDER.length : ib);
  });
}

/**
 * Segundo nível de divulgação (auditoria v2, achado 7): o diagnóstico
 * `source_data` do contrato (reason, confiança da ação, candidatos…) num
 * expansível colapsado por omissão — nunca polui o cartao fechado.
 */
function SourceDataDetails({ data, dense }: { data: QuestionSourceData; dense: boolean }) {
  const [open, setOpen] = useState(false);
  const entries = sourceEntries(data);
  if (entries.length === 0) return null;
  return (
    <Box sx={{ mt: dense ? 0.5 : 1 }}>
      <Button
        size="small"
        aria-expanded={open}
        aria-label={`Diagnóstico do pedido (${entries.length} campos)`}
        onClick={() => setOpen((value) => !value)}
        endIcon={
          <ExpandMore
            fontSize="small"
            sx={{ transform: open ? 'rotate(180deg)' : 'none', transition: 'transform 150ms' }}
          />
        }
      >
        Diagnóstico do pedido ({entries.length})
      </Button>
      <Collapse in={open}>
        <Stack spacing={0.25} sx={{ pt: 0.5, pl: 1, borderLeft: '2px solid var(--mui-palette-divider)' }}>
          {entries.map(([key, value]) => (
            <Stack key={key} direction="row" spacing={1} useFlexGap sx={{ flexWrap: 'wrap' }}>
              <Typography variant="caption" sx={{ fontFamily: MONO_FONT, color: 'text.secondary', minWidth: 150 }}>
                {key}
              </Typography>
              <Typography variant="caption" sx={{ fontFamily: MONO_FONT, whiteSpace: 'pre-wrap' }}>
                {asText(value)}
              </Typography>
            </Stack>
          ))}
        </Stack>
      </Collapse>
    </Box>
  );
}

/**
 * Proveniência da pergunta (achado #2 de `docs/uxui-audit-ui.json`): a fila tem
 * de dizer **porque** o agente pergunta, em que banda e com que ação retida.
 *
 * Os campos são aditivos: sem nenhum deles (produtor antigo) não se desenha nada;
 * com alguns, mostra-se só o que existe — a banda nunca depende só da cor (WCAG).
 */
function QuestionProvenance({ question, dense }: { question: Question; dense: boolean }) {
  const band = isDecisionBand(question.band) ? question.band : null;
  const role = isJevAgent(question.role) ? question.role : null;
  const actionId = question.action_id?.trim() ? question.action_id.trim() : null;
  const why = question.why_pt?.trim() ? question.why_pt.trim() : band ? BAND_WHY_PT[band] : null;
  const source = question.source_data ?? null;

  if (!band && !role && !actionId && !why && !source) return null;

  return (
    <StaggerRevealItem>
      <Box
        sx={{
          p: dense ? 1 : 1.25,
          borderRadius: 1,
          borderLeft: '3px solid var(--mui-palette-info-main)',
          backgroundColor: 'var(--mui-palette-action-hover)',
        }}
      >
      <Stack spacing={0.5}>
        <Stack
          direction="row"
          spacing={0.75}
          useFlexGap
          sx={{ alignItems: 'center', flexWrap: 'wrap' }}
        >
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            Porque pergunta
          </Typography>
          {band ? <BandChip band={band} /> : null}
        </Stack>

        {why ? <Typography variant="body2">{why}</Typography> : null}

        {role || actionId ? (
          <Typography variant="caption" sx={{ color: 'text.secondary', fontFamily: MONO_FONT }}>
            {role ? `papel ${role}` : ''}
            {role && actionId ? ' · ' : ''}
            {actionId ? `ação ${actionId}` : ''}
          </Typography>
        ) : null}

        {source ? <SourceDataDetails data={source} dense={dense} /> : null}
      </Stack>
      </Box>
    </StaggerRevealItem>
  );
}

/**
 * Pergunta/dúvida ao humano: texto em PT, opções rápidas e campo livre.
 * A resposta é traduzida PT→EN pelo servidor antes de entrar nos prompts.
 *
 * Fricção alinhada com a irreversibilidade (auditoria C.1.4.01): a resposta entra
 * na memória do agente e não há edição no contrato, logo nenhum caminho envia com
 * um clique acidental — opção rápida e texto livre passam AMBOS pelo mesmo passo
 * de revisão: o diálogo de confirmação com o texto exato («Rever e enviar» abre-o
 * diretamente — 1 passo consistente).
 *
 * Teclado (auditoria F.1.1.03): o campo é `multiline`, por isso **Enter insere
 * quebra de linha** (comportamento esperado de textarea) e nunca dispara revisão
 * nem envio — nenhuma tecla de edição de texto origina um estado de envio não
 * intencional. O envio por teclado é um atalho explícito: Ctrl+Enter (⌘+Enter).
 */
export default function QuestionCard({
  question,
  onAnswer,
  busy = false,
  dense = false,
  coalesced = [],
}: QuestionCardProps) {
  const [freeText, setFreeText] = useState('');
  const [pending, setPending] = useState<PendingAnswer | null>(null);
  const answered = question.status === 'answered';
  const fieldId = `resposta-livre-${question.id}`;

  /**
   * Dedupe/agrupamento (auditoria v2, achado 1): o total de pedidos desta ação é
   * a soma dos `attempts` de todas as entradas agrupadas (1 quando ausente), e o
   * último re-pedido é o `last_seen_ts` mais recente.
   */
  const groupAttempts =
    [question, ...coalesced].reduce((total, item) => total + (item.attempts ?? 1), 0) || 1;
  const lastSeen = [question, ...coalesced]
    .map((item) => item.last_seen_ts)
    .filter((ts): ts is string => Boolean(ts))
    .sort()
    .at(-1);

  const resetDraft = () => {
    setFreeText('');
  };

  /**
   * Único caminho do texto livre (F.1.1.03): abre o diálogo de revisão com o
   * texto exato — o mesmo passo do botão «Rever e enviar». Disparado pelo botão
   * e pelo atalho explícito Ctrl+Enter (⌘+Enter); Enter nunca chega aqui.
   */
  const reviewFreeText = () => {
    const text = freeText.trim();
    if (!text) return;
    setPending({ text, optionId: null });
  };

  const confirm = () => {
    if (!pending) return;
    onAnswer(
      pending.optionId
        ? { answer_text: pending.text, option_id: pending.optionId }
        : { answer_text: pending.text },
    );
    setPending(null);
    resetDraft();
  };

  const cancel = () => {
    // Cancelar no texto livre volta ao rascunho, sem perder o que foi escrito.
    setPending(null);
  };

  return (
    <>
      {/*
        Cartão composto no vocabulário semântico Motion UI; a chegada (montagem
        do cartão — primeira leitura ou evento WebSocket) revela-se em
        `stagger-reveal`: a pergunta sobe linha a linha e o conteúdo entra em
        cascata por baixo.
      */}
      <StaggerReveal
        as="article"
        className={
          dense
            ? 'flex flex-col gap-3 rounded-lg border border-border bg-card p-3 text-foreground shadow-sm'
            : 'flex flex-col gap-3 rounded-lg border border-border bg-card p-4 text-foreground shadow-sm'
        }
      >
        <div className="flex items-start justify-between gap-2">
          <StaggerRevealHeadline
            as="h3"
            className="min-w-0 text-base font-semibold text-foreground"
          >
            {question.question}
          </StaggerRevealHeadline>
          <Stack direction="row" spacing={0.5} sx={{ alignItems: 'center', flexShrink: 0 }}>
            <AttemptsChip attempts={groupAttempts} lastSeenTs={lastSeen} />
            <QuestionStatusChip status={question.status} />
          </Stack>
        </div>

        <StaggerRevealItem>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {question.id} · {formatRelative(question.ts)}
            {groupAttempts > 1 && lastSeen ? ` · último re-pedido ${formatRelative(lastSeen)}` : ''}
          </Typography>
        </StaggerRevealItem>

        <QuestionProvenance question={question} dense={dense} />

        {coalesced.length > 0 ? (
          <StaggerRevealItem>
            <Box
            sx={{
              p: 1,
              borderRadius: 1,
              border: '1px dashed var(--mui-palette-warning-main)',
            }}
          >
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
              Agrupados: {coalesced.length + 1} pedidos da mesma ação
              {question.action_id ? ` (${question.action_id})` : ''} —{' '}
              {question.source_data?.answer_mode === 'approval' ||
              question.source_data?.reason === 'checkpoint_mode'
                ? 'responder decide o grupo (duplicados pendentes são descartados).'
                : 'cada entrada mantém a sua resposta; o contador soma todos os pedidos.'}
            </Typography>
            <Stack component="ul" spacing={0.25} sx={{ m: 0, pl: 2 }}>
              {coalesced.map((item) => (
                <Typography key={item.id} component="li" variant="caption" sx={{ fontFamily: MONO_FONT }}>
                  {item.id} · {formatRelative(item.ts)}
                  {item.attempts ? ` · ${item.attempts} tentativa(s)` : ''}
                </Typography>
              ))}
            </Stack>
            </Box>
          </StaggerRevealItem>
        ) : null}

        <StaggerRevealItem>
        {answered ? (
          <Box>
            <Typography variant="caption" sx={{ color: 'text.secondary', display: 'block' }}>
              Resposta (canónica em EN)
            </Typography>
            <Typography variant="body2">{question.answer_text ?? '—'}</Typography>
            {question.option_id ? (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                opção: {question.option_id}
              </Typography>
            ) : null}
          </Box>
        ) : (
          <>
            {question.options.length > 0 ? (
              <Stack
                direction="row"
                spacing={1}
                useFlexGap
                role="group"
                aria-label={`Opções rápidas para ${question.id}`}
                sx={{ flexWrap: 'wrap' }}
              >
                {question.options.map((option) => (
                  <Button
                    key={option.id}
                    variant="outlined"
                    size="small"
                    disabled={busy}
                    onClick={() => setPending({ text: option.text, optionId: option.id })}
                    sx={{ minHeight: 40 }}
                  >
                    {option.text}
                  </Button>
                ))}
              </Stack>
            ) : null}

            {question.free_text_allowed ? (
              <Stack spacing={1}>
                <Stack
                  direction={{ xs: 'column', sm: 'row' }}
                  spacing={1}
                  sx={{ alignItems: 'flex-start' }}
                >
                  <TextField
                    id={fieldId}
                    label="Resposta livre"
                    value={freeText}
                    onChange={(event) => setFreeText(event.target.value)}
                    onKeyDown={(event) => {
                      // Enter é do textarea (quebra de linha). Só o atalho
                      // explícito Ctrl/⌘+Enter abre a revisão (F.1.1.03).
                      if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
                        event.preventDefault();
                        reviewFreeText();
                      }
                    }}
                    multiline
                    minRows={1}
                    maxRows={4}
                    fullWidth
                    helperText="Escreve em português — o servidor traduz antes de guardar. Enter quebra linha; Ctrl+Enter (ou ⌘+Enter) revê e envia."
                  />
                  <Button
                    variant="contained"
                    onClick={reviewFreeText}
                    disabled={busy || freeText.trim().length === 0}
                    loading={busy}
                    loadingPosition="start"
                    startIcon={<Send />}
                    sx={{ flexShrink: 0, mt: { xs: 0, sm: 0.5 }, minHeight: 40 }}
                  >
                    Rever e enviar
                  </Button>
                </Stack>
              </Stack>
            ) : (
              <Typography variant="caption" sx={{ color: 'text.secondary' }}>
                Esta pergunta só aceita uma das opções acima.
              </Typography>
            )}
          </>
        )}
        </StaggerRevealItem>
      </StaggerReveal>

      <AnswerConfirmDialog
        question={pending ? question : null}
        answerText={pending?.text ?? ''}
        optionId={pending?.optionId ?? null}
        busy={busy}
        onCancel={cancel}
        onConfirm={confirm}
      />
    </>
  );
}
