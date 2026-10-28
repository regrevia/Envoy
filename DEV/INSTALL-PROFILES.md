# 安装档位：三档装什么，为什么默认完整档

- 基线：`main`（本文随 0.1.26 一起发）
- 起因：用户要求「base 默认塞进去」+「可以提供多几个等级，用户可选简洁安装和完整安装，建议完整安装」
- 本文数字都是本机实测（macOS arm64，`du -sm`），标了「未实测」的不进结论

## 一、三档定义

| 档位 | 装什么 | 实测体积 | 首次使用时会不会下载 |
| --- | --- | --- | --- |
| `minimal` 简洁 | runtime | 178 MB | 读网页/PDF/图片不会；视频页、转写会 |
| `standard` 标准 | runtime + Chromium | 178 + 683 MB | 视频页不会；转写会 |
| `full` 完整（默认） | 上一档 + Whisper `base` | 上一档 + Whisper 依赖 + 权重 | 不会 |

默认 `full`：现有契约是「装完就是完整的」，一次装齐比事后补救便宜；`minimal`/`standard`
留给 CI、容器、磁盘吃紧的用户。

## 二、数字来源

```sh
du -sm ~/.local/share/uv/tools/envoy-cli                    # 178   runtime（含 httpx/pypdf/playwright 客户端库）
du -sm ~/.local/share/uv/tools/envoy-cli/lib/python3.11/site-packages/playwright   # 146
# Chromium 下载：mac 573 MB / Windows 683 MB，见 packages/envoy-cli/src/envoy_cli/browser.py:377
envoy asr status --json                                     # asr_models_cached / asr_model_available
```

- Whisper `base` 权重（`base.pt`）约 139 MB，是本仓库之外的上游体积；`openai-whisper`
  的依赖里 torch 占大头，**各平台差异大、本仓库没有实测数字**，所以文档只写「约 140 MB 权重 + 依赖」，
  不给总安装体积的假精度。
- 本机 `envoy doctor --json` 实测：`browser ready, asr missing: run envoy asr install --model base`
   —— 说明「不装 ASR 时自检会点名给出修复命令」这条是真的。

## 三、实现方式（两个安装器一致）

```sh
install.sh [--profile minimal|standard|full] <host>     # ENVOY_PROFILE 同义，默认 full
install.ps1 [-Profile minimal|standard|full] <host>
```

- 档位只决定 `browser setup` 与 `asr install --model base` 两步跑不跑；宿主接线（插件/MCP 条目）不变，
  所以任何档位装完宿主都能用，只是缺的能力在首次使用前会明确报出修复命令。
- `ENVOY_SETUP_BROWSER=0` / `ENVOY_SETUP_MEDIA=0` 覆盖所选档位里的单步（CI 用），`ENVOY_ASR_MODEL`
  换 `full` 预取的模型。
- provisioning 挂在 `Ensure-Profile` 上、在 runtime 确定之后执行：之前把它放在「装 runtime」分支里，
  当 PATH 上已有 `envoy` 时会整段跳过（真机复现过：`mcp` 目标 + 已装 envoy ⇒ 档位不生效），已修。
- 两个安装器都在装完打印 `runtime …, browser …, asr …`，`asr` 缺失时直接给出要跑的命令。

## 四、验证

```sh
# 三档的 dry-run 语义
for p in minimal standard full; do ENVOY_DRY_RUN=1 sh dist/install.sh --profile $p mcp; done
ENVOY_DRY_RUN=1 sh dist/install.sh --profile ultra mcp   # 退出 2：unknown profile 'ultra'

# 安装器行为与一致性
sh -n dist/install.sh && sh scripts/check_installer_offline.sh   # 10/10
uv run --offline --python 3.11 --with pytest pytest -q tests/test_install_script.py \
  tests/cli/test_installer_ps1_static.py
```

测试覆盖：三档各自跑不跑浏览器/转写、`--profile=X` 与 `ENVOY_PROFILE` 等价、环境变量覆盖档位、
未知档位报错、`Ensure-Profile` 挂在每个宿主分支与 `auto`、从 `Install-Runtime` 里移出。

## 五、未做 / 待定

- 没在 Windows 真机上跑 `-Profile`（本机无 PowerShell）；静态测试只保证文本层一致，真机验收走
  `docs/windows-acceptance.md` 的流程，用 `-Profile minimal` 等价于原来的 `ENVOY_SETUP_BROWSER=0`。
- 本机 `tests/test_install_script.py` 有 2 条用例失败，原因是这台机器的 `/usr/bin/python3` 直接
  退出 69（Xcode license 未同意），`git checkout` 回 HEAD 的安装器后同样失败 —— 环境缺口，不是本次改动引入；
  其余 14 条通过，CI/Windows 侧不受影响。
- 没说「默认下载多少 GB 以下」这类承诺：Whisper 依赖体积未实测，文档只给可信的三段数字。

