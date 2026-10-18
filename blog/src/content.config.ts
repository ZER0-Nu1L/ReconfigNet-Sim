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
    draft: z.boolean(),
  }),
});

export const collections = { posts };
