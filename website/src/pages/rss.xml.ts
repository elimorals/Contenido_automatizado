/**
 * /rss.xml — RSS 2.0 feed de la documentación.
 *
 * Por qué: los AI crawlers (Perplexity, ChatGPT, Claude, You.com) y los
 * lectores de feed clásicos usan RSS para descubrir páginas nuevas sin
 * recrawlear el sitemap completo. Para un proyecto open source que QUIERE
 * ser citado, exponer un feed es una de las palancas más eficientes.
 *
 * Spec: https://www.rssboard.org/rss-specification
 * Discovery: <link rel="alternate" type="application/rss+xml"> en BaseLayout.
 */

import rss from "@astrojs/rss";
import type { APIContext } from "astro";
import { getCollection } from "astro:content";

// Los docs actuales no llevan `date` en frontmatter — usamos un baseline
// único hasta que extendamos el schema. Los crawlers usan el feed para
// descubrir URLs, no necesariamente para ordenar por frescura.
const BASELINE_DATE = new Date("2026-06-16T00:00:00.000Z");

// Orden curado: los docs "carta de presentación" primero. Match contra
// los IDs lowercased que Astro genera del filename (sin .md).
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

function titleFromId(id: string): string {
  return id.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

export async function GET(context: APIContext): Promise<Response> {
  const docs = await getCollection("docs");
  const byId = new Map(docs.map((d) => [d.id, d]));

  const items = ORDER.filter((id) => byId.has(id)).map((id) => {
    const entry = byId.get(id)!;
    return {
      title: entry.data.title ?? titleFromId(id),
      description:
        entry.data.description ??
        `${titleFromId(id)} — documentación del proyecto contenido`,
      link: `/docs/${id}/`,
      pubDate: BASELINE_DATE,
    };
  });

  return rss({
    title: "contenido · documentación",
    description:
      "Plataforma open source de generación de video con IA. Reels cinematográficos con LoRA propia, long-form de 5–60 min, ComfyUI multi-tenant, cost-tracking nativo.",
    site: context.site ?? "https://contenido.vercel.app/",
    items,
    customData:
      "<language>es</language>" +
      "<copyright>Apache-2.0</copyright>" +
      "<generator>Astro · @astrojs/rss</generator>" +
      "<docs>https://www.rssboard.org/rss-specification</docs>",
    xmlns: { atom: "http://www.w3.org/2005/Atom" },
  });
}
