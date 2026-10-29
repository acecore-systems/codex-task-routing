# 0.22.0 検証記録

2026-10-29、Windows・Python 3.13の専用cloneで確認しました。変更後に348件のunittest（変更前312件）、`python scripts/check_package.py`、`git diff --check`が成功しました。

- 一次情報: openai/codex main（rust-v0.165.0時点）のhooks schema（PreToolUseの入力に`model`、子からの呼出しだけに`agent_id`/`agent_type`、出力の`permissionDecision: deny`）、core実装（spawn_agentのhook名は`spawn_agent`、matcher aliasは`Agent`、`tool_input`は引数JSONそのまま、`model`省略時の継承と既定model・役割設定による上書き）を読んで確認しました。深さと割当て役割は入力にないため、上位役割は判定しません。
- 新しいテスト36件で、既定・不正設定での無出力・無書込み、observeの値を含まない記録、enforceのdeny、root・省略・役割指定・型不正等の素通し、記録上限、status、hooks.jsonの実コマンドを確認しました。
- SessionStart/SubagentStartの注入文は変更なしです。同じ条件の測定でSessionStart 4,901字、SubagentStart 2,985字の前後同値で、8,000字の上限に影響しません。defaults・policy_revision・provenance・fixtureは変更していません。

未確認: 実機CodexでのPreToolUse入力例とdenyの反映、導入先Codexが`agent_id`を渡す版か、`/hooks`の再信頼、Python 3.11、他OS（CIで確認）、実際の運用での誤検知率です。観測と強制は利用者がファイルで有効にしたときだけ動き、稼働環境へは導入していません。
