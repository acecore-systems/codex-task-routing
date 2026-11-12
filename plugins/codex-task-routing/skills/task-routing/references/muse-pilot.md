# Muse Ember の単回 text-only pilot

この経路は root による明示 opt-in と実ホスト・固定対象・公開または合成資料の送信承認がある場合だけ使う。固定モデルとローカル鍵を持つ別の Local Auth MCP が必要で、Chat、他モデル、通常のサブエージェントや既定 route の代替に自動変更しない。秘密・未承認資料を送らない。入力分類と承認の真偽は親が確認する。

`python <plugin-root>/scripts/muse_pilot_plan.py prepare --facts <JSON>` は pure planner で、MCP を呼ばない。facts は `schema_version:1`、`actor:"root"`、`explicit_opt_in`、`host_authorized`、`target_authorized`、`materials_authorized`（各 boolean）、`data_class:"public_or_synthetic"`、`prompt`、その UTF-8 SHA-256 小文字 `prompt_sha256`、専用 MCP の実 `status` 応答を含む。prompt は UTF-8 4096 bytes 以下。全条件と完全な `not_started` status が揃う場合だけ、`local_auth_muse_ember_pilot` 用の手動 `arguments` を返す。planner の結果はホスト権限や送信承認を代替しない。確認できない・試行済み・結果不明なら送信を提案せず、別 ID や設定で再送を回避しない。

呼出は承認済み root が一度だけ行い、戻り値と元の `prompt_sha256` を `verify --facts <JSON>` に渡す。JSON は `schema_version:1`、`prompt_sha256`、Local Auth の `result` を含む。planner は `completed`、同じ hash、実応答モデル `muse-ember-1.0`、制限内の本文と metadata を検証し、本文の意味・正確さは受け入れない。親が成果を読んで確認し、必要な編集と独立テストを行う。status は本文を保存しないため、応答未回収や中断は既存の journal を確認し、再送しない。
