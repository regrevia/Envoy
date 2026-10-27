/**
 * Envoy for DeepSeek Harness.
 *
 * Registers the DeepSeek Harness adaptation of Envoy's Agent Skill on the
 * public `ctx.skills` service. The companion `cordis.patch.yml` mounts
 * `envoy mcp` through `@deepseek-ai/dsh-mcp-client`; this module deliberately
 * imports nothing from the Harness packages so it keeps working across
 * profiles and Harness releases.
 *
 * It also installs one `agent/pre-step` waterfall listener. On the first step
 * of a turn, when the freshly claimed user message carries a public http(s)
 * link, the listener prepends a user-role routing notice that tells the model
 * to read that link through the Envoy MCP bridge before a built-in web fetch.
 * The notice is host-neutral policy: it also says Envoy does not replace the
 * host's native web search, and that video/audio and OCR evidence are what the
 * bridge adds. The listener never mutates the incoming payload; it only
 * extends the decision produced by the default handler, once per turn.
 *
 * The skill body and candidate metadata are generated from
 * `integrations/skills/envoy/SKILL.md` by
 * `scripts/build_envoy_dsh_plugin.py`. Edit the canonical skill, then
 * regenerate; do not hand-edit the files under `skill/`.
 *
 * The URL extraction, private-destination filtering, opt-out markers, and the
 * policy wording mirror `packages/envoy-cli/src/envoy_cli/hooks.py`, the shared
 * host-neutral module used by the Claude Code and Codex adapters. Keep the two
 * in sync when the policy changes.
 */
import { randomUUID } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'

export const name = 'envoy-dsh'
export const inject = ['skills']

const PROVIDER_NAME = 'envoy-dsh'
// `@deepseek-ai/dsh-skill` exports BUNDLED_SKILL_RANK = 600, but that package
// is supplied by the Harness installation rather than by every profile. Keep
// the numeric value local so the bundle has no undeclared runtime imports.
const BUNDLED_SKILL_RANK = 600
const SKILL_DIR_URL = new URL('./skill/', import.meta.url)
const SKILL_BODY_URL = new URL('./skill/envoy.md', import.meta.url)
const MANIFEST = JSON.parse(
  readFileSync(new URL('./skill/manifest.json', import.meta.url), 'utf8'),
)

const RESOURCE_BASE = {
  kind: 'directory',
  path: fileURLToPath(SKILL_DIR_URL),
}

const CANDIDATE = {
  name: MANIFEST.name,
  description: MANIFEST.description,
  invocation: {
    modelInvocable: true,
    userInvocable: true,
  },
  provider: PROVIDER_NAME,
  source: 'bundled',
  resourceBase: RESOURCE_BASE,
  rank: BUNDLED_SKILL_RANK,
  locator: SKILL_BODY_URL,
}

const provider = {
  name: PROVIDER_NAME,
  list: () => Promise.resolve([CANDIDATE]),
  async get(_candidate) {
    return {
      name: CANDIDATE.name,
      description: CANDIDATE.description,
      invocation: CANDIDATE.invocation,
      provider: CANDIDATE.provider,
      source: CANDIDATE.source,
      resourceBase: RESOURCE_BASE,
      content: await readFile(SKILL_BODY_URL, 'utf8'),
    }
  },
}

// ---------------------------------------------------------------------------
// Link routing
//
// A turn's first `agent/pre-step` call claims the pending inbox messages, so a
// listener sees exactly the input admitted for this turn; later steps of the
// same turn claim nothing. That is the natural "once per turn" boundary, and a
// marker scan of the admitted messages is the second guard for callers that
// re-run a step with an already-extended decision.
// ---------------------------------------------------------------------------

