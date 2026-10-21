# 0.15.1 の検証範囲

2026-10-21、専用ブランチ`codex/current-cloud-only`で、Cloudの標準を現行UIと明示handoffへそろえた。
変更前の基点は`8ff945cd847538e79c46a20147804e958fad5ab0`。

## 変更と確認対象

- `codex_cloud_ui`を標準とし、既存の同一範囲の承認・送信前の永続claim・元依頼ID/hash・所有者・元タブ/taskの保持を継続する。
- 開始時の絶対cwd、repo/branch/期待revision、README・AGENTS、cloud-environment runtimeスキルとnetwork policy、必須検証、許可操作・停止条件を依頼本文に渡す。自動`start_skill`注入には依存しない。
- HTTP listenerはUIの利用要件ではなく、必要な個別工程でだけ確認する。
- 旧`codex_cloud_cli`/`cloud_run.py`のコード・テスト・既存記録契約を保持し、非推奨互換資料へ隔離する。自動候補・通常運用の残対応にせず、旧CLIの互換性・実送信検証を現行UIの完了条件にしない。
- 方針の意味変更を配布形式の変更と分け、モデル/effort、親設定、通常Chat条件、明示override、planner入出力と実行コードは維持する。

## 前版の実Cloud結果との境界

[0.15.0の実機記録](validation-0.15.0.md)では、現行UIで対象環境の公開、実タスクの起動、同一タスクの回収を確認済み。
commit `8120ca9ac94327f02233842bfd21fceaf4d671a9`の追跡対象ソースを同じタスクへ渡した再検証は、
Linux / Python 3.12.14、全252件中250成功・失敗0・エラー0・Windows専用skip 2件、終了コード0だった。
package検査と一時Codexホームのstatus/renderも成功した。
元checkoutと復元artifactを分け、Git管理外の適切な一時保存先を同じ実行単位内で用いた。

これは前版ソースとその環境に対する過去の実績であり、0.15.1の実Cloud再実行や、
旧CLIの送信・回収、HTTP listenerの実能力を実証する結果ではない。
旧CLIの404から現行UIタスクの不存在や認証不在を推定せず、UIタスクを再送しない。

## 今回の検証と後処理

Windows / Python 3.13.15で次を確認した。

| 検証 | 結果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 通常権限で252件すべて成功、skipなし、終了コード0（10.680秒） |
| `python scripts/check_package.py` | manifest、参照、hook、defaults、公開素材検査が成功 |
| 一時Codexホームのstatus/render | 成功、0.15.1 / `2026-10-21-current-cloud-ui` |
| 通常Chat有効時の生成hook文脈 | SessionStart 7369字 / SubagentStart 5648字、既存8000字上限内 |
| 基点との差分照合 | models全体、summary/policy_sessions以外のprinciples、実行コードは不変 |
| `git diff --check` | 成功 |

最初のサンドボックス実行は252件中、failureイベント1件・errorイベント49件で終了コード1だった。
Windowsの一時ディレクトリとhardlink作成に対する`WinError 5`が原因で、
同じソースを通常権限で再実行した結果を上表へ記録した。製品コードや安全ガードは緩和していない。
一時Codexホームで生成文書・fixture・provenanceを同期し、試験用フォルダーはfixtureが後処理した。
稼働中の利用環境への導入はこの担当では行っていない。
新しいCloudタスク・Browserタブ・子エージェントは作成していない。

旧CLI互換性・実送信、自動start_skill注入、CloudでのHTTP待受は通常運用の残対応に含めない。
利用環境への0.15.1導入、親による差分受入・マージは別の到達点として扱う。
