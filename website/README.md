# contenido — website

Sitio informativo y documental del proyecto [`contenido`](../). Built con Astro 5 + Tailwind v4 + MDX.

## Identidad

- **Estética**: cinematográfica · deep-black (`#050608`) + amber/golden (`#E8B86F`) accent
- **Tipografía**: Fraunces (display, axes SOFT/opsz) + Manrope (body) + JetBrains Mono
- **Background**: film grain noise overlay + golden hour radial light leak
- **Filosofía**: regla 80/20 del color — 80% del frame es deep black/warm white, 20% es amber escaso donde el ojo debe parar

## Estructura

```
website/
├── astro.config.mjs       # MDX + sitemap + Tailwind v4 vite plugin
├── package.json           # Astro 5 + pagefind para búsqueda
├── tsconfig.json
├── vercel.json            # Deploy config
├── public/
│   └── favicon.svg        # SVG con la 'c' serif italic + punto amber
└── src/
    ├── styles/global.css  # Design tokens cinematográficos + tipografía
    ├── layouts/
    │   ├── BaseLayout.astro    # Head, OG, Twitter card
    │   └── DocsLayout.astro    # Sidebar de docs + prose styling
    ├── components/
    │   ├── Header.astro         # Sticky nav con backdrop-blur
    │   ├── Footer.astro         # 3 cols: brand + docs + linajes
    │   ├── Hero.astro           # Asimétrico — copy + terminal preview
    │   ├── StatsStrip.astro     # 5 números tabulares grandes
    │   ├── LineagesSection.astro # 5 linajes, layout listado serif
    │   ├── CapabilitiesMatrix.astro # 13 backends con filtro client-side
    │   ├── LongFormVsReels.astro # Diptych comparativa
    │   └── CTAClosing.astro      # CTA final con golden glow
    ├── data/
    │   └── workflows.ts          # Source-of-truth de las capabilities
    ├── content.config.ts         # Glob loader hacia ../docs/*.md
    └── pages/
        ├── index.astro            # Landing
        ├── docs/
        │   ├── index.astro        # Listado de docs agrupados
        │   └── [...slug].astro    # Render dinámico de cada .md
        └── 404.astro
```

## Source of truth

Los documentos `.md` viven en `../docs/`. Astro los carga con `glob({ base: "../docs" })`. **No hay duplicación** — editar un doc del repo se propaga al site con `npm run build`.

## Comandos

```bash
npm install           # Una vez
npm run dev           # Dev server con HMR — http://localhost:4321
npm run build         # Build estático + indexa pagefind
npm run preview       # Sirve dist/ localmente
```

## Deploy

```bash
# Vercel (auto via `vercel.json`)
vercel --prod
```

El sitio es 100% estático. Sin SSR, sin DB, sin auth. ~1.5MB total post-build.

## Decisiones de diseño clave

1. **Sin JS framework**: vanilla CSS + Astro components. El único script es el filtro client-side de la `CapabilitiesMatrix` (~30 líneas). Carga rápido.
2. **Stagger CSS-only**: `animation-delay` escalonado para hero reveal. Cero libs.
3. **Tabular nums everywhere**: `font-variant-numeric: tabular-nums` en stats y specs — feel "engineered".
4. **Letras decorativas gigantes como watermark**: `c`, `M`, `D`, `✦` en serif italic 144pt — refuerzan la identidad editorial-cinema sin agregar ruido.
5. **Búsqueda gratis**: pagefind indexa el sitio post-build (4225 palabras), una sola lib client-side WASM.