/** Prefix that identifies an injected routing notice in message text. */
export const LINK_ROUTING_MARKER = '[envoy-link-routing]'
/** Alias for parity with the shared module's `HOOK_MARKER`. */
export const HOOK_MARKER = LINK_ROUTING_MARKER
/** `source.kind` of the injected user message; also used for idempotency. */
export const LINK_ROUTING_SOURCE_KIND = 'envoy-link-routing'
/** The Harness loop numbers a turn's first step as 1. */
const LINK_ROUTING_FIRST_STEP = 1

// A URL ends at whitespace, at a quote/angle/backtick, or at CJK punctuation
// glued to it in running prose. Full-width brackets are handled here too; ASCII
// brackets stay in the match because they can belong to the URL itself.
// This mirrors hooks.py `_URL_RE`; do not add a `u`-flag range class: under
// `/iu` a range such as \u0080-\uffff is case-folded into the negated set and
// would start excluding plain letters like `s` and `k`.
const PUBLIC_URL_RE = /https?:\/\/[^\s<>"'`，。；：！？、（）【】「」《》]+/gi
const TRAILING_PUNCTUATION = '.,;:!?，。；：！？、'
const BRACKET_OPENERS = {
  ')': '(',
  ']': '[',
  '}': '{',
  '）': '（',
  '】': '【',
  '」': '「',
  '》': '《',
}
const OPT_OUT_MARKERS = [
  '不要用 envoy',
  '不要使用 envoy',
  '不用 envoy',
  '别用 envoy',
  "don't use envoy",
  'do not use envoy',
  'no envoy',
]

/** Cut trailing characters that belong to the sentence, not to the URL. */
function rstripChars(value, chars) {
  let end = value.length
  while (end > 0 && chars.includes(value[end - 1])) end -= 1
  return value.slice(0, end)
}

function countChar(value, char) {
  return value.split(char).length - 1
}

/**
 * Strip sentence punctuation and unbalanced closing brackets from a raw match,
 * mirroring hooks.py `_strip_url`.
 * @param {string} raw
 * @returns {string}
 */
function stripUrl(raw) {
  let url = rstripChars(raw, TRAILING_PUNCTUATION)
  while (url.length > 0) {
    const close = url[url.length - 1]
    const open = BRACKET_OPENERS[close]
    if (open === undefined) break
    if (countChar(url, open) >= countChar(url, close)) break
    url = rstripChars(url.slice(0, -1), TRAILING_PUNCTUATION)
  }
  return url
}

function parseIpv4(host) {
  const parts = host.split('.')
  if (parts.length !== 4) return null
  const octets = []
  for (const part of parts) {
    if (!/^\d{1,3}$/u.test(part)) return null
    const octet = Number(part)
    if (octet > 255) return null
    octets.push(octet)
  }
  return octets
}

/** IPv4 rejection set mirrored from Python `ipaddress` on CPython 3.9. */
function isPublicIpv4([a, b, c, d]) {
  if (a === 0 || a === 10 || a === 127) return false
  if (a === 169 && b === 254) return false
  if (a === 172 && b >= 16 && b <= 31) return false
  if (a === 192 && b === 0 && c === 0) return false
  if (a === 192 && b === 0 && c === 2) return false
  if (a === 192 && b === 168) return false
  if (a === 198 && (b === 18 || b === 19)) return false
  if (a === 198 && b === 51 && c === 100) return false
  if (a === 203 && b === 0 && c === 113) return false
  // 224.0.0.0/4 multicast and 240.0.0.0/4 reserved (covers 255.255.255.255).
  if (a >= 224) return false
  return d >= 0
}

function ipv6Hextet(host, index) {
  const groups = host.split(':')
  if (index >= groups.length) return 0
  const group = groups[index]
  if (group === '') return 0
  const value = Number.parseInt(group, 16)
  return Number.isNaN(value) ? 0 : value
}

/**
 * IPv6 rejection approximated from Python `ipaddress` on CPython 3.9: only
 * global unicast (2000::/3) minus the 2001::/23 and 2001:db8::/32 reserved
 * ranges is publicly routable.
 */
function isPublicIpv6(host) {
  if (host === '::' || host === '::1') return false
  const first = host.startsWith('::') ? 0 : ipv6Hextet(host, 0)
  if (first < 0x2000 || first > 0x3fff) return false
  if (first === 0x2001) {
    const second = ipv6Hextet(host, 1)
    if (second <= 0x01ff) return false
    if (second === 0x0db8) return false
  }
  return true
}

/**
 * Whether a URL points at a public, remotely fetchable destination. Loopback,
 * private, link-local, multicast, and intranet names are rejected; they belong
 * to the host's own browser tools.
 * @param {string} url
 * @returns {boolean}
 */
export function isPublicUrl(url) {
  if (typeof url !== 'string' || url.length === 0) return false
  let parsed
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') return false
  const host = parsed.hostname
    .replace(/^\[/u, '')
    .replace(/\]$/u, '')
    .replace(/\.+$/u, '')
    .toLowerCase()
  if (host === '') return false
  if (
    host === 'localhost' ||
    host === 'localhost.localdomain' ||
    host === '0.0.0.0' ||
    host.endsWith('.localhost')
  ) {
    return false
  }
  if (host.endsWith('.local') || host.endsWith('.internal')) return false
  if (host.endsWith('.home.arpa')) return false
  const ipv4 = parseIpv4(host)
  if (ipv4 !== null) return isPublicIpv4(ipv4)
  if (host.includes(':')) return isPublicIpv6(host)
  // A single-label name is an intranet host, not a public destination.
  return host.includes('.')
}

const CODE_HOSTS = ['github.com', 'gitlab.com', 'bitbucket.org', 'codeberg.org']
const CODE_PATH_RE = /^\/[^/]+\/[^/]+\/(?:-\/)?(?:commit|commits|blob|tree|raw|pull|pulls|pull-requests|merge_requests|issues|compare)(?:\/|$)/iu

/**
 * Whether a URL is a source-code page whose diff Envoy cannot return. Mirrors
 * hooks.py `is_code_host_diff_url`: commit, file, tree, pull-request, and issue
 * pages belong to `git`/`gh`, not to the reader.
 * @param {string} url
 * @returns {boolean}
 */
export function isCodeHostDiffUrl(url) {
  if (typeof url !== 'string' || url.length === 0) return false
  let parsed
  try {
    parsed = new URL(url)
  } catch {
    return false
  }
  let host = parsed.hostname.toLowerCase().replace(/\.+$/u, '')
  if (host.startsWith('www.')) host = host.slice(4)
  if (host === 'gist.github.com') return false
  if (!CODE_HOSTS.includes(host) && !CODE_HOSTS.some((item) => host.endsWith(`.${item}`))) {
    return false
  }
  return CODE_PATH_RE.test(parsed.pathname || '')
}

/**
 * Extract unique public http(s) URLs in first-seen order. Mirrors hooks.py
 * `extract_public_urls`: deduplication is case-insensitive while the returned
 * spelling is the first occurrence.
 * @param {string} text
 * @returns {string[]}
 */
export function extractPublicUrls(text) {
  if (typeof text !== 'string' || text.length === 0) return []
  const seen = new Set()
  const urls = []
  for (const match of text.matchAll(PUBLIC_URL_RE)) {
    const url = stripUrl(match[0])
    if (url.length === 0 || !isPublicUrl(url)) continue
    const key = url.toLowerCase()
    if (seen.has(key)) continue
    seen.add(key)
    urls.push(url)
  }
  return urls
}

/**
 * Alias kept for callers that read better as detection.
 * @type {typeof extractPublicUrls}
 */
export const detectPublicUrls = extractPublicUrls

/**
 * Whether the user asked to bypass Envoy. Mirrors hooks.py `_opted_out`.
 * @param {string} text
 * @returns {boolean}
 */
export function isOptedOut(text) {
  const lowered = typeof text === 'string' ? text.toLowerCase() : ''
  const compact = lowered.replace(/\s+/g, '')
  return OPT_OUT_MARKERS.some(
    (marker) => lowered.includes(marker) || compact.includes(marker.replace(/\s+/g, '')),
  )
}

/**
 * Inspect one prompt for links and an explicit opt-out. Mirrors hooks.py
 * `inspect_prompt`; `shouldRoute` is true only for a non-empty URL list with no
 * opt-out.
 * @param {string} text
 * @returns {{urls: string[], optedOut: boolean, shouldRoute: boolean}}
 */
/**
 * Whether the prompt already carries its own Envoy instruction, such as a task
 * that is itself about Envoy. The notice restates the routing policy, so
 * appending it to such a turn spends context without changing behaviour.
 * @param {string} text
 * @returns {boolean}
 */
export function mentionsEnvoyTooling(text) {
  return (
    typeof text === 'string' &&
    /envoy[-_]?(read|expand|search|replay|mcp|link-routing|dsh)|mcp__envoy__|envoy-link-routing/iu.test(
      text,
    )
  )
}

/**
 * Inspect one prompt for links and an explicit opt-out. Mirrors hooks.py
 * `inspect_prompt`; `shouldRoute` is true only for a non-empty URL list with no
 * opt-out.
 * @param {string} text
 * @returns {{urls: string[], optedOut: boolean, shouldRoute: boolean}}
 */
export function inspectPrompt(text) {
  const urls = extractPublicUrls(text)
  const optedOut = isOptedOut(text)
  return { urls, optedOut, shouldRoute: urls.length > 0 && !optedOut }
}

/**
 * Extract the public links that need routing. A link the prompt already ties
 * to an Envoy tool is dropped, because the turn states the intent itself.
 * @param {string} text
 * @returns {string[]}
 */
export function routableUrls(text) {
  if (typeof text !== 'string' || text.length === 0) return []
  if (mentionsEnvoyTooling(text)) return []
  return extractPublicUrls(text).filter((url) => !isCodeHostDiffUrl(url))
}

/**
 * Pick the policy language from the prompt: a CJK prompt gets the Chinese
 * policy, everything else the English one. Deterministic for a given input.
 * @param {string} text
 * @returns {'zh' | 'en'}
 */
export function detectRoutingLanguage(text) {
  return /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]/u.test(
    typeof text === 'string' ? text : '',
  )
    ? 'zh'
    : 'en'
}

