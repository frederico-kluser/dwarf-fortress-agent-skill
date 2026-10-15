import { useState } from 'react';
import Alert from '@mui/material/Alert';
import Box from '@mui/material/Box';
import Button from '@mui/material/Button';
import Chip from '@mui/material/Chip';
import List from '@mui/material/List';
import ListItem from '@mui/material/ListItem';
import ListItemText from '@mui/material/ListItemText';
import Stack from '@mui/material/Stack';
import TextField from '@mui/material/TextField';
import Typography from '@mui/material/Typography';
import Send from '@mui/icons-material/Send';

import { errorMessage } from '../api/events';
import { formatDateTime } from '../api/format';
import { useInputs, useSubmitInput } from '../api/queries';
import PanelShell from '../components/PanelShell';
import { PanelError, PanelNotice } from '../components/PanelState';
import { EmptyState, RunaRule } from '../components/rpg';
import { RunaLoading, RunaSection } from '../components/RunaKit';
import { MONO_FONT } from '../theme';

const EXAMPLES = [
  'Há uma caverna a norte que dá água.',
  'Evita combater à noite; procura abrigo primeiro.',
  'O portão de pedra é o objetivo principal.',
];

/** Injeção de informação livre pelo utilizador (traduzida PT→EN pelo servidor). */
export default function InputsPanel() {
  const inputsQuery = useInputs();
  const [text, setText] = useState('');
  const [feedback, setFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);

  const mutation = useSubmitInput({
    onSuccess: () => {
      setText('');
      setFeedback({ tone: 'success', text: 'Informação enviada e traduzida para inglês.' });
    },
    onError: (error) =>
      setFeedback({ tone: 'error', text: `Falha ao enviar: ${errorMessage(error)}` }),
  });

  const history = [...(inputsQuery.data ?? [])].sort((a, b) => (a.ts < b.ts ? 1 : -1));

  return (
    <PanelShell
      title="Injeção de informação"
      description="Informação livre para o agente. É traduzida PT→EN antes de entrar nos prompts."
    >
      <Stack spacing={2}>
        <RunaSection
          title="Nova informação"
          subtitle="Uma ideia por envio — o agente lê isto como contexto"
          glyph="key"
          texture
        >
          <Stack spacing={1.5}>
            <TextField
              id="inputs-texto"
              label="Informação para o agente (português)"
              value={text}
              onChange={(event) => setText(event.target.value)}
              multiline
              minRows={3}
              fullWidth
              helperText="Escreve em português. O servidor traduz para inglês antes de guardar."
            />

            <Stack
              direction="row"
              spacing={0.5}
              useFlexGap
              sx={{ flexWrap: 'wrap', alignItems: 'center' }}
            >
              <Typography variant="caption" sx={{ color: 'text.secondary', mr: 0.5 }}>
                Exemplos:
              </Typography>
              {EXAMPLES.map((example) => (
                <Chip
                  key={example}
                  size="small"
                  variant="outlined"
                  label={example}
                  onClick={() => setText(example)}
                />
              ))}
            </Stack>

            <Stack direction="row" spacing={1}>
              <Button
                variant="contained"
                startIcon={<Send />}
                sx={{ minHeight: 40 }}
                loading={mutation.isPending}
                loadingPosition="start"
                disabled={text.trim().length === 0}
                onClick={() => mutation.mutate({ text: text.trim() })}
              >
                Enviar
              </Button>
              <Button variant="text" sx={{ minHeight: 40 }} disabled={text.length === 0} onClick={() => setText('')}>
                Limpar
              </Button>
            </Stack>

            {feedback ? (
              <Alert severity={feedback.tone} role="status" onClose={() => setFeedback(null)}>
                {feedback.text}
              </Alert>
            ) : null}
          </Stack>
        </RunaSection>

        <RunaRule />

        <RunaSection
          title="Histórico"
          subtitle={`${history.length} envios nesta campanha`}
          glyph="scroll"
          flush
        >
          {inputsQuery.isLoading ? (
            <Box sx={{ p: 2 }}>
              <RunaLoading label="A carregar histórico…" />
            </Box>
          ) : null}

          {inputsQuery.isError ? (
            <Box sx={{ p: 2 }}>
              <PanelError
                message={errorMessage(inputsQuery.error)}
                onRetry={() => void inputsQuery.refetch()}
              />
            </Box>
          ) : null}

          {!inputsQuery.isLoading && history.length === 0 ? (
            <EmptyState
              glyph="key"
              title="Ainda não injetaste informação."
              body="Tudo o que escreveres aqui entra no contexto do agente depois de traduzido."
            />
          ) : null}

          {history.length > 0 ? (
            <List dense disablePadding>
              {history.map((entry) => (
                <ListItem key={entry.id} divider alignItems="flex-start">
                  <ListItemText
                    primary={entry.text_pt}
                    secondary={`EN: ${entry.text} · ${formatDateTime(entry.ts)}`}
                    slotProps={{
                      primary: {
                        variant: 'body2',
                        sx: { maxWidth: '72ch', textWrap: 'pretty' },
                      },
                      secondary: {
                        variant: 'caption',
                        sx: { fontFamily: MONO_FONT, fontVariantNumeric: 'tabular-nums' },
                      },
                    }}
                  />
                </ListItem>
              ))}
            </List>
          ) : null}
        </RunaSection>

        <PanelNotice severity="info">
          O contrato (<code>docs/api.md</code>) só define <code>POST /api/campaigns/{'{id}'}/inputs</code>{' '}
          — não há <code>GET</code> de histórico. Este histórico é mantido localmente pelo cliente
          (por campanha): o texto em português que escreveste e o texto em inglês devolvido pelo
          servidor.
        </PanelNotice>
      </Stack>
    </PanelShell>
  );
}
