# 0.25.0 検証記録

- WindowsのClaude Code 2.1.308で `--help`、`--max-turns 1 --help` を確認しました。後者はオプション構文の受理に限り、上限の実動作を示しません。
- Windows / Python 3.13で `python -m unittest discover -s tests -v` は403件成功、`python scripts/check_package.py` は成功しました。初回の制限付き環境では既存テストの一時ディレクトリACL等が失敗し、全件を通常権限で再実行しました。模擬subprocessで要求値、環境、許可、結果、重複、失敗、入力不正を確認しています。
- 現ホストは標準子のみを許すため、Haiku CLIによるAPI送信はしていません。APIキーの登録・注入、認証、課金、モデル応答、実際のファイル操作、managed settingsの影響、権限ルールとturn/budget制限の実動作、他OS、品質・コスト比較は未検証です。稼働中のCodex環境へインストールしていません。
