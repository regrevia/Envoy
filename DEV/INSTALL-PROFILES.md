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
