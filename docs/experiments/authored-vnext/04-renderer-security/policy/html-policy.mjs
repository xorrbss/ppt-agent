import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const moduleDirectory = path.dirname(fileURLToPath(import.meta.url));
const DEFAULT_POLICY_PATH = path.join(moduleDirectory, "allowlist.policy.json");

const EVENT_HANDLER = /^on/i;
const CSS_IMPORT = /@import\b/i;
const CSS_URL = /url\s*\(/i;
const CSS_FONT = /@font-face\b|(?:^|[;{])\s*font(?:-family)?\s*:/i;
const CSS_EXECUTION = /expression\s*\(|-moz-binding\s*:|behavior\s*:|javascript\s*:/i;
const EXECUTION_TOKEN = /\b(?:child_process|ipcRenderer|ipcMain|process\.env|require\s*\(|Deno\.|Bun\.|window\.open\s*\(|document\.write\s*\()/i;
const TIMEOUT_TOKEN = /\bwhile\s*\(\s*true\s*\)|\bfor\s*\(\s*;\s*;\s*\)|Atomics\.wait\s*\(/i;
const TRAVERSAL_TOKEN = /(?:^|[\\/])\.\.(?:[\\/]|$)|%2e%2e(?:%2f|%5c)/i;
const LOOPBACK_HOST = /^(?:localhost|127(?:\.\d{1,3}){3}|\[?::1\]?)$/i;

export function loadPolicy(policyPath = DEFAULT_POLICY_PATH) {
  return JSON.parse(fs.readFileSync(policyPath, "utf8"));
}

function decodeEntities(value) {
  const decodeCodePoint = (digits, radix) => {
    const codePoint = Number.parseInt(digits, radix);
    return Number.isInteger(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff
      ? String.fromCodePoint(codePoint)
      : "\ufffd";
  };
  return value
    .replace(/&#x([0-9a-f]+);?/gi, (_, hex) => decodeCodePoint(hex, 16))
    .replace(/&#([0-9]+);?/g, (_, decimal) => decodeCodePoint(decimal, 10))
    .replace(/&quot;/gi, '"')
    .replace(/&#39;|&apos;/gi, "'")
    .replace(/&lt;/gi, "<")
    .replace(/&gt;/gi, ">")
    .replace(/&colon;/gi, ":")
    .replace(/&tab;/gi, "\t")
    .replace(/&newline;/gi, "\n")
    .replace(/&amp;/gi, "&");
}

function parseAttributes(raw) {
  const attributes = [];
  const attributePattern = /([^\s=/>]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'=<>`]+)))?/g;
  let match;
  while ((match = attributePattern.exec(raw)) !== null) {
    attributes.push({
      name: match[1].toLowerCase(),
      value: decodeEntities(match[2] ?? match[3] ?? match[4] ?? ""),
    });
  }
  return attributes;
}

export function parseHtml(source) {
  const root = { type: "root", children: [], parent: null, parseIssues: [] };
  const stack = [root];
  const tokenPattern = /<!--[\s\S]*?-->|<![^>]*>|<\/?[A-Za-z][^>]*>|[^<]+|</g;
  let match;
  while ((match = tokenPattern.exec(source)) !== null) {
    const token = match[0];
    const parent = stack.at(-1);
    if (token.startsWith("<!--") || /^<!/i.test(token)) {
      continue;
    }
    if (token.startsWith("</")) {
      const name = token.slice(2, -1).trim().toLowerCase();
      let matched = false;
      for (let index = stack.length - 1; index > 0; index -= 1) {
        if (stack[index].name === name) {
          for (const implicitlyClosed of stack.slice(index + 1)) {
            root.parseIssues.push(`Unclosed tag <${implicitlyClosed.name}> before </${name}>`);
          }
          stack.length = index;
          matched = true;
          break;
        }
      }
      if (!matched) root.parseIssues.push(`Unexpected closing tag </${name}>`);
      continue;
    }
    if (token.startsWith("<") && token !== "<") {
      const tagMatch = token.match(/^<\s*([^\s/>]+)([\s\S]*?)\/?\s*>$/);
      if (!tagMatch) {
        parent.children.push({ type: "text", value: token, parent });
        continue;
      }
      const element = {
        type: "element",
        name: tagMatch[1].toLowerCase(),
        attributes: parseAttributes(tagMatch[2]),
        children: [],
        parent,
      };
      parent.children.push(element);
      if (!token.endsWith("/>") && !["area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"].includes(element.name)) {
        stack.push(element);
      }
      continue;
    }
    parent.children.push({ type: "text", value: token, parent });
  }
  for (const unclosed of stack.slice(1)) root.parseIssues.push(`Unclosed tag <${unclosed.name}>`);
  return root;
}

function walk(node, visitor, depth = 0, pathParts = []) {
  visitor(node, depth, pathParts);
  node.children?.forEach((child, index) => {
    const label = child.type === "element" ? child.name : "#text";
    walk(child, visitor, depth + 1, [...pathParts, `${label}[${index}]`]);
  });
}

function addIssue(issues, ruleId, message, nodePath, evidence = undefined) {
  issues.push({ ruleId, severity: "error", message, path: `/${nodePath.join("/")}`, ...(evidence ? { evidence } : {}) });
}

function classifyUrl(value, policy) {
  const normalized = value.trim();
  const compact = normalized.replace(/[\u0000-\u0020]+/g, "");
  const lower = compact.toLowerCase();
  const findings = [];
  if (!normalized) return findings;
  if (normalized.includes(policy.reservedMetadataToken)) findings.push("U003");
  if (TRAVERSAL_TOKEN.test(compact)) findings.push("P001");
  if (lower.startsWith("//")) findings.push("U005");
  if (/^(?:javascript|vbscript):/i.test(compact)) findings.push("U006");
  if (/^file:/i.test(compact)) findings.push("U001");
  if (/^https?:/i.test(compact)) {
    findings.push("U004");
    try {
      const parsed = new URL(compact);
      if (LOOPBACK_HOST.test(parsed.hostname)) findings.push("U002");
    } catch {
      findings.push("U007");
    }
  }
  const scheme = compact.match(/^([a-z][a-z0-9+.-]*):/i)?.[1]?.toLowerCase();
  if (scheme && !policy.allowedUrlSchemes.includes(scheme) && !["file", "http", "https", "javascript", "vbscript"].includes(scheme)) {
    findings.push("U007");
  }
  if (scheme === "data") {
    const mediaType = lower.slice(5).split(/[;,]/, 1)[0];
    if (!policy.allowedDataMediaTypes.includes(mediaType)) findings.push("U008");
  }
  return [...new Set(findings)];
}

function cssDeclarations(block) {
  return block.split(";").map((declaration) => declaration.trim()).filter(Boolean);
}

function inspectCss(css, policy, issues, nodePath, inline = false) {
  const normalized = css.replace(/\/\*[\s\S]*?\*\//g, "");
  if (normalized.includes("\\")) addIssue(issues, "C008", "CSS escapes are forbidden", nodePath);
  if (CSS_IMPORT.test(normalized)) addIssue(issues, "C001", "CSS @import is forbidden", nodePath);
  if (CSS_URL.test(normalized)) addIssue(issues, "C002", "CSS url() is forbidden", nodePath);
  if (CSS_FONT.test(normalized)) addIssue(issues, "C003", "Authored font loading/declaration is forbidden", nodePath);
  if (CSS_EXECUTION.test(normalized)) addIssue(issues, "C004", "Executable or binding CSS is forbidden", nodePath);
  if (normalized.includes(policy.reservedMetadataToken)) addIssue(issues, "U003", "Reserved metadata endpoint token was referenced", nodePath);
  if (TRAVERSAL_TOKEN.test(normalized)) addIssue(issues, "P001", "Path traversal sequence was referenced", nodePath);
  for (const atRule of normalized.matchAll(/@([a-z-]+)/gi)) {
    addIssue(issues, "C006", `CSS at-rule @${atRule[1]} is forbidden`, nodePath);
  }
  const blocks = inline ? [normalized] : [...normalized.matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => {
    const selector = match[1].trim();
    if (!/^(?:[.#]?[a-z][a-z0-9_-]*)(?:\s*,\s*(?:[.#]?[a-z][a-z0-9_-]*))*$/i.test(selector)) {
      addIssue(issues, "C007", `CSS selector is not allowlisted: ${selector}`, nodePath);
    }
    return match[2];
  });
  if (!inline && normalized.trim() && blocks.length === 0 && !normalized.trim().startsWith("@")) {
    addIssue(issues, "C009", "CSS could not be parsed into declaration blocks", nodePath);
  }
  for (const block of blocks) {
    for (const declaration of cssDeclarations(block)) {
      const colon = declaration.indexOf(":");
      if (colon <= 0) {
        addIssue(issues, "C009", "Malformed CSS declaration", nodePath);
        continue;
      }
      const property = declaration.slice(0, colon).trim().toLowerCase();
      if (!policy.allowedCssProperties.includes(property)) {
        addIssue(issues, "C005", `CSS property is not allowlisted: ${property}`, nodePath);
      }
    }
  }
  const urlPattern = /url\s*\(\s*["']?([^"')\s]+)["']?\s*\)/gi;
  let urlMatch;
  while ((urlMatch = urlPattern.exec(normalized)) !== null) {
    for (const ruleId of classifyUrl(urlMatch[1], policy)) {
      addIssue(issues, ruleId, `Forbidden CSS URL: ${ruleId}`, nodePath);
    }
    if (urlMatch[1].toLowerCase().startsWith("data:") && Buffer.byteLength(urlMatch[1], "utf8") > policy.limits.maxDataUrlBytes) {
      addIssue(issues, "R006", "Inline CSS data URL exceeds byte limit", nodePath);
    }
  }
}

export function lintHtml(source, policy = loadPolicy()) {
  const issues = [];
  const sourceBytes = Buffer.byteLength(source, "utf8");
  if (sourceBytes > policy.limits.maxSourceBytes) {
    addIssue(issues, "R001", `Source is ${sourceBytes} bytes; limit is ${policy.limits.maxSourceBytes}`, []);
  }
  const tree = parseHtml(source);
  for (const parseIssue of tree.parseIssues) addIssue(issues, "H004", parseIssue, []);
  let nodeCount = 0;
  walk(tree, (node, depth, nodePath) => {
    if (node.type === "root") return;
    nodeCount += 1;
    if (depth > policy.limits.maxDepth) addIssue(issues, "R003", `DOM depth ${depth} exceeds limit`, nodePath);
    if (node.type === "text") {
      if (Buffer.byteLength(node.value, "utf8") > policy.limits.maxTextBytes) addIssue(issues, "R004", "Text node exceeds byte limit", nodePath);
      if (EXECUTION_TOKEN.test(node.value)) addIssue(issues, "X001", "Process, IPC, or dynamic execution token found", nodePath);
      if (TIMEOUT_TOKEN.test(node.value)) addIssue(issues, "X002", "Crash/timeout loop token found", nodePath);
      if (node.parent?.name === "style") inspectCss(node.value, policy, issues, nodePath, false);
      return;
    }
    if (!policy.allowedTags.includes(node.name)) addIssue(issues, "H001", `Tag <${node.name}> is not allowlisted`, nodePath);
    if (node.attributes.length > policy.limits.maxAttributesPerElement) addIssue(issues, "R005", "Element has too many attributes", nodePath);
    for (const attribute of node.attributes) {
      const allowed = policy.globalAttributes.includes(attribute.name) || (policy.tagAttributes[node.name] ?? []).includes(attribute.name);
      if (EVENT_HANDLER.test(attribute.name)) addIssue(issues, "H002", `Event handler ${attribute.name} is forbidden`, nodePath);
      else if (!allowed) addIssue(issues, "H003", `Attribute ${attribute.name} is not allowlisted on <${node.name}>`, nodePath);
      if (attribute.name === "style") inspectCss(attribute.value, policy, issues, nodePath, true);
      if (policy.urlAttributes.includes(attribute.name)) {
        for (const ruleId of classifyUrl(attribute.value, policy)) addIssue(issues, ruleId, `Forbidden URL in ${attribute.name}: ${ruleId}`, nodePath);
        if (attribute.value.toLowerCase().startsWith("data:") && Buffer.byteLength(attribute.value, "utf8") > policy.limits.maxDataUrlBytes) {
          addIssue(issues, "R006", "Inline data URL exceeds byte limit", nodePath);
        }
      } else {
        if (attribute.value.includes(policy.reservedMetadataToken)) addIssue(issues, "U003", "Reserved metadata endpoint token was referenced", nodePath);
        if (TRAVERSAL_TOKEN.test(attribute.value)) addIssue(issues, "P001", "Path traversal sequence was referenced", nodePath);
      }
      if (["width", "height"].includes(attribute.name)) {
        const dimension = Number.parseInt(attribute.value, 10);
        if (!Number.isFinite(dimension) || dimension < 0 || dimension > policy.limits.maxImageDimension) {
          addIssue(issues, "R007", `${attribute.name} exceeds image dimension limit`, nodePath);
        }
      }
      if (EXECUTION_TOKEN.test(attribute.value)) addIssue(issues, "X001", "Process, IPC, or dynamic execution token found", nodePath);
      if (TIMEOUT_TOKEN.test(attribute.value)) addIssue(issues, "X002", "Crash/timeout loop token found", nodePath);
    }
  });
  if (nodeCount > policy.limits.maxNodes) addIssue(issues, "R002", `DOM has ${nodeCount} nodes; limit is ${policy.limits.maxNodes}`, []);
  return { ok: issues.length === 0, issues, stats: { sourceBytes, nodeCount } };
}

function escapeText(value) {
  return value.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");
}

function escapeAttribute(value) {
  return escapeText(value).replaceAll('"', "&quot;");
}

function filterCssDeclarations(block, policy) {
  return cssDeclarations(block).flatMap((declaration) => {
    const colon = declaration.indexOf(":");
    if (colon <= 0) return [];
    const property = declaration.slice(0, colon).trim().toLowerCase();
    return policy.allowedCssProperties.includes(property) ? [`${property}:${declaration.slice(colon + 1).trim()}`] : [];
  }).join(";");
}

function sanitizeCss(css, policy, inline = false) {
  if (css.includes("\\")) return "";
  const stripped = css
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/@import[\s\S]*?(?:;|$)/gi, "")
    .replace(/@font-face\s*\{[\s\S]*?\}/gi, "")
    .replace(/url\s*\([^)]*\)/gi, "none")
    .replace(/font(?:-family)?\s*:[^;}]*(?:;|$)/gi, "")
    .replace(/(?:expression\s*\([^)]*\)|-moz-binding\s*:[^;}]*(?:;|$)|behavior\s*:[^;}]*(?:;|$))/gi, "");
  if (inline) return filterCssDeclarations(stripped, policy);
  return [...stripped.matchAll(/([^{}]+)\{([^{}]*)\}/g)].flatMap((match) => {
    const selector = match[1].trim();
    if (!/^(?:[.#]?[a-z][a-z0-9_-]*)(?:\s*,\s*(?:[.#]?[a-z][a-z0-9_-]*))*$/i.test(selector)) return [];
    const declarations = filterCssDeclarations(match[2], policy);
    return declarations ? [`${selector}{${declarations}}`] : [];
  }).join("");
}

function renderSanitized(node, policy, state, depth = 0) {
  if (state.nodes >= policy.limits.maxNodes || depth > policy.limits.maxDepth) return "";
  if (node.type === "text") {
    state.nodes += 1;
    const bounded = Buffer.from(node.value, "utf8").subarray(0, policy.limits.maxTextBytes).toString("utf8");
    return escapeText(node.parent?.name === "style" ? sanitizeCss(bounded, policy, false) : bounded);
  }
  if (node.type === "root") return node.children.map((child) => renderSanitized(child, policy, state, depth)).join("");
  if (policy.dropWithContents.includes(node.name)) return "";
  if (!policy.allowedTags.includes(node.name)) return node.children.map((child) => renderSanitized(child, policy, state, depth)).join("");
  state.nodes += 1;
  const permitted = new Set([...policy.globalAttributes, ...(policy.tagAttributes[node.name] ?? [])]);
  const attributes = [];
  for (const attribute of node.attributes.slice(0, policy.limits.maxAttributesPerElement)) {
    if (!permitted.has(attribute.name) || EVENT_HANDLER.test(attribute.name)) continue;
    let value = attribute.value;
    if (policy.urlAttributes.includes(attribute.name) && classifyUrl(value, policy).length > 0) continue;
    if (policy.urlAttributes.includes(attribute.name) && value.toLowerCase().startsWith("data:") && Buffer.byteLength(value, "utf8") > policy.limits.maxDataUrlBytes) continue;
    if (attribute.name === "style") value = sanitizeCss(value, policy, true);
    if (["width", "height"].includes(attribute.name)) {
      const dimension = Number.parseInt(value, 10);
      if (!Number.isFinite(dimension) || dimension < 0 || dimension > policy.limits.maxImageDimension) continue;
    }
    if (EXECUTION_TOKEN.test(value) || TIMEOUT_TOKEN.test(value) || TRAVERSAL_TOKEN.test(value) || value.includes(policy.reservedMetadataToken)) continue;
    attributes.push(`${attribute.name}="${escapeAttribute(value)}"`);
  }
  const opening = attributes.length > 0 ? `<${node.name} ${attributes.join(" ")}>` : `<${node.name}>`;
  if (policy.voidTags.includes(node.name)) return opening;
  const children = node.children.map((child) => renderSanitized(child, policy, state, depth + 1)).join("");
  return `${opening}${children}</${node.name}>`;
}

export function sanitizeHtml(source, policy = loadPolicy()) {
  return renderSanitized(parseHtml(source), policy, { nodes: 0 });
}

function main(argv) {
  const [command, inputPath, outputPath] = argv;
  if (!["lint", "sanitize"].includes(command) || !inputPath) {
    console.error("Usage: node html-policy.mjs <lint|sanitize> INPUT.html [OUTPUT.html]");
    return 2;
  }
  const source = fs.readFileSync(path.resolve(inputPath), "utf8");
  if (command === "lint") {
    const result = lintHtml(source);
    console.log(JSON.stringify(result, null, 2));
    return result.ok ? 0 : 1;
  }
  const output = sanitizeHtml(source);
  if (outputPath) fs.writeFileSync(path.resolve(outputPath), output, "utf8");
  else process.stdout.write(output);
  return 0;
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  process.exitCode = main(process.argv.slice(2));
}
