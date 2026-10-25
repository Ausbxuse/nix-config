import mermaid from "mermaid";
import DOMPurify from "dompurify";

mermaid.initialize({ startOnLoad: false, securityLevel: "strict", suppressErrorRendering: true,
  theme: "base", look: "classic", htmlLabels: false, flowchart: { htmlLabels: false, useMaxWidth: true, minNodeWidth: 0, padding: 12, nodeSpacing: 28, rankSpacing: 24 },
  maxTextSize: 18000, maxEdges: 250, fontFamily: '"Maple Mono NF", monospace',
  themeVariables: { darkMode: true, background: "#191d2c", primaryColor: "#303548", primaryTextColor: "#dfdeec", primaryBorderColor: "#69708b", secondaryColor: "#293848", tertiaryColor: "#303b31", lineColor: "#b4befe", textColor: "#dfdeec", edgeLabelBackground: "#191d2c", fontSize: "14px" },
});
let counter = 0;
export async function draw(figure, source) {
  const target = figure.querySelector(".diagram-canvas");
  try {
    if (source.length > 18000 || /%%\{|^\s*---/m.test(source)) throw new Error("Diagram configuration is not supported in this reader.");
    const { svg } = await mermaid.render(`hub-diagram-${++counter}`, source);
    if (!figure.isConnected) return;
    target.innerHTML = DOMPurify.sanitize(svg, { USE_PROFILES: { svg: true, svgFilters: true }, FORBID_TAGS: ["foreignObject", "script", "image", "a", "iframe"], FORBID_ATTR: ["href", "xlink:href"] });
    for (const element of target.querySelectorAll("style,[style]")) {
      const text = element.tagName.toLowerCase() === "style" ? element.textContent : element.getAttribute("style");
      const urls = [...text.matchAll(/url\s*\(\s*([^)]*)\)/gi)].map(match => match[1].trim().replace(/^["']|["']$/g, ""));
      if (/@import|expression\s*\(/i.test(text) || urls.some(url => !url.startsWith("#"))) {
        if (element.tagName.toLowerCase() === "style") element.remove(); else element.removeAttribute("style");
      }
    }
  } catch {
    if (!figure.isConnected) return;
    target.textContent = "Couldn’t draw this diagram. Check the source below.";
    figure.querySelector(".diagram-source").hidden = false;
  }
}
