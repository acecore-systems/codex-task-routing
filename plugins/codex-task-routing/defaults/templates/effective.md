# Codex Task Routing — 有効方針

上位指示・実行機能・利用者の明示指定と承認を優先し、親設定は変更しません。default_effortが自動選定の固定値です。min_effort/max_effortは既存overrideの範囲検証用で、毎回の選択候補ではありません。

| 担当 | model ID | 有効な固定値 |
| --- | --- | --- |
| Luna子 | {{models.luna.id}} | {{models.luna.default_effort}} |
| Sol子 | {{models.sol.id}} | {{models.sol.default_effort}} |
| Terra子（terra61） | {{models.terra61.id}} | {{models.terra61.default_effort}} |
| Astra子 | {{models.astra.id}} | {{models.astra.default_effort}} |
| 通常Chat | UIでPro | 別経路・Codex effortと換算しない |
| ローカル認証 | local-auth-lfmの共有モデル | 秘密値を扱う登録済み工程・状態はMCPで確認 |

TerraはSolと同列の実装・修正・テスト候補。性能順位は未実証。旧terraは互換専用で選定しない。通常Chatの所有者はrootまたはroot直下Sol子だけ。原則overrideは自動移行せず、候補・役割との矛盾が残ればrootへ返す。

{{principles.summary}}
