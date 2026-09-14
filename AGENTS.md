# Codex Task Routing の開発

- 作業は `codex/` ブランチで行い、無関係の変更を混ぜない。plan、commit、PRは日本語。
- 作者の分担原則・effort条件は `plugins/codex-task-routing/defaults/` を正本とする。既定の意味変更は配布形式の修正と分けて説明する。
- 標準サブエージェントを利用し、プラグインから独自の子・CLIセッション・外部モデルを起動しない。親設定や承認境界を変更しない。
- 認証、会話履歴、個人メモリを読み込まず、利用者の `AGENTS.md` と `config.toml` を自動編集しない。
- フックや設定変更は一時的なCodexホームで検証する。稼働中の利用環境へ自動インストールしない。
- `python -m unittest discover -s tests -v` と `python scripts/check_package.py` を実行する。現在の版で確認済みの範囲と未検証の実行環境を明記する。
- 公開前に個人パス、秘密値、実行ログ、未完成の生成用プレースホルダーが含まれないことを確認する。
- 親に独立した有用作業がある時だけ、明確な作業を指定model/effortの子へ渡す。通常実装はTerra/xhigh、限定された重要レビューはSol/highを候補にし、同じ子を継続する。