/**
 * Declarative verdict tables exported from the Python module by
 * `scripts/export_link_verdicts.py`. Do not hand-edit the JSON: the Python
 * `read_support_verdict()` remains the single source of truth, and the export
 * script's `--check` mode fails CI when the copy drifts.
 */
const LINK_VERDICTS_URL = new URL('./link-verdicts.json', import.meta.url)
let linkVerdictTables
const PATH_REGEXPS = new Map()

function verdictTables() {
  linkVerdictTables ??= JSON.parse(readFileSync(LINK_VERDICTS_URL, 'utf8'))
  return linkVerdictTables
}

function matchesPath(pattern, path) {
  let regexp = PATH_REGEXPS.get(pattern)
  if (regexp === undefined) {
    regexp = new RegExp(pattern, 'i')
    PATH_REGEXPS.set(pattern, regexp)
  }
  return regexp.test(path)
}

/**
 * Whether Envoy is expected to read a URL: supported, unsupported, or unknown.
 * Mirrors Python `envoy_cli.platforms.read_support_verdict()`: static, offline,
 * no redirect resolution. Host/path data comes from link-verdicts.json.
 * @param {string} url
 * @returns {'supported' | 'unsupported' | 'unknown'}
 */
export function readSupportVerdict(url) {
  const tables = verdictTables()
  const supported = tables.supported
  const unsupported = tables.unsupported
  let parsed
  try {
    parsed = new URL(String(url ?? '').trim())
  } catch {
    return 'unknown'
  }
  if (parsed.protocol && parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    // mailto:, tel:, file:, ftp:, data: and app intents never turn into text.
    return 'unsupported'
  }
  let host = (parsed.hostname || '').toLowerCase()
  const path = parsed.pathname || ''
  if (!host) return 'unknown'
  if (host.startsWith('www.')) host = host.slice(4)

  if (unsupported.hosts.includes(host)) return 'unsupported'
  if (
    unsupported.douyin_live_hosts.includes(host) ||
    (supported.douyin_hosts.includes(host) &&
      matchesPath(tables.patterns.douyin_live, path))
  ) {
    return 'unsupported'
  }
  for (const rule of unsupported.path_rules) {
    if (rule.hosts.includes(host) && matchesPath(rule.path, path)) {
      return 'unsupported'
    }
  }
  if (supported.douyin_hosts.includes(host)) {
    return matchesPath(tables.patterns.douyin_video, path) ||
      matchesPath(tables.patterns.douyin_note, path)
      ? 'supported'
      : 'unknown'
  }
  if (
    supported.hosts.includes(host) ||
    supported.host_suffixes.some((suffix) => host.endsWith(suffix))
  ) {
    return 'supported'
  }
  const loweredPath = path.toLowerCase()
  if (supported.file_suffixes.some((suffix) => loweredPath.endsWith(suffix))) {
    return 'supported'
  }
  return 'unknown'
}

