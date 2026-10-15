/**
 * Textura de ruído de pedra (docs/design-rpg-medieval.md §4.2) — camada de
 * fundo CSS/SVG inline, sem imagens, sem overlay, sem mix-blend-mode. O corpo
 * da página já a recebe por `body { background-image: var(--texture) }`; este
 * componente serve para o hero (§5.1) e outras superfícies de nível 2.
 * Com `--texture: none` a camada desaparece (teste de pronto §4.5).
 */
export function StoneTexture({ className }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={className ? `pointer-events-none absolute inset-0 ${className}` : 'pointer-events-none absolute inset-0'}
      style={{ backgroundImage: 'var(--texture)' }}
    />
  );
}
