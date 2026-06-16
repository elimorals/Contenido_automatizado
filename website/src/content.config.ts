// Carga los .md de docs/ del proyecto como Content Collection.
// Source-of-truth single: editar .md una vez, propagado al site.
import { defineCollection, z } from "astro:content";
import { glob } from "astro/loaders";

const docs = defineCollection({
  loader: glob({
    base: "../docs",
    pattern: "*.md",
  }),
  schema: z.object({
    title: z.string().optional(),
    description: z.string().optional(),
  }),
});

export const collections = { docs };
