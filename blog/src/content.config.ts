import { defineCollection } from 'astro:content';
import { z } from 'astro/zod';
import { glob } from 'astro/loaders';

const posts = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/posts' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    author: z.string().trim().min(1),
    date: z.string(),
    updated: z.string().optional(),
    draft: z.boolean(),
    citation: z.boolean().default(false),
    hero: z.object({
      light: z.string().trim().min(1),
      dark: z.string().trim().min(1).optional(),
      alt: z.string().trim().min(1),
      width: z.number().int().positive().default(1536),
      height: z.number().int().positive().default(640),
    }).optional(),
  }),
});

export const collections = { posts };
