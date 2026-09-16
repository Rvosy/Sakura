// Native adaptation of lucide-animated's BrainIcon; paths remain Lucide geometry.
// Sources and licenses: assets/lucide/README.md, LICENSE and ANIMATED-LICENSE.
export function animatedBrainMarkup() {
  return `<svg class="animated-brain" aria-hidden="true" focusable="false" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path class="brain-stem" pathLength="1" d="M12 18V5" />
    <path class="brain-side" pathLength="1" d="M15 13a4.17 4.17 0 0 1-3-4 4.17 4.17 0 0 1-3 4" />
    <path class="brain-top" pathLength="1" d="M12 5A3 3 0 1 1 17.598 6.5" />
    <path class="brain-top" pathLength="1" d="M12 5A3 3 0 1 0 6.402 6.5" />
    <path d="M17.997 5.125a4 4 0 0 1 2.526 5.77" />
    <path class="brain-lower" pathLength="1" d="M18 18a4 4 0 0 0 2-7.464" />
    <path d="M19.967 17.483A4 4 0 1 1 12 18a4 4 0 1 1-7.967-.517" />
    <path class="brain-lower" pathLength="1" d="M6 18a4 4 0 0 1-2-7.464" />
    <path d="M6.003 5.125a4 4 0 0 0-2.526 5.77" />
  </svg>`;
}
