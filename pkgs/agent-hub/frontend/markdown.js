import MarkdownIt from "markdown-it";
import { katex } from "@mdit/plugin-katex";
import DOMPurify from "dompurify";
import { highlightCode, codeBlock } from './code.mjs';
import { fileLinks, localFilePath } from "./links.mjs";
import { highlightTerminal } from "./terminal.mjs";
import { createAssetPreviews, localAsset, attachMediaPoster, pruneMediaPosters } from "./assets.mjs";
import { artifactMarkdown, createArtifactReview } from './artifacts.mjs';

const md = new MarkdownIt({ html: false, breaks: false, linkify: true }).use(katex, {
  delimiters: "all", mathFence: true, trust: false, throwOnError: false, maxExpand: 500, maxSize: 20, strict: "ignore",
});
md.linkify.set({ fuzzyLink: false, fuzzyEmail: false });
md.use(fileLinks);
md.use(artifactMarkdown);
const escape = md.utils.escapeHtml;
const attachAssetPreview = createAssetPreviews((text, extension) => highlightCode(text, extension).html, (text, asset) => {
  const template = document.createElement('template');
  template.innerHTML = render(text, {filePreview:true});
  for (const link of template.content.querySelectorAll('a[href]')) {
    const path = localFilePath(link.getAttribute('href'));
    if (!path) continue;
    const base = asset.path.slice(0, asset.path.lastIndexOf('/') + 1);
    link.href = '/api/file?' + new URLSearchParams({agent_id:new URL(asset.file, location.origin).searchParams.get('agent_id'), path:path.startsWith('/') ? path : base + path});
  }
  return template.innerHTML;
});
const artifactReview=createArtifactReview(attachAssetPreview);
const originalFence = md.renderer.rules.fence;
md.renderer.rules.fence = (tokens, index, ...args) => {
  const token = tokens[index], language = token.info.trim().split(/\s+/)[0].slice(0, 40).toLowerCase();
  if (language === "math") return originalFence(tokens, index, ...args);
  if (language === "mermaid" && !args[1]?.filePreview) return `<figure class="md-diagram"><div class="code-toolbar"><span>Mermaid</span><button type="button" data-diagram-source>Source</button><button type="button" data-diagram-expand>Expand</button></div><div class="diagram-canvas" aria-label="Mermaid diagram"><p class="muted">Drawing diagram…</p></div><pre class="diagram-source" hidden><code>${escape(token.content)}</code></pre></figure>`;
  return codeBlock(token.content, token.info);
};
md.renderer.rules.code_block = (tokens, index) => codeBlock(tokens[index].content);
md.renderer.rules.table_open = () => '<div class="reader-table md-table" tabindex="0" role="region" aria-label="Table"><table>';
md.renderer.rules.table_close = () => '</table></div>';
md.renderer.rules.image = (tokens, index) => {
  const token = tokens[index], src = token.attrGet("src") || "";
  if (/\.(mp4|webm|ogv|mov|png|jpg|jpeg|webp|gif|svg|pdf)(?:[?#]|$)/i.test(src)) return `<a href="${escape(src)}" target="_blank" rel="noopener noreferrer">${escape(token.content || "Open file")}</a>`;
  return `<span class="markdown-image-label">${escape(token.content || "Image")}</span>`;
};
const link = md.renderer.rules.link_open || ((tokens, index, options, env, self) => self.renderToken(tokens, index, options));
md.renderer.rules.link_open = (tokens, index, ...args) => {
  tokens[index].attrSet("target", "_blank"); tokens[index].attrSet("rel", "noopener noreferrer");
  if (localFilePath(tokens[index].attrGet("href") || "")) tokens[index].attrJoin("class", "md-file-link");
  return link(tokens, index, ...args);
};
function render(text, options = {}) {
  return DOMPurify.sanitize(md.render(String(text || "").slice(0, options.filePreview ? 524288 : 180000), options), { USE_PROFILES: { html: true, mathMl: true, svg: true }, ADD_ATTR: ["target"], FORBID_TAGS: ["img", "image", "iframe", "form", "input", "style", "foreignObject"] });
}
let diagramModule;
function enhance(root) {
  pruneMediaPosters();
  artifactReview.enhance(root);
  for (const link of root.querySelectorAll('a[href]:not([data-link-ready])')) {
    const href = link.getAttribute("href");
    link.dataset.linkReady = "true";
    const mediaPath = localFilePath(href);
    const agentId = link.closest("[data-agent-id]")?.dataset.agentId;
    const isVideo = /\.(mp4|webm|ogv|mov)(?:[?#]|$)/i.test(href);
    const external = /^https:\/\//i.test(href);
    if (mediaPath) {
      link.classList.add("md-file-link");
      if (!agentId) {
        // A global supervisor reply has no reliable host for a relative file.
        const reference = document.createElement("span");
        reference.className = "md-file-reference";
        reference.append(...link.childNodes); link.replaceWith(reference);
        continue;
      }
      link.href = "/api/file?" + new URLSearchParams({agent_id:agentId,path:mediaPath});
      if (!isVideo) attachAssetPreview(link, href, agentId);
    }
    if (!isVideo || !external && !(mediaPath && agentId)) continue;
    // Keep the source anchor for the reader's DOM reconciliation, but present
    // only one player. Its native controls already provide fullscreen playback.
    link.hidden = true;
    const figure = document.createElement("figure");
    figure.className = "reader-video";
    const frame = document.createElement("div");
    frame.className = "reader-video-frame";
    const video = document.createElement("video");
    video.playsInline = true; video.preload = "none";
    const url = external ? href : "/api/media?" + new URLSearchParams({agent_id:agentId,path:mediaPath});
    if (!external) attachMediaPoster(video, localAsset(mediaPath, agentId));
    const label = link.textContent.trim() || "Video";
    video.setAttribute("aria-label", label);
    const play = document.createElement("button");
    play.type = "button"; play.className = "reader-video-play"; play.setAttribute("aria-label", "Play " + label);
    play.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg>';
    play.onclick = () => {
      play.hidden = true; video.controls = true; video.src = url;
      video.play().catch(() => {}); // Native controls remain usable if playback is denied.
    };
    const caption = document.createElement("figcaption");
    caption.textContent = label;
    caption.hidden = /^(?:watch(?: video| rollout)?|play(?: video)?|video|open(?: file| video)?)$/i.test(label);
    const error = document.createElement("p");
    error.className = "reader-video-error"; error.hidden = true; error.setAttribute("role", "status");
    const original = document.createElement("a");
    original.href = url; original.target = "_blank"; original.rel = "noopener noreferrer";
    original.dataset.linkReady = "true"; original.textContent = "Open video";
    error.append("Preview unavailable. ", original);
    video.addEventListener("error", () => { video.hidden = true; play.hidden = true; error.hidden = false; });
    frame.append(video, play, error);
    figure.append(frame, caption);
    link.after(figure);
  }
  // A sentence-ending mark after an embedded link belongs to its caption,
  // not on a separate line below the player. Reapply after DOM reconciliation.
  for (const figure of root.querySelectorAll('.reader-video')) {
    const tail = figure.nextSibling, caption = figure.querySelector('figcaption');
    if (tail?.nodeType === 3 && /^\s*[.!?;,]\s*$/.test(tail.nodeValue)) {
      const punctuation = tail.nodeValue.trim();
      if (!caption.hidden && !caption.textContent.endsWith(punctuation)) caption.textContent += punctuation;
      tail.remove();
    }
  }
  for (const figure of root.querySelectorAll(".md-diagram:not([data-started])")) {
    figure.dataset.started = "true";
    const source = figure.querySelector("code").textContent;
    (diagramModule ||= import("/vendor/diagram.js")).then(({ draw }) => draw(figure, source)).catch(() => {
      if (!figure.isConnected) return;
      figure.querySelector(".diagram-canvas").textContent = "Diagram unavailable. The source is below.";
      figure.querySelector(".diagram-source").hidden = false;
    });
  }
}
async function copy(text) {
  if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text);
  const previous = document.activeElement, input = document.createElement("textarea");
  input.className = "sr-only"; input.value = text; document.body.append(input); input.select();
  const copied = document.execCommand("copy"); input.remove(); previous?.focus({ preventScroll: true });
  if (!copied) throw new Error("Copy unavailable");
}
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-copy-code], [data-diagram-source], [data-diagram-expand]");
  if (!button) return;
  if (button.hasAttribute("data-copy-code")) {
    try { await copy(button.closest(".md-code-block").querySelector("code").textContent); button.textContent = "Copied"; }
    catch { button.textContent = "Select to copy"; }
    setTimeout(() => { if (button.isConnected) button.textContent = "Copy"; }, 1800);
  } else if (button.hasAttribute("data-diagram-source")) {
    const source = button.closest(".md-diagram").querySelector(".diagram-source"); source.hidden = !source.hidden;
    button.textContent = source.hidden ? "Source" : "Hide source";
  } else {
    const figure = button.closest(".md-diagram"), dialog = document.createElement("dialog");
    dialog.className = "diagram-dialog";
    dialog.innerHTML = '<div class="code-toolbar"><span>Diagram</span><button type="button" autofocus>Close</button></div><div class="diagram-large"></div>';
    const content = figure.querySelector("svg") || figure.querySelector(".diagram-source");
    const clone = content.cloneNode(true); clone.hidden = false;
    if (clone.tagName.toLowerCase() === "svg") { clone.style.width = `${Math.max(600, clone.viewBox.baseVal.width)}px`; clone.style.maxWidth = "none"; }
    dialog.querySelector(".diagram-large").append(clone); document.body.append(dialog);
    dialog.querySelector("button").onclick = () => dialog.close();
    dialog.onclose = () => { dialog.remove(); button.focus({ preventScroll: true }); };
    dialog.showModal();
  }
});
window.HubMarkdown = { render, enhance, codeBlock, highlightTerminal, isPreviewing: root=>attachAssetPreview.isOpenFor(root)||artifactReview.isOpenFor(root) };
