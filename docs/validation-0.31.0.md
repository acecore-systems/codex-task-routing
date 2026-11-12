# 0.31.0 の検証範囲

## 実装と検査

- `scripts/large_read_guard.py` を新設しました。標準ライブラリだけを使い、状態・DB・ログを持ちません。親の `Bash` / `exec` / `exec_command` で、100KB以上のファイルを `cat` `type` `Get-Content` などで丸読みする呼出しを検出し、ファイル名とサイズだけを示す `additionalContext` を返します。止めず、該当しない・判定できない・壊れた入力は何も出力せず終了します。
- `hooks/hooks.json` の PreToolUse に項目を1つ足しました。既存の2項目は変えていません。
- 既存の契約検査の期待値を更新しました。`scripts/check_package.py` と `tests/test_spawn_guard.py` の PreToolUse 件数を2から3にし、`check_package.py` では新しい項目の matcher と command、新スクリプトの存在を検査します。検査の内容は外していません。
- `tests/test_large_read_guard.py` を新設しました（10件）。検出・非検出の代表例、command / cmd / 配列 / workdir の入力形、親だけを見る条件、ツール名の正規化、壊れた入力・深い入れ子・入力上限、注意文の内容、配布コマンドの子プロセス実行、hooks.json の登録を検査します。

## 検証結果

- 作業ツリー（ブランチ `claude/practical-galileo-5ies19`、基点 `11f3b1f`、変更は未コミット）、Python 3.13.18、Node.js 22.23.4 で実行しました。
- `python -m unittest discover -s tests -v` は終了コード0で、513件、OK、skip 5件でした。変更前は503件、OK、skip 5件で、増分は新規10件です。
- `python scripts/check_package.py` は終了コード0で、Package checks passed でした。
- `node --test tests/test_chat_browser.cjs tests/test_chat_browser_run.cjs` は終了コード0で、70件成功、失敗0、skip 0でした。
- `RELEASE_BASE=11f3b1f591966602f798ebcd55c8648e56f8ba81 python scripts/check_release.py` は終了コード0で、Release version check passed でした。ただし変更が未コミットのため HEAD と基点は同じで、この検査は今回の差分を対象にしていません。補足として、作業ツリーの版 0.30.20 から 0.31.0 への変更は `needs_version_change` で要件を満たし、同じ版のままなら要件違反になることを確認しました。
- 移植元の `block-large-read.js`（v1.7.0）を Node.js で同じ入力に実行し、Bash 形式の62コマンドで判定を比べました。対象になったのは35件で、判定の不一致は0件でした。比較用の一時スクリプトはリポジトリに含めていません。
- テストの子プロセスは `CODEX_HOME` を一時フォルダに向け、フックがそこへ何も書かないことを確認しています。

## 実ホストでの確認範囲

- 稼働中の利用環境への導入、`/hooks` での再信頼は行っていません。自動インストールもしていません。
- 実際の Codex でのイベント配送、`Bash` / `exec` / `exec_command` の実入力は未確認です。特に `exec_command` の `cmd` や `workdir` の実際の形は、テストの想定入力です。
- 注意文の案内による親の行動変化は未確認です。非ブロックの助言であり、効果は実際の利用で確認する必要があります。
- Windows と macOS の実行は行っていません。Windows の MSYS 形式の変換分岐も実機では未確認です。CI の Windows / macOS 構成の結果は PR の Checks で確認します。
- Read ツール向けの判定は移植していません。
