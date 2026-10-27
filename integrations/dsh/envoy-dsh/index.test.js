import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

import {
  buildRoutingText,
  LINK_ROUTING_MARKER,
  readSupportVerdict,
  routableUrls,
} from './index.js'

test('readSupportVerdict classifies the three policies', () => {
  const cases = new Map([
    ['https://www.bilibili.com/video/BV1xx411c7mD', 'supported'],
    ['https://b23.tv/abcdefg', 'supported'],
    ['https://www.douyin.com/video/7300000000000000000', 'supported'],
    ['https://www.douyin.com/note/7300000000000000000', 'supported'],
    ['https://mp.weixin.qq.com/s/abcdefg', 'supported'],
    ['https://example.com/a.pdf', 'supported'],
    ['https://feeds.arstechnica.com/arstechnica/index', 'supported'],
    ['https://feeds.simplecast.com/54nAGcIl', 'supported'],
    ['https://discourse.mozilla.org/', 'supported'],
    ['https://v.youku.com/v_show/id_XNTk1ODQ0MjAw.html', 'supported'],
    ['https://www.npr.org/sections/technology/', 'supported'],
    ['https://www.cnbc.com/technology/', 'supported'],
    ['https://podcasts.apple.com/us/podcast/the-daily/id1200361736', 'supported'],
    ['https://open.spotify.com/episode/7makk4oTQel546B0PZlDM5', 'supported'],
    ['https://docs.anthropic.com/en/docs/overview', 'supported'],
    ['https://apnews.com/hub/technology', 'unsupported'],
    ['mailto:someone@example.com', 'unsupported'],
    ['file:///Users/apple/notes.md', 'unsupported'],
    ['https://x.com/OpenAI', 'unsupported'],
    ['https://mp.weixin.qq.com/', 'unsupported'],
    ['https://x.com/someone/status/1234567890', 'unsupported'],
    ['https://twitter.com/someone/status/1234567890', 'unsupported'],
    ['https://www.instagram.com/p/CxYzAbCdEfG/', 'unsupported'],
    ['https://www.linkedin.com/posts/someone-activity-123', 'unsupported'],
    ['https://live.douyin.com/123456', 'unsupported'],
    ['https://www.douyin.com/live/123456', 'unsupported'],
    ['https://t.me/+abcdefg', 'unsupported'],
    ['https://www.youtube.com/watch?v=dQw4w9WgXcQ', 'supported'],
    ['https://www.xiaohongshu.com/explore/abcdefg', 'unknown'],
    ['https://www.douyin.com/user/MS4wLjABAAAA', 'unknown'],
    ['https://example.com/a', 'unknown'],
    ['', 'unknown'],
  ])
  for (const [url, expected] of cases) {
    assert.equal(readSupportVerdict(url), expected, url)
  }
})

test('supported links route to envoy_read only', () => {
  const text = buildRoutingText(
    ['https://www.bilibili.com/video/BV1xx411c7mD'],
    'zh',
  )
  assert.ok(text.startsWith(LINK_ROUTING_MARKER))
  assert.match(text, /以下链接用 envoy_read 读：/)
  assert.ok(text.includes('https://www.bilibili.com/video/BV1xx411c7mD'))
  assert.ok(text.includes('mcp__envoy__envoy_read'))
  assert.doesNotMatch(text, /不要调用 envoy 工具/)
  assert.doesNotMatch(text, /先试 envoy_read/)
})

test('unsupported links route to native fetch without envoy', () => {
  const text = buildRoutingText(
    ['https://www.instagram.com/p/CxYzAbCdEfG/'],
    'zh',
  )
  assert.ok(text.startsWith(LINK_ROUTING_MARKER))
  assert.match(text, /不要调用 envoy 工具/)
  assert.ok(text.includes('https://www.instagram.com/p/CxYzAbCdEfG/'))
  assert.doesNotMatch(text, /mcp__envoy__envoy_read/)
  assert.doesNotMatch(text, /以下链接用 envoy_read 读/)
})