## 六、发行结果

- 0.1.26 已发：tag `v0.1.26` → 提交 `2b899fd`，发布流水线 `40705522220` success，
  资产 7 个（`envoy_cli-0.1.26-py3-none-any.whl` 247691 B、`envoy-dsh-0.1.26.tgz` 106719 B、
  `envoy-skill.zip` 31128 B、`install.sh` 24549 B、`install.ps1` 29687 B、`release-manifest.json`、
  `SHA256SUMS`）。交付目录 `/Users/apple/Desktop/2026/envoy-delivery/0.1.26/`，
  `verify_delivery.py --dir .` = 13 checks ok，`shasum -a 256 -c SHA256SUMS` 6/6 OK。
- npm 已补齐：`envoy-dsh@0.1.26` 已发布（`dist-tags.latest = 0.1.26`，packument 里版本由 6 个
  变 7 个）。发出来的 tarball `sha256 e161fcd6…` 与交付目录里那份**逐字节相同**；解包后
  `package/launch.cjs` 与仓库一致、`cordis.patch.yml` 里的 payload 解码后也与它一致，
  `repository.url` 指回本仓库，市场探针能认。发版用的是本机 Keychain 里的 automation token
  （`dsh-npm`），仓库 secret 仍然空着 —— 所以流水线不会自动发 npm，要么补 `NPM_TOKEN`，要么
  以后都走本机 `dsh-npm publish`。0.1.24/0.1.25 没有补发，`latest` 直接跳 0.1.23 → 0.1.26。
- 插件市场：条目已在（`data/plugins/regrevia__Envoy--integrations-dsh-envoy-dsh.yml`，无版本号
  字段），线上 `plugins.json` 目前还是 `version 0.1.23 / downloads 473`（构建日期 2026-10-27）。
  按站点机制，npm 版本只在带 probe 的 nightly 构建回填，白天的 push 构建不探测，所以卡片徽章
  要到次日 10:23 之后才变 0.1.26，不需要手动 dispatch。
- 全量校验（0.1.26 源码）：`pytest -q tests/cli` 876 passed（带 `reportlab`）；
  `pytest -q tests/test_install_script.py tests/cli/test_installer_ps1_static.py`
  14 通过 + 2 条环境缺口（见上）；`ruff check .` 全部通过；`check_versions.py --check`
  11 项一致；`check_release_manifest.py --check` 一致。

## 七、Windows 真机验收（2026-10-28）

三档的装法都在真 Windows 上验过（`full` 的浏览器那一半由 `standard` 覆盖，转写那一半由默认那轮覆盖），
证据是 Actions 日志，不是推断：

| 跑的档 | 运行 | 结果 |
| --- | --- | --- |
| 默认那轮（Chromium 关、转写开） | `40705581883` | PS 5.1 真机 / PS 7 / 托管新机 三个 job 全 success，共 68 条 `OK`、0 条 `FAIL`；托管新机实测下载 Whisper `base` 145,262,807 B、sha256 `ed3a0b6b…`、`asr ready`，同机第二次装 `downloaded_bytes=0`（缓存在） |
| `minimal` | `40707304663` | 两台真机 A8 全 OK（`browser.ready=False`、`asr_installed=False`）；安装器原话 `skipped browser setup (profile minimal)` / `skipped speech recognition (profile minimal)` |
| `standard` | `40707667395` | 三个 job A8 全 OK，`browser.ready=True`；托管新机实际下了 `chromium-1248` 与 `chromium_headless_shell-1248`，真机上这段约 5 分钟 |

- 做法：`scripts/windows/acceptance.ps1` 加 `-Profile`（有档位时不强塞
  `ENVOY_SETUP_BROWSER=0`，非 `full` 档显式 `ENVOY_SETUP_MEDIA=0`），
  `.github/workflows/windows-acceptance.yml` 加 `profile` 输入并透传给三个 job，
  装完跑 `envoy doctor --json` 做 A8 断言。落地提交 `cd449e3`、`1fe4113`。
- 顺手抓到的两个真问题：`scripts/embed_dsh_launcher.py --check` 原来比 base64 字符串，同一个 `launch.cjs` 在 macOS 和 Linux 上压出来的字节不同，于是 main 的 CI 在 Ubuntu 上一直红（`06afb0e` 改成解码后比内容）；`dev` 分支的 dsh 测试与 manifest 检查器停在旧格式，同步后转绿（`d944672`、`34e546f`）。
- 没做：Windows 上把 Chromium 与 Whisper 一起装的 `full` 一整轮（默认那轮 Chromium
  是关的，`standard` 那轮转写是关的）；托管机有一次转写 `did not finish`，安装器照常
  退出 0 并打印 `run envoy asr install --model base later` —— 这是容错行为，不是失败，
  但也说明首次装并不总能一次装完，修复命令得保留。
