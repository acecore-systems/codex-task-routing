# Redmine の作業記録

Redmine を継続作業の進捗・受入・再開の正本にする。実装、運用、複数工程の調査など、後から同じ目的で再開する作業が対象。通常の会話や短い単発作業は API を呼ばず、記録もしない。ユーザーとプロジェクトの有効な指示が優先される。

明確な参照が無い新規の継続作業では、設定済みのプロジェクトで既存の Issue・紐付けを権限内で確認する。一意に対応する既存 Issue を再利用する。未登録なら許可済みの Redmine UI/API で Issue を作成し、初回の作業記録を設定する。必要な GitHub Issue との紐付けは agent-toolkit の docs/redmine-task-memory.md に従い、許可された範囲で Redmine 側に記録する。新しい公開 Issue の作成や公開投稿には、この手順とは別に対象操作への許可が必要。対応先が曖昧・未許可、設定や接続が無い場合は通常の作業を続け、正本の記録が未設定または未確認である理由を伝える。

1. **開始・再開**。ユーザー、タスクの引き継ぎ、承認された作業設定で既に紐づいた参照を使う。Redmine の Issue ID・設定済みサーバーの Issue URL、または紐付け済みの GitHub Issue URL を、親が `node <agent-toolkit>/scripts/redmine-task-memory.ts resume <ref>` に渡す。目的・到達点・受入状態と根拠・次の作業・待ち理由・関連 PR の状態を確認する。既存の作業ツリーと検証結果は再確認する。humanContext.reviewNeeded が真なら、新しい説明・状態・注記を同じ Issue で確認し、以前の記録と照合してから作業や節目の記録を進める。ツール未設定・権限なしの場合は通常の作業と引き継ぎを続け、記録できなかった旨を伝える。初回の `uninitialized` は revision 0 で、既に受入済みの記録があるとみなさない。
2. **引き継ぎ**。現在の到達点・合意した受入条件・次の作業を短く親から渡す。参照と根拠は受け手に許可された範囲だけに絞る。実行担当は検証結果、未確認事項、次の作業を親に返す。子や外部委譲先に Redmine の更新を委ねない。
3. **節目・終了**。親が結果を受け入れてから、到達・待ち・完了など変化した節目を一度記録する。同じ Issue を対象に `node <agent-toolkit>/scripts/redmine-task-memory.ts update <ref>` を実行し、標準入力に短い JSON を渡す。直近の resume の `revision` を `expectedRevision` に使い、同じ節目には同じ `milestoneId` を使う。PR の draft・open・merged 等は参照情報として扱い、マージだけで受入を完了にしたり Issue を閉じたりしない。

```json
{
  "expectedRevision": 0,
  "milestoneId": "review-complete-1",
  "objective": "設定変更を検証して引き継ぐ",
  "progress": "差分とテストを確認した",
  "nextAction": "承認後に展開を検証する",
  "waitReason": "展開の承認待ち",
  "acceptance": {"state": "pending", "evidence": null},
  "pullRequests": []
}
```

`updated` / `already_applied` で記録を確認する。競合は同じ参照を読み直して差分を照合する。送信結果が不明なら同じ参照と節目を再確認し、無条件に再送しない。PR の `unverified` は確認待ちとして扱う。

実行には信頼済みの共有 agent-toolkit と Node.js 24 以降が必要。明示されたローカル設定の toolkit ルート（例: 環境変数 AGENT_TOOLKIT_ROOT）を使い、パスを推測しない。Redmine の接続先・認証（`REDMINE_URL`、`REDMINE_API_KEY`）・対象プロジェクト（`REDMINE_TASK_MEMORY_PROJECT`）は利用者のローカル設定を使う。PR の状態確認は利用可能な認証済み GitHub CLI を使う。フックはローカルの手順案内のみで、API、toolkit、認証、書き込みを起動しない。プラグインから接続設定やローカルの上書きを変更しない。認証値、内部 URL、内部の要約を公開 GitHub・公開成果物・未承認の外部経路へ転記しない。
