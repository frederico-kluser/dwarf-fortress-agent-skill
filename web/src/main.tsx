import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react';
import { createRoot } from 'react-dom/client';
import Alert from '@mui/material/Alert';
import AlertTitle from '@mui/material/AlertTitle';
import Container from '@mui/material/Container';
import CssBaseline from '@mui/material/CssBaseline';
import InitColorSchemeScript from '@mui/material/InitColorSchemeScript';
import { ThemeProvider } from '@mui/material/styles';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// §3.1 — fontes self-host (fontsource, zero CDN): display Cinzel, texto Inter,
// dados IBM Plex Mono. Subconjunto latin-* conforme o spec.
import '@fontsource/cinzel/latin-600.css';
import '@fontsource/cinzel/latin-700.css';
import '@fontsource-variable/inter';
import '@fontsource/ibm-plex-mono/latin-400.css';
import '@fontsource/ibm-plex-mono/latin-500.css';
import './index.css';

import App from './App';
import { ApiProvider } from './api/ApiProvider';
import { theme } from './theme';
import { MotionUIThemeProvider } from '@/components/motion-ui/ui-theme';
import motionTheme from '../motion.theme';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // O canal vivo é o WebSocket; as queries são leitura + rede de segurança.
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

/** Última linha de defesa: um erro de render não deve deixar o ecrã em branco. */
class AppErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };

  static getDerivedStateFromError(error: Error) {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Erro na UI:', error, info.componentStack);
  }

  render() {
    if (this.state.error) {
      return (
        <Container maxWidth="sm" sx={{ py: 6 }}>
          <Alert severity="error" role="alert">
            <AlertTitle>A interface falhou</AlertTitle>
            {this.state.error.message}
          </Alert>
        </Container>
      );
    }
    return this.props.children;
  }
}

createRoot(document.getElementById('root') as HTMLElement).render(
  <StrictMode>
    {/* Anti-flicker: com colorSchemeSelector 'class' o atributo tem de casar. */}
    <InitColorSchemeScript attribute="class" defaultMode="dark" />
    <ThemeProvider theme={theme} defaultMode="dark" noSsr disableTransitionOnChange>
      <CssBaseline />
      {/* Motion UI: uma só vez na raiz — sem isto as secções caem nos defaults. */}
      <MotionUIThemeProvider theme={motionTheme}>
        <AppErrorBoundary>
          <QueryClientProvider client={queryClient}>
            <ApiProvider>
              <App />
            </ApiProvider>
          </QueryClientProvider>
        </AppErrorBoundary>
      </MotionUIThemeProvider>
    </ThemeProvider>
  </StrictMode>,
);
