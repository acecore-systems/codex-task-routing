# Codex Task Routing の開発

- 作業は `codex/` ブランチで行い、無関係の変更を混ぜない。plan、commit、PRは日本語。
- 作者の分担原則・effort条件は `plugins/codex-task-routing/defaults/` を正本とする。既定の意味変更は配布形式の修正と分けて説明する。
- 標準サブエージェントを利用し、分担フックから独自の子・CLIセッション・外部モデルを起動しない。任意の自動更新タスクは対象marketplaceの管理コマンドだけを実行し、モデルに仕事を送らない。通常の起動方法、親設定や承認境界を変更しない。
- 利用者が通常Chat経路を明示的に有効にした場合だけ、ルート親は通常Chatへの送受信を別経路として使える。フックからは起動しない。Work・モデルAPIへの切替は禁止し、UIモデル確認、依頼ID照合、内容の受入を親が行う。
- 認証、会話履歴、個人メモリを読み込まず、利用者の `AGENTS.md` と `config.toml` を自動編集しない。
- フックや設定変更は一時的なCodexホームで検証する。稼働中の利用環境へ自動インストールしない。
- `python -m unittest discover -s tests -v` と `python scripts/check_package.py` を実行する。現在の版で確認済みの範囲と未検証の実行環境を明記する。
- 公開前に個人パス、秘密値、実行ログ、未完成の生成用プレースホルダーが含まれないことを確認する。
- 基準運用は親GPT-6.1 Sol/xhigh、子GPT-6 Luna/max・GPT-6.1 Sol/max・GPT-6 Astra/high、適合工程は通常Chat Proを優先する。親の実設定は変更せず、同モデルへの無用な移管をしない。並列は親の独立作業、直列の専門委譲は具体的追加価値とホストの許可を確認し、上位指示を迂回しない。

- 通常Chatの選択表示はPro（旧6 Pro / GPT-6 Proも受理）。backendは推測しない。転送許可の根拠は`chat-authorization.md`に従い、ネイティブ接続操作と別Chatへの転送を分ける。元の依頼の承認を再利用し、ホストの実際の承認要求・上位制約は維持する。

- Browser経路の調査は`chat_plan.py --phase inspect`、新規送信は既定dispatch、既存依頼の回収は`--phase receive`で分ける。有効な既存承認を再利用し、transport_evidenceを重ねて要求しない。実Browser未検証をローカルテスト/GitHub成功で代替しない。
