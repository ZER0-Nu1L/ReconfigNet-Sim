const texEscapes = {
  '\\': '\\textbackslash{}', '{': '\\{', '}': '\\}', '&': '\\&', '%': '\\%',
  '$': '\\$', '#': '\\#', '_': '\\_', '~': '\\textasciitilde{}', '^': '\\textasciicircum{}',
};

/** @param {string} value */
const escapeTex = value => value.replace(/[\\{}&%$#_~^]/g, character => texEscapes[character]);

/**
 * English frontmatter lists authors in given-name surname order, separated by commas.
 * @param {{ title: string, author: string, date: string }} post
 * @param {string} slug
 * @param {string} url
 */
export function formatCitation(post, slug, url) {
  const names = post.author.split(',').map(name => name.trim().split(/\s+/));
  const surname = names[0].at(-1);
  const key = `${surname}${post.date.slice(0, 4)}${slug}`.toLowerCase().replace(/[^a-z0-9]/g, '');
  const authors = names.map(parts => {
    const family = parts.at(-1);
    const given = parts.slice(0, -1).join(' ');
    return escapeTex(given ? `${family}, ${given}` : family);
  }).join(' and ');
  const month = ['jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec'][Number(post.date.slice(5, 7)) - 1];
  return `@misc{${key},
  title = {{${escapeTex(post.title)}}},
  author = {${authors}},
  year = {${post.date.slice(0, 4)}},
  month = ${month},
  howpublished = {ReconfigNet-Sim Blog},
  url = {${url}}
}`;
}
