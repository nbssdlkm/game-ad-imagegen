# Fixtures — A skill (game-ad-imagegen)

最小 adversarial sample JSON,**用于安装后自检 + 第三方审计**。每份文件聚焦一个边界条件。

跑法:`python ../scripts/batch_runner.py <fixture>.json --dry-run` 或 真跑(去掉 `--dry-run`)。

## 期望行为对照表

| 文件 | 期望 |
|---|---|
| `good_minimal.json` | ✅ runner 通过 + dry-run 显示正确 CLI 拼装(注意 `./fixtures/sample_ref.png` 是占位,真跑前替换成有效图片路径) |
| `bad_empty_refs.json` | ❌ exit 2 + 报 "A skill 不支持纯文字生图(0 图)。请加至少 1 张图,或切到 B skill" |
| `bad_empty_prompt.json` | ❌ exit 2 + 报 "prompt 不能空" |
| `bad_wrong_skill.json` | ❌ exit 2 + 报 "config.skill = 'b',本 runner 只跑 skill='a'" + 指向 B 的 runner |
| `bad_missing_path.json` | ❌ exit 2 + 报 "参考图不存在 — ./not_exists/nowhere.png" |

## 注意

- 所有 fixture 的 `out_dir` 都用 `~/Desktop/...` 占位,跨 OS 安全
- `good_minimal.json` 的 `reference_images` 是相对路径占位符 `./fixtures/sample_ref.png` — **要真跑必须替换成有效图片绝对路径**,否则会触发 "参考图不存在" 校验
- 这些是用来验证 runner / form 自身行为的最小集,不是测出图质量的基准
