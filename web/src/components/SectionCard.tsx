import type { ReactNode } from 'react';
import Card from '@mui/material/Card';
import CardContent from '@mui/material/CardContent';
import CardHeader from '@mui/material/CardHeader';
import Divider from '@mui/material/Divider';
import Typography from '@mui/material/Typography';

export interface SectionCardProps {
  title: string;
  subtitle?: string;
  /** Ações à direita do cabeçalho (botões, chips, filtros). */
  action?: ReactNode;
  children: ReactNode;
  /** Nível do heading — mantém a hierarquia sem saltos (a11y). */
  headingLevel?: 'h2' | 'h3';
  /** Sem padding no conteúdo (para listas/tabelas que gerem o seu próprio espaçamento). */
  flush?: boolean;
}

/** Bloco de secção: cabeçalho + conteúdo, sempre sobre um Card outlined. */
export default function SectionCard({
  title,
  subtitle,
  action,
  children,
  headingLevel = 'h2',
  flush = false,
}: SectionCardProps) {
  return (
    <Card component="section">
      <CardHeader
        sx={{ px: 2, py: 1.5 }}
        title={
          <Typography variant="h5" component={headingLevel}>
            {title}
          </Typography>
        }
        subheader={
          subtitle ? (
            <Typography variant="caption" sx={{ color: 'text.secondary' }}>
              {subtitle}
            </Typography>
          ) : undefined
        }
        action={action}
      />
      <Divider />
      <CardContent sx={flush ? { p: 0, '&:last-child': { pb: 0 } } : { p: 2, '&:last-child': { pb: 2 } }}>
        {children}
      </CardContent>
    </Card>
  );
}