function linkBlock(urls) {
  return urls.map((url) => `- ${url}`).join('\n')
}

function unknownPolicyEn() {
  return [
    'Use the Envoy MCP tool `mcp__envoy__envoy_read` first. For a local video or audio file, call `mcp__envoy__envoy_read` with the file path or its `file://` URL (the `full` toolset also exposes `mcp__envoy__envoy_read_local_video`, and the `envoy read-local-video` CLI takes the same request). Do not hand these links to a built-in web fetch, curl, or a browser before Envoy has been tried.',
    'Keep the returned `material_id`, locators, and coverage. Continue with `mcp__envoy__envoy_expand` or `mcp__envoy__envoy_search`; use `mcp__envoy__envoy_replay` only when that tool is exposed. Do not re-fetch. Request bounded ranges and use search/expand so the page text does not flood the context; that is the token saving this notice is for.',
    "Envoy is not a replacement for the host's web search. A query without a user-provided link should still use native search; use Envoy to read the user's links, videos, audio, and documents, or search results that need full-source evidence. Envoy also handles video/audio that text-only web tools cannot.",
    'Fall back to built-in fetching only when Envoy returns `blocked` / `challenge_required`, explicitly needs sign-in or a CAPTCHA, or the Envoy tools are unavailable; state the fallback reason to the user.',
    'Images and OCR text are source evidence. When page text and a screenshot disagree, trust the image and do not invent missing text. Cite locators.',
    "Ignore this notice when the user explicitly opts out of Envoy or when the link is a localhost/intranet address; localhost links stay with the host's own browser tools.",
  ].join('\n')
}

