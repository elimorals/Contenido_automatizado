#!/usr/bin/env node
/**
 * Genera /public/og-image.png (1200×630) desde SVG con sharp.
 *
 * Corre como parte del prebuild — el output es estático y se sirve
 * desde /og-image.png para todas las meta tags og:image / twitter:image.
 *
 * Diseño: deep-black + amber/golden + serif italic 'c' decorativo.
 * Misma identidad cinematográfica del sitio.
 */
import sharp from "sharp";
import { writeFile, mkdir } from "node:fs/promises";
import { dirname } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const OUT_PATH = `${__dirname}/../public/og-image.png`;

const W = 1200;
const H = 630;

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <defs>
    <!-- Golden hour radial gradient -->
    <radialGradient id="glow" cx="50%" cy="0%" r="80%">
      <stop offset="0%" stop-color="#E8B86F" stop-opacity="0.25"/>
      <stop offset="40%" stop-color="#E8B86F" stop-opacity="0.06"/>
      <stop offset="100%" stop-color="#050608" stop-opacity="0"/>
    </radialGradient>
    <!-- Film grain stub -->
    <filter id="grain">
      <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2"/>
      <feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 0.04 0"/>
      <feComposite operator="in" in2="SourceGraphic"/>
    </filter>
  </defs>

  <!-- Background base -->
  <rect width="${W}" height="${H}" fill="#050608"/>
  <rect width="${W}" height="${H}" fill="url(#glow)"/>

  <!-- Letterbox top + bottom lines -->
  <line x1="60" y1="60" x2="${W - 60}" y2="60" stroke="#3A3528" stroke-width="1"/>
  <line x1="60" y1="${H - 60}" x2="${W - 60}" y2="${H - 60}" stroke="#3A3528" stroke-width="1"/>

  <!-- Decorative giant 'c' italic (watermark) -->
  <text
    x="${W - 100}"
    y="${H + 40}"
    text-anchor="end"
    fill="#E8B86F"
    fill-opacity="0.10"
    font-family="Georgia, 'Times New Roman', serif"
    font-style="italic"
    font-weight="500"
    font-size="650"
  >c</text>

  <!-- Cinema label -->
  <g transform="translate(80, 130)">
    <line x1="0" y1="0" x2="36" y2="0" stroke="#E8B86F" stroke-width="2"/>
    <text
      x="48"
      y="5"
      fill="#8A8275"
      font-family="ui-monospace, 'JetBrains Mono', monospace"
      font-size="15"
      font-weight="500"
      letter-spacing="3.5"
    >ECOSISTEMA · OPEN SOURCE · PYTHON 3.11+</text>
  </g>

  <!-- Main headline -->
  <text
    x="80"
    y="240"
    fill="#F5F1E8"
    font-family="Georgia, 'Times New Roman', serif"
    font-size="86"
    font-weight="400"
    letter-spacing="-2"
  >contenido</text>

  <!-- Tagline serif italic -->
  <text
    x="80"
    y="345"
    fill="#C5BFAE"
    font-family="Georgia, 'Times New Roman', serif"
    font-size="50"
    font-weight="300"
    letter-spacing="-1"
  >Genera video con</text>
  <text
    x="80"
    y="410"
    fill="#E8B86F"
    font-family="Georgia, 'Times New Roman', serif"
    font-style="italic"
    font-size="50"
    font-weight="400"
    letter-spacing="-1"
  >identidad propia<tspan fill="#C5BFAE" font-style="normal"> , no</tspan></text>
  <text
    x="80"
    y="475"
    fill="#C5BFAE"
    font-family="Georgia, 'Times New Roman', serif"
    font-size="50"
    font-weight="300"
    letter-spacing="-1"
  >"slop de IA".</text>

  <!-- Footer stats bar -->
  <g transform="translate(80, ${H - 95})">
    <text
      fill="#8A8275"
      font-family="ui-monospace, 'JetBrains Mono', monospace"
      font-size="14"
      letter-spacing="2.5"
    >
      <tspan>413 TESTS</tspan>
      <tspan dx="20" fill="#5E5848">/</tspan>
      <tspan dx="20">5 LINAJES</tspan>
      <tspan dx="20" fill="#5E5848">/</tspan>
      <tspan dx="20">7 WORKFLOWS COMFYUI</tspan>
      <tspan dx="20" fill="#5E5848">/</tspan>
      <tspan dx="20" fill="#E8B86F">LONG-FORM 5-60MIN</tspan>
    </text>
  </g>

  <!-- URL bottom right -->
  <text
    x="${W - 80}"
    y="${H - 60}"
    text-anchor="end"
    fill="#5E5848"
    font-family="ui-monospace, 'JetBrains Mono', monospace"
    font-size="14"
    letter-spacing="2"
  >contenido.vercel.app</text>
</svg>`;

async function main() {
  await mkdir(dirname(OUT_PATH), { recursive: true });
  await sharp(Buffer.from(svg))
    .png({ quality: 90, compressionLevel: 9 })
    .toFile(OUT_PATH);
  console.log(`✓ Generated ${OUT_PATH}`);
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});
