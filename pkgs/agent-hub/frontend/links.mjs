// Keep automatic file references conservative; the host still checks the project boundary.
const extensions = new Set("png jpg jpeg webp gif pdf txt md json jsonl ndjson csv tsv mp4 webm ogv mov nix py js jsx ts tsx mjs cjs sh bash zsh fish yaml yml toml ini cfg conf lock log c h cc hh cpp hpp rs go java rb lua sql css scss html xml svg tex rst patch diff ipynb".split(" "));
const filenames = new Set(["Makefile", "Dockerfile", "Justfile", "CMakeLists.txt", ".gitignore", ".gitattributes", ".editorconfig"]);

export function localFilePath(value) {
  let path;
  try { path = decodeURIComponent(value.split(/[?#]/)[0]).replace(/:\d+(?::\d+)?$/, ""); }
  catch { return null; }
  if (!path || /[\x00-\x1f\x7f]/.test(path) || /^[a-z][a-z0-9+.-]*:/i.test(path) || path.startsWith("//") || path.startsWith("~/")) return null;
  const name = path.split("/").at(-1);
  return filenames.has(name) || extensions.has(name.split(".").at(-1).toLowerCase()) && name.includes(".") ? path : null;
}

export function fileReferences(text) {
  const references = [];
  // Paths with spaces remain available through explicit Markdown links.
  const pattern = /(?<![\w@./:+~%-])(?:\.{0,2}\/|\/)?[\w@+.-]+(?:\/[\w@+.-]+)*(?::\d+(?::\d+)?)?(?:#L\d+(?:-L?\d+)?)?/g;
  for (const match of text.matchAll(pattern)) {
    const label = match[0].replace(/\.+$/, ""), path = localFilePath(label);
    if (path) references.push({ index: match.index, label, path });
  }
  return references;
}

export function fileLinks(md) {
  md.core.ruler.after("linkify", "local_files", (state) => {
    const textToken = (content) => { const token = new state.Token("text", "", 0); token.content = content; return token; };
    const linked = (content, path, label) => {
      const open = new state.Token("link_open", "a", 1);
      open.attrSet("href", md.normalizeLink(path));
      open.attrSet("title", label);
      return [open, content, new state.Token("link_close", "a", -1)];
    };
    for (const block of state.tokens) {
      if (block.type !== "inline") continue;
      const children = []; let linkDepth = 0;
      for (const token of block.children) {
        if (token.type === "link_open") linkDepth++;
        if (token.type === "link_close") { linkDepth--; children.push(token); continue; }
        if (linkDepth || !["text", "code_inline"].includes(token.type)) { children.push(token); continue; }
        if (token.type === "code_inline") {
          const path = localFilePath(token.content);
          children.push(...(path && /^[\w@./+:#%~-]+$/.test(token.content) ? linked(token, path, token.content) : [token]));
          continue;
        }
        let offset = 0;
        for (const reference of fileReferences(token.content)) {
          children.push(textToken(token.content.slice(offset, reference.index)), ...linked(textToken(reference.label), reference.path, reference.label));
          offset = reference.index + reference.label.length;
        }
        children.push(offset ? textToken(token.content.slice(offset)) : token);
      }
      block.children = children;
    }
  });
}
