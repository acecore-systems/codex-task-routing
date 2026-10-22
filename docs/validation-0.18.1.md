# 0.18.1 の検証範囲

2026-10-22、専用ブランチ`codex/updater-short-cwd`で、Windowsの自動更新が長い`CODEX_HOME`で黙って失敗する問題を直した。基点は`8907681`（origin/main、版0.18.0、PR #31）。依頼時の基点は0.17.0（`b9b41e4`）として指示されていたが、origin/mainが0.18.0に進んでいたため、0.18.0の上に0.18.1として付けた。更新処理の該当ファイルは両者で同一。

## 原因と変更

- 更新処理は、marketplaceを確認・更新する子プロセス（`codex plugin list`、`marketplace upgrade`）の作業ディレクトリを状態ディレクトリ（`CODEX_HOME`＋33字）にしていた。Windowsは作業ディレクトリが259字以上だと`WinError 267`で子を起動できない。依頼時の調査では、`CODEX_HOME`が226字以上で更新が`skipped/cli_error`として黙って失敗していた。
- 変更: Windowsでは`SystemRoot`（無ければ`GetWindowsDirectoryW`）を作業ディレクトリにする。利用者が書き込めない場所のため、`python -c`ヘルパーの`sys.path[0]`や、拡張子なしの`codex`の検索にCODEX_HOME内のファイルが混ざらない。Windows以外は状態ディレクトリのまま。作業ディレクトリが無ければ子を起動せず失敗にする。
- 変えていないもの: 更新処理の入口`updater_entry.py`の`cwd=base`、定期タスクの`WorkingDirectory`（既存登録の所有判定に使う）、方針・defaults・`policy_revision`・provenance。
- README: Windowsでは`CODEX_HOME`を約230字以内にする注意を追加。

## ローカル検証

Windows 11 / Python 3.13.15、`LongPathsEnabled=1`の機で実施。

| 検証 | 結果 |
| --- | --- |
| 変更前の`origin/main`、短いパス | 286件成功（基準） |
| 変更前のコードに新規試験を当てる | 259字の子起動試験が起動失敗で失敗。不具合を再現 |
| `python -I -m unittest discover -s tests`、短いパス、変更後 | 289件成功、終了コード0 |
| 同、長いパス（作業ツリーを約207字のscratchpadへ展開） | `test_updater`は16件成功。全件は289件中2件失敗。`test_hook_command`の`test_shipped_command_supports_spaces_and_both_events`（SessionStart / SubagentStart）。同じ展開の`origin/main`でも同じ2件が失敗するため、既存の問題で本版の変更とは無関係。未修正 |
| `python -I scripts/check_package.py` | 成功 |
| `RELEASE_BASE=origin/main python -I scripts/check_release.py` | 成功（0.18.0から0.18.1へ） |
| `git diff --check` | 成功 |

## 版と記録

- 0.18.1は配布修正。方針の意味、defaults、`policy_revision`、provenanceは変えていない。
- 変更履歴では「配布修正」と明記し、方針の意味変更として扱っていない。

## 未検証

- Windows以外（POSIX）の試験は未実行。POSIXでは従来どおり状態ディレクトリを作業ディレクトリにするため、動作は変わらない想定。
- 実際のCodex CLI本体が作業ディレクトリに依存しないことは未確認。Codex CLIが利用できる環境がなく、`scripts/smoke_native.py`も実行していない。コード上は、子へ渡す設定が絶対パスの環境変数だけであることを確認した。
- 実機の定期タスク、ログオン中の実更新、Codex・ChatGPTの稼働中の挙動は未確認。
- 232字以上の`CODEX_HOME`では、定期タスクの起動や更新処理の入口の確認が失敗しうる。これは今回の変更で解消しておらず、README に注意を記載した。実機では未確認。
- `LongPathsEnabled`が無効な環境では、状態ディレクトリ配下のロック・診断ファイルが260字を超えて失敗しうる。この環境では未検証。
- 長いパスで残る`test_hook_command`の2件は既存の問題で、本版では扱っていない。
