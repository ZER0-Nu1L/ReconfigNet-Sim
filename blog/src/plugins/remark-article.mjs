// Authoring conventions shared by every Markdown article.
export default function articleFormatting() {
  return (tree) => {
    function walk(parent) {
      if (!parent.children) return;
      for (let index = 0; index < parent.children.length; index++) {
        const node = parent.children[index];
        const first = node.children?.[0];
        const marker = node.type === 'blockquote' && first?.type === 'paragraph'
          && first.children[0]?.type === 'text'
          && first.children[0].value.match(/^\[!TLDR\](?:\r?\n|$)/);
        if (marker) {
          first.children[0].value = first.children[0].value.slice(marker[0].length);
          if (!first.children[0].value) first.children.shift();
          if (!first.children.length) node.children.shift();
          node.data = { hName: 'aside', hProperties: { className: ['tldr'], ariaLabel: 'TL;DR' } };
          node.children.unshift({
            type: 'paragraph', data: { hProperties: { className: ['tldr-title'] } },
            children: [{ type: 'text', value: '💡 TL;DR' }],
          });
        }

        // A standalone image followed by an italic "Figure N:" / "图 N：" caption.
        const next = parent.children[index + 1];
        const caption = next?.type === 'paragraph' && next.children.length === 1
          && next.children[0].type === 'emphasis' ? next.children[0] : null;
        const label = caption?.children[0]?.value?.match(/^(?:图\s*(\d+)|Figure\s+(\d+))\s*[:：.]/i);
        if (node.type === 'paragraph' && node.children.length === 1
          && first?.type === 'image' && label) {
          node.data = { hName: 'figure', hProperties: { id: `figure-${label[1] || label[2]}` } };
          node.children.push({
            type: 'paragraph', data: { hName: 'figcaption' }, children: caption.children,
          });
          parent.children.splice(index + 1, 1);
        }
        walk(node);
      }
    }
    walk(tree);
  };
}