test('unknown links keep the try-envoy-first policy', () => {
  const text = buildRoutingText(['https://example.com/a'], 'zh')
  assert.match(text, /以下链接先试 envoy_read，失败再用宿主原生抓取：/)
  assert.ok(text.includes('mcp__envoy__envoy_read'))
  assert.ok(text.includes('以图片为准'))
  assert.ok(text.includes('mcp__envoy__envoy_expand'))
  assert.ok(text.includes('mcp__envoy__envoy_search'))
})

test('mixed prompt groups links once per verdict and keeps one marker', () => {
  const urls = [
    'https://www.bilibili.com/video/BV1xx411c7mD',
    'https://x.com/someone/status/1234567890',
    'https://example.com/a',
  ]
  const text = buildRoutingText(urls, 'zh')
  assert.equal(text.split(LINK_ROUTING_MARKER).length - 1, 1)
  const supportedAt = text.indexOf('以下链接用 envoy_read 读：')
  const unsupportedAt = text.indexOf('以下链接不要调用 envoy 工具')
  const unknownAt = text.indexOf('以下链接先试 envoy_read')
  assert.ok(supportedAt >= 0 && unsupportedAt > supportedAt)
  assert.ok(unknownAt > unsupportedAt)
  // Each URL appears once, inside its own group.
  for (const url of urls) {
    assert.equal(text.split(url).length - 1, 1)
  }
})

test('english prompt gets the same three groups', () => {
  const text = buildRoutingText(
    [
      'https://www.bilibili.com/video/BV1xx411c7mD',
      'https://www.instagram.com/p/CxYzAbCdEfG/',
      'https://example.com/a',
    ],
    'en',
  )
  assert.match(text, /Read these links with `mcp__envoy__envoy_read`:/)
  assert.match(text, /Do not call Envoy tools for these links/)
  assert.match(text, /Try `mcp__envoy__envoy_read` on these links first/)
  assert.ok(text.includes("not a replacement for the host's web search"))
})

test('an empty URL list injects the marker but no link groups', () => {
  const text = buildRoutingText([], 'zh')
  assert.ok(text.startsWith(LINK_ROUTING_MARKER))
  assert.doesNotMatch(text, /^- /m)
})

test('routableUrls still skips prompts that already mention Envoy tooling', () => {
  assert.deepEqual(routableUrls('请用 envoy_read 读 https://example.com/a'), [])
  assert.deepEqual(routableUrls('看看 https://www.bilibili.com/video/BV1x'), [
    'https://www.bilibili.com/video/BV1x',
  ])
})

test('an unsupported verdict beats a supported one for the same URL', () => {
  // 抖音直播属于 supported 家族（douyin_hosts），但 /live/ 必须按 unsupported
  // 处理：宁可走宿主原生抓取，也不误判成「Envoy 能读」。
  for (const url of [
    'https://www.douyin.com/live/123456',
    'https://live.douyin.com/123456',
  ]) {
    assert.equal(readSupportVerdict(url), 'unsupported', url)
    const text = buildRoutingText([url], 'zh')
    assert.doesNotMatch(text, /以下链接用 envoy_read 读：/, url)
    assert.match(text, /不要调用 envoy 工具/, url)
  }

  // 锁住匹配顺序：unsupported 的 host / path_rule 判断必须排在 supported 之前，
  // 否则将来新增重叠条目时会把不可读链接误标成 supported。
  const source = readFileSync(new URL('./index.js', import.meta.url), 'utf8')
  const body = source.slice(
    source.indexOf('export function readSupportVerdict'),
    source.indexOf('function linkBlock'),
  )
  const unsupportedAt = body.indexOf('unsupported.hosts.includes(host)')
  const pathRulesAt = body.indexOf('unsupported.path_rules')
  // lastIndexOf: 'unsupported.hosts.includes(host)' 里含同样子串
  const supportedAt = body.lastIndexOf('supported.hosts.includes(host)')
  assert.ok(unsupportedAt >= 0 && pathRulesAt > unsupportedAt)
  assert.ok(supportedAt > pathRulesAt)
})
