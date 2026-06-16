/**
 * /llms-full.txt — corpus completo para LLMs.
 *
 * Variante del spec llms.txt: incluye el CONTENIDO COMPLETO de los docs
 * concatenado en un solo archivo plano, listo para ingest por LLMs.
 *
 * Pattern observado en FastHTML, Anthropic, nbdev: el llms.txt es el
 * "índice", el llms-full.txt es el "manual completo" para ingestión.
 *
 * Acceso: https://contenido.vercel.app/llms-full.txt
 */

import type { APIRoute } from "astro";
import { getCollection } from "astro:content";

const SITE = "https://contenido.vercel.app";

// Orden curado — mismo que el sidebar de docs
const ORDER = [
  "getting_started",
  "api_keys",
  "configuration",
  "editorial",
  "comfyui",
  "long_form",
  "pipeline",
  "cost_model",
  "troubleshooting",
  "api_reference",
  "decisions",
  "migration_from_mpt",
  "contributing",
];

export const GET: APIRoute = async () => {
  const allDocs = await getCollection("docs");
  const byId = new Map(allDocs.map((d) => [d.id, d]));

  const sections: string[] = [];

  sections.push(`# contenido — full documentation corpus

> This file concatenates ALL documentation of the project into a single
> markdown corpus for LLM ingestion. Updated on each build.
>
> Source repo: https://github.com/elimorals/Contenido_automatizado
> Site: ${SITE}
> Specification: https://llmstxt.org/

---
`);

  for (const id of ORDER) {
    const doc = byId.get(id);
    if (!doc) continue;
    sections.push(`\n\n# ──── ${id}.md ────────────────────────────────────────────────\n`);
    sections.push(`<!-- canonical URL: ${SITE}/docs/${id} -->\n\n`);
    sections.push(doc.body ?? "");
  }

  // Append any docs not in ORDER (defensive — si alguien agrega un .md nuevo)
  const used = new Set(ORDER);
  for (const doc of allDocs) {
    if (used.has(doc.id)) continue;
    sections.push(`\n\n# ──── ${doc.id}.md ─────────────────────────────────\n`);
    sections.push(`<!-- canonical URL: ${SITE}/docs/${doc.id} -->\n\n`);
    sections.push(doc.body ?? "");
  }

  return new Response(sections.join(""), {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=3600",
    },
  });
};
