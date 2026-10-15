/**
 * Componentes partilhados «RPG Medieval» (docs/design-rpg-medieval.md §4–§5).
 * Pele sobre o dashboard de operação: só tokens/variáveis do tema, zero cores
 * cravadas, cor nunca como único sinal (glifo + etiqueta PT sempre).
 */
export { RunaGlyph, type RunaGlyphName, type RunaGlyphProps } from './RunaGlyph';
export { RunaRule, RunaCorda } from './RunaRule';
export { StoneTexture } from './StoneTexture';
export { MissionCard, type MissionCardProps, type MissionProgressState } from './MissionCard';
export {
  CharacterSheet,
  type CharacterSheetProps,
  type CharacterStat,
  type CharacterVital,
  type CharacterSkill,
} from './CharacterSheet';
export { BandBadge, type BandBadgeProps, type BadgeBanda } from './BandBadge';
export { EmptyState, type EmptyStateProps } from './EmptyState';
export { RpgSkeleton, type RpgSkeletonProps, type SkeletonHeight } from './RpgSkeleton';
export { ErrorBlock, type ErrorBlockProps } from './ErrorBlock';
