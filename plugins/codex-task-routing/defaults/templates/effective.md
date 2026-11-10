# Codex Task Routing — 有効方針

上位指示・実行機能・明示指定と承認を優先し、親設定は変えません。自動選定はdefault_effort、min/maxはoverride検証用です。

| 担当 | model ID | 有効な固定値 |
| --- | --- | --- |
| Luna子 | {{models.luna.id}} | {{models.luna.default_effort}} |
| Sol子 | {{models.sol.id}} | {{models.sol.default_effort}} |
| Terra子（terra61） | {{models.terra61.id}} | {{models.terra61.default_effort}} |
| Astra子 | {{models.astra.id}} | {{models.astra.default_effort}} |
| 通常Chat | UIでPro | 別経路・Codex effortと換算しない |
| Haiku API | claude-haiku-5-5 / xhigh | 任意の別経路・root所有・host許可と資料送信の承認が必要 |
| ローカル認証 | local-auth-lfmの共有モデル | 秘密値を扱う登録済み工程・状態はMCPで確認 |

以下は同梱既定の役割であり、原則overrideがある場合は有効原則に従って矛盾を確認する。調査はLuna/{{models.luna.default_effort}}、通常の実装・修正はTerra/{{models.terra61.default_effort}}、難題の実行はSol/{{models.sol.default_effort}}、相談は条件を満たす通常Chat Proを優先し、必要な時だけAstra/{{models.astra.default_effort}}を検討する。難しい判断・実装を含む調査は分離可能な証拠収集をLunaに任せ、重い判断はrootへ戻して役割を再判定する。root Sol→Sol子は難題の具体的な完了単位の分離に追加価値がある場合に限り、同モデルへの無用な移管はしない。親の実model・effort、有効override、既存担当とホスト・階層の制約は維持する。Sol→Terraはホスト・深さ条件付き、Terra→Solは不可。方式選択・合意前提の変更は親が判断。通常の実装判断はTerraが行う。旧terraは互換専用で選定しない。Chatはroot/直下Solのみ。性能順位は未実証。原則overrideは自動移行せず、矛盾はrootへ返す。

{{principles.summary}}
