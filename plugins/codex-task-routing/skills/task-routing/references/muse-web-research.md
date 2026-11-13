# Muse Ember 公開 Web 調査

この経路は既定では無効。任意の利用者設定 `codex-task-routing/muse-web.json` に `{"schema_version":1,"enabled":true}` を設定した場合だけ、開始フックが公開 Web 調査の候補を案内する。フックは設定を読むだけで MCP や外部 API を呼ばない。Luna 既定・override は変更しない。Local Auth 側にも別途 `publicWebResearchEnabled:true` が必要。実設定・インストールは本人の環境と承認に従う。

親は公開 Web 調査かを判断し、ホスト・固定 Meta 対象への送信、入力資料、費用を含む承認の有効範囲を確認する。同一範囲の既存承認は再利用し、別経路の単回 pilot 承認を自動流用しない。設定済みで、適任の継続担当がいない承認済みの新規公開 Web 調査では、標準 Luna 起動前に status と prepare を確認し ready なら Ember を選ぶ。未送信で承認不足・対象外・無効設定・利用不能なら有効な従来方針へ戻る。送信結果が不明なら他経路にも同じ仕事を再送しない。秘密や非公開情報を含めない。

専用 MCP `local_auth_muse_web_research` に `operation:status` と入力 UTF-8 SHA-256 `promptSha256` を渡し、結果を planner の `status` に渡す。`muse_web_research.py prepare --facts <JSON>` の facts は schema_version:1、actor:root、explicit_opt_in、host_authorized、target_authorized、materials_authorized、cost_authorized、data_class:public、purpose:public_web_research、prompt、prompt_sha256、status を含む。真偽は親が確認し、planner は外部に送信しない。成功時の arguments を親が実 MCP に一度だけ渡す。各入力は承認範囲内でのみ実施し、intent、失敗、不明、完了の同一入力は再送しない。run のタイムアウト時は同じ hash の status を先に確認する。

受け取った result と元 hash を `muse_web_research.py verify --facts <JSON>` に渡す。完了した検索呼出しと本文に紐づく URL citation を検証し、内容の正確性や出典の信頼性は親が確認する。max を要求し、provider が別の effort を明示した結果は拒否する。effort が省略された場合は観測不明として報告する。status は本文を返さず結果不明の回収を保証しない。Local Auth は固定 Responses endpoint と専用鍵・固定 web_search プロファイルの通信、journal だけを担当する。検索指示、本文、出典と最終受入は Routing が扱う。web_search は最大3回の呼出しであり、検索クエリ数や費用の上限ではない。
