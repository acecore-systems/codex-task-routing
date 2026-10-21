# 0.15.2 の検証範囲

2026-10-21、専用ブランチ`codex/remove-legacy-cloud`で旧Cloud CLI互換実装を除去し、現行UI専用へ整理した。
基点は`d3984bcbdbe70fcdd4c201653a91dbf934c6b39f`。0.15.1の非推奨化から、互換実装自体を保持しない方針へ変更する。

## 変更範囲

- 旧CLIの実行コードと専用テスト、専用transport資料を削除。plannerは現行`codex_cloud_ui`と未確認を示す`unknown`だけを受理し、旧transportは入力エラーにする。
- 共通の`cloud_record.ClaimStore`はUI所有者が使う最小のローカル保存helperとして保持。CLI固有wire hash、attempt/diff保存、remote status、commandイベントを除去し、schema 2で依頼・材料hash・scope・元タブ/taskを固定する。
- 永久O_EXCL claim、固定inodeのOS lock、binding一致、既知task IDの不変、状態の巻戻し拒否を維持。storeのrequest IDとbindingのrequest IDが異なる場合も拒否する。
- 旧形式・未知・破損claim/snapshotやclaimのないsnapshotは削除・変換・未送信扱いにせず、照合まで新規送信を止める。保存失敗・process crashでも再送を許可しない。
- 方針本文、引継ぎ、README、スキル、生成fixtureとprovenance、package検査を同期。過去changelog・検証記録と既存の保存済み記録は保持し、利用者の設定は編集しない。

## ローカル検証

Windows / Python 3.13.15で実施した。既知のWindows sandboxの一時保存先・hardlink ACL制限を避け、実ファイルを使うテストは通常権限で実行した。

| 検証 | 結果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 247件すべて成功、skipなし、終了コード0（10.879秒） |
| UI claim専用テスト | 15件成功。実ファイル、並行所有、OS lock、実子process crash、原子的snapshot保存失敗、scope/tab/task固定、旧/未知/破損記録の保持を検証 |
| 旧transport拒否 | inspect / dispatch-plan / 元タスクreceiveで入力エラー、元入力は不変 |
| `python scripts/check_package.py` | 成功。配布物の構成、旧実行コード/専用資料の非存在、個人パス・秘密値・未完成素材を検査 |
| 一時Codexホームのstatus/render | 成功、manifest 0.15.2 / policy revision `2026-10-21-cloud-ui-only` |
| 通常Chat有効時の生成hook文脈 | SessionStart 7369字 / SubagentStart 5648字、既存8000字上限内 |
| 基点との差分照合 | models全体とpolicy_sessions以外のprinciplesは不変。親・effort・Chat・認証・承認方針を保持 |
| `git diff --check` | 成功 |

件数は前版252件から旧CLI専用21件を除き、共通claim15件と旧transport拒否1件を加えた247件。
旧CLI専用テストを実行しないことを未検証の残対応にはせず、必要な保存安全性はUI用の独立テストで検証した。
helperの保存成功やplannerの照合を、実UI送信・認証・テスト環境・内容受入の証明とは扱わない。

## 実Cloudと後処理の境界

今回の0.15.2では新しいCloud送信や実Browser操作は行っていない。
[0.15.0の実機記録](validation-0.15.0.md)のUI起動・回収と、以前のソースに対する250成功・Windows専用skip 2件は過去実績として保持し、今回の新schemaの実UI往復とは区別する。
HTTP listenerと自動start_skill注入はUI利用の前提や通常運用の残対応にしない。

一時Codexホームと試験用保存先はfixtureが後処理した。既存の保存済み記録は探索・削除・移行していない。
新しいBrowserタブ・Cloudタスク・agent worktreeは作成していない。PR対応のため専用ブランチを保持し、無関係のworktreeは変更しない。
マージと利用環境への導入・削除ファイル確認は親担当の別工程として引き継ぐ。
