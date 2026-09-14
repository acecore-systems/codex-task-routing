# Codex Task Routing — 有効方針

これは利用者が導入した既定の分担運用です。上位指示・実行機能・ユーザーの明示指定と承認を優先し、親の設定を変更しません。個別の上書きは対応する既定を置き換えます。原則の要約と関連する詳細節の整合も確認してください。

上書きで範囲を変更した場合も、条件別の候補は有効な下限・上限の内側でだけ適用します。範囲外の固定候補が残る場合は親へ返し、必要な深さと許容範囲を両立できる担当を選び直します。

| 役割 | model ID | 下限 | 標準 | 上限 |
| --- | --- | --- | --- | --- |
| Luna | {{models.luna.id}} | {{models.luna.min_effort}} | {{models.luna.default_effort}} | {{models.luna.max_effort}} |
| Terra | {{models.terra.id}} | {{models.terra.min_effort}} | {{models.terra.default_effort}} | {{models.terra.max_effort}} |
| Sol | {{models.sol.id}} | {{models.sol.min_effort}} | {{models.sol.default_effort}} | {{models.sol.max_effort}} |
| Astra子 | {{models.astra.id}} | {{models.astra.min_effort}} | {{models.astra.default_effort}} | {{models.astra.max_effort}} |

{{principles.summary}}
