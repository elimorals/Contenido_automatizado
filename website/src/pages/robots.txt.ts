/**
 * /robots.txt — política de crawlers con allow-list explícito.
 *
 * Decisión 2026 para este proyecto (open source que QUIERE descubrimiento):
 * PERMITIMOS ambos tipos de bots:
 *   - Training crawlers (GPTBot, Google-Extended, anthropic-ai, CCBot, Bytespider, Applebot-Extended)
 *     → para que el conocimiento del proyecto entre a los foundation models
 *   - Search crawlers (OAI-SearchBot, PerplexityBot, ChatGPT-User, Claude-SearchBot, ClaudeBot)
 *     → para que aparezca en respuestas con citación + tráfico referrer
 *
 * Si quisieras BLOCK training pero permitir search, descomenta la sección
 * "OPT-OUT training" abajo.
 *
 * Acceso: https://contenido.vercel.app/robots.txt
 */

import type { APIRoute } from "astro";

const SITE = "https://contenido.vercel.app";

export const GET: APIRoute = () => {
  const body = `# robots.txt — contenido
# Política: allow-all (proyecto open source, queremos descubrimiento)
# Spec: https://www.robotstxt.org/

User-agent: *
Allow: /

# AI training crawlers — permitidos (queremos entrar al conocimiento base)
User-agent: GPTBot
Allow: /

User-agent: Google-Extended
Allow: /

User-agent: anthropic-ai
Allow: /

User-agent: ClaudeBot
Allow: /

User-agent: CCBot
Allow: /

User-agent: Bytespider
Allow: /

User-agent: Applebot-Extended
Allow: /

User-agent: cohere-ai
Allow: /

User-agent: FacebookBot
Allow: /

User-agent: Meta-ExternalAgent
Allow: /

User-agent: Amazonbot
Allow: /

# AI search/RAG crawlers — permitidos (queremos aparecer en citaciones)
User-agent: OAI-SearchBot
Allow: /

User-agent: ChatGPT-User
Allow: /

User-agent: PerplexityBot
Allow: /

User-agent: Perplexity-User
Allow: /

User-agent: Claude-SearchBot
Allow: /

User-agent: Applebot
Allow: /

# Sitemap discovery
Sitemap: ${SITE}/sitemap-index.xml

# LLM discovery (extension, no oficial pero respetada)
# See: https://llmstxt.org/
# LLMs: ${SITE}/llms.txt
# LLMs (full corpus): ${SITE}/llms-full.txt
`;

  return new Response(body, {
    status: 200,
    headers: {
      "Content-Type": "text/plain; charset=utf-8",
      "Cache-Control": "public, max-age=86400",
    },
  });
};
