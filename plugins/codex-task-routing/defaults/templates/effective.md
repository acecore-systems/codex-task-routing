# Codex Task Routing — 有効方針

上位指示・実行機能・利用者の明示指定と承認を優先し、親設定は変更しません。default_effortが自動選定の固定値です。min_effort/max_effortは既存overrideの範囲検証用で、毎回の選択候補ではありません。

| 担当 | model ID | 有効な固定値 |
| --- | --- | --- |
| Luna子 | {{models.luna.id}} | {{models.luna.default_effort}} |
| Sol子 | {{models.sol.id}} | {{models.sol.default_effort}} |
| Astra子 | {{models.astra.id}} | {{models.astra.default_effort}} |
| Terra（親の基準・互換定義、子は標準外） | {{models.terra.id}} | {{models.terra.default_effort}} |
| 通常Chat | UIで6 Pro | 別経路・Codex effortと換算しない |

{{principles.summary}}