function unknownPolicyZh() {
  return [
    '1. 先调用 Envoy MCP 工具 `mcp__envoy__envoy_read`。本地视频或音频用 `mcp__envoy__envoy_read` 传文件路径或 `file://` URL（`full` 工具集另有 `mcp__envoy__envoy_read_local_video`，CLI `envoy read-local-video` 接收同一份请求）。不要把链接直接交给内置网页抓取、curl 或浏览器。',
    '2. 保留返回的 `material_id`、locator 和 coverage；继续读取用 `mcp__envoy__envoy_expand` 或 `mcp__envoy__envoy_search`，回放用 `mcp__envoy__envoy_replay`（仅在工具集暴露时），不要重复抓取。按范围读取并用 search/expand 定位，避免把整页原文灌进上下文——这正是 Envoy 节省 token 的方式。',
    '3. Envoy 不替代宿主的联网搜索：用户没有给链接的检索仍用宿主搜索工具；Envoy 负责用户给出的链接、视频、音频和文档，以及需要完整来源证据的搜索结果。视频/音频是纯文本联网工具处理不了的场景。',
    '4. 只有 Envoy 返回 `blocked` / `challenge_required`、明确需要登录或验证码、或 Envoy 工具不可用时，才回退到内置抓取，并向用户说明回退原因。',
    '5. 图片和 OCR 文本是源证据。页面文字与截图冲突时以图片为准，不要臆造缺失文字；引用结论时带上 locator。',
    '6. 如果用户明确要求不用 Envoy，或链接是 localhost/内网地址，忽略本条。',
  ].join('\n')
}

