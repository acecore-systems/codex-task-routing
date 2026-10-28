# Codex Task Routing — 有効方針

上位指示・実行機能・明示指定と承認を優先し、親設定は変えません。自動選定はdefault_effort、min/maxはoverride検証用です。

| 担当 | model ID | 有効な固定値 |
| --- | --- | --- |
| Luna子 | {{models.luna.id}} | {{models.luna.default_effort}} |
| Sol子 | {{models.sol.id}} | {{models.sol.default_effort}} |
| Terra子（terra61） | {{models.terra61.id}} | {{models.terra61.default_effort}} |
| Astra子 | {{models.astra.id}} | {{models.astra.default_effort}} |
| 通常Chat | UIでPro | 別経路・Codex effortと換算しない |
| ローカル認証 | local-auth-lfmの共有モデル | 秘密値を扱う登録済み工程・状態はMCPで確認 |

通常実装・修正・調査等はTerraを既定とし、合意済み設計の具体化・能力不足時は理由付きでSolを選ぶ。Sol→Terraはホスト・深さ条件付き、Terra→Solは不可。方式選択・合意前提の変更は親が判断。通常の実装判断はTerraが行う。旧terraは互換専用で選定しない。Chatはroot/直下Solのみ。性能順位は未実証。原則overrideは自動移行せず、矛盾はrootへ返す。

{{principles.summary}}
