/**
 * Envoy for DeepSeek Harness.
 *
 * Registers the DeepSeek Harness adaptation of Envoy's Agent Skill on the
 * public `ctx.skills` service. The companion `cordis.patch.yml` mounts
 * `envoy mcp` through `@deepseek-ai/dsh-mcp-client`; this module deliberately
 * imports nothing from the Harness packages so it keeps working across
 * profiles and Harness releases.
 *
 * The skill body and candidate metadata are generated from
 * `integrations/skills/envoy/SKILL.md` by
 * `scripts/build_envoy_dsh_plugin.py`. Edit the canonical skill, then
 * regenerate; do not hand-edit the files under `skill/`.
 */
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

/**
 * Register the Envoy skill provider synchronously, as `ctx.skills` requires.
 * @param {import('cordis').Context} ctx
 */
export function apply(ctx) {
  ctx.skills.registerProvider(() => provider)
}