/**
 * Build the model-facing routing policy for a prompt with public links.
 * Mirrors hooks.py `build_prompt_context(host='dsh')`: links are grouped by
 * `readSupportVerdict`, so a known login wall is not sent through Envoy and a
 * supported page is not handed to a native fetch. Every Envoy tool is named
 * with the `mcp__envoy__` prefix the Harness exposes.
 * @param {string[]} urls
 * @param {'zh' | 'en' | string} [language] defaults to the shared module's zh
 * @returns {string}
 */
export function buildRoutingText(urls, language = 'zh') {
  const groups = { supported: [], unsupported: [], unknown: [] }
  for (const url of new Set(urls)) groups[readSupportVerdict(url)].push(url)
  const parts = []
  if (String(language).toLowerCase().startsWith('en')) {
    parts.push(LINK_ROUTING_MARKER, 'This turn contains public link(s):')
    if (groups.supported.length > 0) {
      parts.push(
        'Read these links with `mcp__envoy__envoy_read`:\n' +
          linkBlock(groups.supported),
      )
      parts.push(
        'Call the Envoy MCP tool `mcp__envoy__envoy_read` for them and keep the returned `material_id`, locators, and coverage; continue with `mcp__envoy__envoy_expand` or `mcp__envoy__envoy_search` instead of re-fetching. Envoy also handles the video and audio these pages carry.',
      )
    }
    if (groups.unsupported.length > 0) {
      parts.push(
        "Do not call Envoy tools for these links; use the host's native web fetch or a browser instead, or state that they cannot be read:\n" +
          linkBlock(groups.unsupported),
      )
    }
    if (groups.unknown.length > 0) {
      parts.push(
        'Try `mcp__envoy__envoy_read` on these links first, then fall back to built-in fetching:\n' +
          linkBlock(groups.unknown),
      )
      parts.push(unknownPolicyEn())
    }
    return parts.join('\n\n')
  }
  parts.push(LINK_ROUTING_MARKER, '本轮用户消息包含公网链接：')
  if (groups.supported.length > 0) {
    parts.push('以下链接用 envoy_read 读：\n' + linkBlock(groups.supported))
    parts.push(
      '调用 Envoy MCP 工具 `mcp__envoy__envoy_read`，保留返回的 `material_id`、locator 和 coverage；继续读取用 `mcp__envoy__envoy_expand` 或 `mcp__envoy__envoy_search`，不要重复抓取。页面自带的视频/音频也交给 Envoy。',
    )
  }
  if (groups.unsupported.length > 0) {
    parts.push(
      '以下链接不要调用 envoy 工具，直接用宿主原生抓取（web fetch / 浏览器）或说明无法读取：\n' +
        linkBlock(groups.unsupported),
    )
  }
  if (groups.unknown.length > 0) {
    parts.push(
      '以下链接先试 envoy_read，失败再用宿主原生抓取：\n' +
        linkBlock(groups.unknown),
    )
    parts.push(unknownPolicyZh())
  }
  return parts.join('\n\n')
}

/** Alias for callers that describe the payload as a message. */
export const buildRoutingMessage = buildRoutingText

/**
 * Read the text of one Harness message (string content or text blocks).
 * @param {unknown} message
 * @returns {string}
 */
