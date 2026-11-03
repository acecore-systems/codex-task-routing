# 0.26.0 検証記録

- WindowsのClaude Code 2.1.308で `--help`、`--max-turns 1 --help` を確認しました。後者はオプション構文の受理に限り、上限の実動作を示しません。
- Windows / Python 3.13で `python -m unittest discover -s tests -v` は403件成功、`python scripts/check_package.py` は成功しました。初回の制限付き環境では既存テストの一時ディレクトリACL等が失敗し、全件を通常権限で再実行しました。模擬subprocessで要求値、環境、許可、結果、重複、失敗、入力不正を確認しています。
- 現ホストは標準子のみを許すため、Haiku CLIによるAPI送信はしていません。APIキーの登録・注入、認証、課金、モデル応答、実際のファイル操作、managed settingsの影響、権限ルールとturn/budget制限の実動作、他OS、品質・コスト比較は未検証です。稼働中のCodex環境へインストールしていません。

- 起動文は標準Codex子の規則と、上位ホストが許可する明示opt-inの別経路を区別します。doctorと合成fixtureはAPIへ依頼せず、環境変数名の存在とローカル条件のみを診断します。現在ロード済みのホスト指示の変更や稼働中環境への導入は行っていません。

- 今回のWindows環境で実行プロセスのキー環境変数は未設定、合成repoのidentity/cleanは一致、CLIのversion/helpと必要flagおよびmax-turnsのhelp parser受理を確認しました。キーの有効性・ホスト許可・API接続は未確認で、合成requestのopt-inと権限はfalseのままです。

- 追加の事前診断・fixture対応後、Windows / Python 3.13で全409件のテストと `python scripts/check_package.py` が成功しました。

- mainの0.25.0を取り込んだ統合版は、Windows / Python 3.13で全411件と `python scripts/check_package.py` が成功しました。ローカル現物と標準子の適合性を分ける方針と、任意のHaiku候補が同時に保持されることを確認しました。
