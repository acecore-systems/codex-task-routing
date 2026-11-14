# LFM3-VL

利用者が `$CODEX_HOME/codex-task-routing/local-vl.json` に `{"schema_version":1,"enabled":true}` を保存すると、起動フックで親に選択条件を案内します。MCP の登録と実行権限はホストで別途設定します。フックは資料・画像・プロンプトを読み取らず、推論を起動しません。

公開または合成の短いテキストや、利用者が推論専用 inbox に置いた PNG の分析に使います。有効方針、作業の担当、素材の利用許可を確認し、専用 MCP の `local_auth_local_vl` に `{"operation":"status"}` を渡します。`ready` の場合は `operation` を `text` または `image` に指定し、`prompt`、`dataClass:"public_or_synthetic"`、`explicitAuthorization:true` を渡します。画像には拡張子を除く `imageId` も指定します。親が元資料と照らして回答を受け入れます。

すでに適任の担当が進めている作業はその担当が続けます。公開 Web 調査は適用条件を満たす Muse Ember、型付きの選択・条件判定・採点は適用条件を満たす Drex Decisions を先に選びます。ローカル処理の指定、保存済み PNG の定型分析、短いローカル分析に該当する場合は、上の条件を満たせば LFM3-VL を選びます。

端末の画面、認証素材、任意のパスや URL は入力対象外です。利用不可のときは既存の分担方針を適用します。他経路への転送に必要な権限は各経路の条件に従います。親のモデルと effort は現在の設定に従います。