export function messageText(message) {
  const content = message?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  const parts = []
  for (const block of content) {
    if (
      block !== null &&
      typeof block === 'object' &&
      block.type === 'text' &&
      typeof block.text === 'string'
    ) {
      parts.push(block.text)
    }
  }
  return parts.join('\n')
}

/**
 * Whether a message is a routing notice this plugin already injected.
 * @param {unknown} message
 * @returns {boolean}
 */
export function isRoutingMessage(message) {
  if (message === null || typeof message !== 'object') return false
  if (message.source?.kind === LINK_ROUTING_SOURCE_KIND) return true
  return messageText(message).includes(LINK_ROUTING_MARKER)
}

/**
 * Build the user-role message injected before the claimed user messages.
 * `agent/pre-step` decisions only admit `role: 'user'` messages, so the notice
 * is a user message with a plugin-owned `source.kind`.
 * @param {string[]} urls
 * @param {{language?: string, id?: string}} [options]
 * @returns {{id: string, role: 'user', content: Array<{type: 'text', text: string}>, source: {kind: string}}}
 */
export function createRoutingUserMessage(urls, options = {}) {
  const language = options.language ?? 'zh'
  return {
    id: options.id ?? randomUUID(),
    role: 'user',
    content: [{ type: 'text', text: buildRoutingText(urls, language) }],
    source: { kind: LINK_ROUTING_SOURCE_KIND },
  }
}

/**
 * Decide whether this step is a turn's first step with newly claimed input.
 * `payload.step` is 1 for that first step; 0 and `undefined` are accepted so
 * the pure helper is also callable outside the loop.
 * @param {unknown} payload
 * @returns {boolean}
 */
export function shouldInjectLinkRouting(payload) {
  if (payload === null || typeof payload !== 'object') return false
  const step = payload.step
  if (
    step !== undefined &&
    step !== null &&
    step !== 0 &&
    step !== LINK_ROUTING_FIRST_STEP
  ) {
    return false
  }
  if (payload.signal?.aborted === true) return false
  return Array.isArray(payload.messages) && payload.messages.length > 0
}

/**
 * Extend one `agent/pre-step` decision with the Envoy routing notice.
 * Returns the given decision untouched when the turn is not eligible, when the
 * messages are already routed, when the user opted out, or when the claimed
 * messages carry no public link.
 * @param {{kind?: string, messages?: unknown[]}} decision
 * @param {{messages?: unknown[], step?: number, signal?: {aborted?: boolean}}} payload
 * @returns {object} the original decision or a new decision with one extra message
 */
export function injectLinkRouting(decision, payload) {
  if (decision === null || typeof decision !== 'object') return decision
  if (decision.kind !== 'enter') return decision
  if (!Array.isArray(decision.messages) || decision.messages.length === 0) {
    return decision
  }
  if (!shouldInjectLinkRouting(payload)) return decision
  const messages = payload.messages
  if (messages.some(isRoutingMessage)) return decision
  if (decision.messages.some(isRoutingMessage)) return decision
  const text = messages.map(messageText).join('\n')
  if (isOptedOut(text)) return decision
  const urls = routableUrls(text)
  if (urls.length === 0) return decision
  const routingMessage = createRoutingUserMessage(urls, {
    language: detectRoutingLanguage(text),
  })
  return { ...decision, messages: [routingMessage, ...decision.messages] }
}

/**
 * Register the Envoy skill provider synchronously, as `ctx.skills` requires,
 * and install the link-routing waterfall listener.
 * @param {import('cordis').Context} ctx
 */
export function apply(ctx) {
  ctx.skills.registerProvider(() => provider)
  if (typeof ctx.on !== 'function') return
  ctx.on('agent/pre-step', async (payload, next) => {
    const decision = await next()
    try {
      return injectLinkRouting(decision, payload)
    } catch (error) {
      // A routing notice must never break a turn: degrade to the untouched
      // decision, which is what the default handler produced.
      ctx.logger?.warn?.('envoy-dsh: link routing skipped', error)
      return decision
    }
  })
}
