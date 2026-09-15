# 接続の再利用と機械的な適合確認

自然文の意味判断、機密の範囲、引継ぎが見合うかはrootが一度判断します。分類専用の子やChatを起動しません。短い仕事ではCLI入力を作る負担も避け、その場で判断します。まとまった工程では下の事実JSONと確認済み接続を `chat_plan.py` で照合できます。これは送信許可や実行器ではなく、モデルを使わない補助です。

## 接続一覧を毎回読まない

同じ工程は同じfactsファイルと一行の選定理由を再利用し、材料・範囲・必要操作・委譲方式・ホストの制約・準備や検収の負担が変わった時だけ意味判断をやり直します。期限の照合はCLIで再実行でき、分類のためにモデルへ材料を再送しません。新しいChatのUI・6 Pro・必要プラグインの提供状態は現在の画面で確認します。

初回は依頼に必要な接続だけ、利用者が一覧調査を依頼した時は既存接続のメタデータだけを確認します。サポートされたChatの設定・一時Chatのプラスメニューで確認し、Codex側のツール一覧からChatの能力を推測しません。接続の確認だけを目的にメール本文や顧客データを取得しません。

結果は利用者が指定したローカル領域、通常は `<CODEX_HOME>/codex-task-routing/capabilities.json` に保存します。配布キャッシュや公開repoには個人の接続一覧を入れません。秘密、アカウント識別子、会話URL、応答全文、認証情報は保存しません。フックは一覧を読み込まず、会話コンテキストにも注入しません。

- `installed_plugins` はインストール済みの名前です。接続・権限・Chatでの取得成功を保証しません。
- `capabilities` は実際に必要となった操作と対象範囲だけ記録します。`advertised` はそのChat側で操作の提供だけを確認、`permission_checked` は操作仕様と対象範囲の権限を確認したが未実行、`verified` は同じ操作と対象範囲で実行成功を確認、`blocked` は利用不能です。設定の「すべて許可」は新しい仕事の承認ではありません。
- `scope` は完全一致です。例のrepoでの成功を全repo、書込み、CI実行へ拡張しません。UIだけで見た操作はverifiedにしません。初回操作は下記のpermission_checkedを使い、事前の試験書込みを要求しません。Chatの自己申告だけで更新せず、ツール実行の表示、出典、取得範囲を確認します。
- 各記録に確認日時と有効期限を付けます。最大7日で、短い期限を選べます。未来日時・期限切れはstaleとして必要分だけ再確認します。接続エラー、再ログイン、権限やアカウント変更に気づいたら期限内でも直ちにblockedまたはadvertisedへ戻します。
- 検索は `needs` に合う操作だけ。未登録・期限切れは不存在と断定せず、必要な接続だけ確認します。本文が十分ならツールなしの依頼にできます。

GitHubでは承認されたrepoの原文・差分・Issue・CIの読取りを使って調査やレビューをまとめて渡せます。Gmailは指定範囲の検索・要約、Canva/Figmaは対象デザインの読取り・提案、Stripeは承認済み範囲の調査、公式Docs MCPは仕様確認の候補です。いずれも実際のChat側の操作と対象権限を確認します。プラグインのスキルだけの導入からローカル編集・ビルド・本番実行がChatで可能とは扱いません。書込みが必要な時はその操作・対象・承認を別途確定し、読取実績を流用しません。

## 入力と結果

接続台帳の最小例（架空）:

```json
{
  "schema_version": 1,
  "surface": "chat-temporary",
  "model": "6 Pro",
  "installed_plugins": ["GitHub"],
  "capabilities": [{
    "tool": "GitHub", "operation": "fetch_file", "scope": "example/project",
    "state": "verified",
    "observed_at": "2026-09-14T00:00:00Z",
    "expires_at": "2026-09-15T00:00:00Z"
  }]
}
```

一度判断する事実を `facts.json` に指定します。目的・材料の全文は含めません。

```json
{
  "substantial": true,
  "parent_has_independent_work": true,
  "materials_approved": true,
  "handoff_proportionate": true,
  "requires_repeated_local_access": false,
  "needs": [{"tool": "GitHub", "operation": "fetch_file", "scope": "example/project"}]
}
```

```text
python <plugin-root>/scripts/chat_plan.py --facts facts.json --inventory <CODEX_HOME>/codex-task-routing/capabilities.json
```

本文だけで完結する場合は `needs: []` とし、`--inventory` を省略できます。CLIはネットワークやユーザー設定を探索せず、指定ファイルだけ読みます。重複キー・未知キー・不正な型は拒否します。出力には該当操作の状態と短い理由だけを含めます。

| route | rootの次の動作 |
| --- | --- |
| chat_candidate | 有効なopt-in設定、現在のChat／一時Chat／6 Pro、対象と操作の承認を確認して実依頼を準備 |
| preflight_needed | 必須の未確認・期限切れ操作だけ確認。既に材料が十分ならツール不要の依頼へ縮小 |
| codex | 現担当で進めるか、有効方針の通常子のmodel・effort表を使う |
| not_ready | 不足・取得不能を報告し、既存の承認内で解決。暗黙にWork/APIへ切り替えない |

CLIの終了コード0は分類処理の成功です。`chat_candidate` も送信の許可・モデル・利用枠・内容の十分さを証明しません。Chatの有効化や親のモデル設定は変更しません。接続確認のためだけの追加Chatは作らず、必要な実取得・実操作を本来の仕事と一緒に行って証拠を返します。

## 引継ぎ本文を再生成しない

依頼JSONは一度だけ作り、`chatgpt_route.py prepare` の出力をファイルへリダイレクトします。大きなpromptをツール出力へ流して親が再転記したり、同じ材料を別の分類依頼へ送ったりしません。対応するBrowser機能がある時は [ローカル転送補助](chat-transfer.md) でDOMの値を変数のまま受け渡します。返却もID/hash/形式を機械照合し、親は要点と合格条件・重要根拠だけ読みます。承認確認や品質受入は省略しません。

この仕組みが削るのは分類専用のモデル呼出しと本文の再転記です。rootの判断・Browserツール呼出し・検収にはCodex側の利用負担が残ります。ファイル文字数はトークン実測ではなく、削減率は比較測定なしに提示しません。

## 直列の専門委譲と送信直前のゲート（0.8.0）

従来のfactsは互換性を保ち、delegation省略時は並列として扱います。親に独立作業がないことだけで直列を許可しません。専門委譲に具体的な追加価値があり、ホストの指示・機能が許す場合だけ、factsへ次を追加します。

```json
{"delegation":{"mode":"serial_specialist","host_allows_serial":true,"reason":"材料が完結した専門設計を6 Proへ渡し、結果を受けて実装する"}}
```

`requires_repeated_local_access`は、**Codex側にしかない現物**を繰り返し参照する必要のことです。Chat自身が持つコード編集・テスト環境を使う工程は、実在と権限が確認できれば一律に除外しません。readの台帳からwriteの能力は推測せず、許された実作業の証拠または必要な事前確認を使います。

送信直前は`--live live.json`を必ず渡します。以下は型の例で、日時・可否をその場の観測と確認結果に置き換えます。契約や自己申告だけでtrueにしません。

```json
{
  "is_root": true,
  "route_enabled": true,
  "surface": "chat-temporary",
  "model": "6 Pro",
  "transport": "browser-temporary",
  "transport_authorized": false,
  "actions_authorized": true,
  "quota_state": "unknown",
  "observed_at": "2026-09-15T00:00:00Z"
}
```

`transport_authorized`は具体的な送受信方法が許されることの確認です。利用者のopt-inだけでは足りず、[利用条件](chat-limits.md)の未解決事項を確認します。確認できなければfalseのまま、送信を止めます。`actions_authorized`は今回の対象・操作の承認です。PR作成からmerge権限を推測しません。

`quota_state`は`unknown`／`no_limit_notice`／`exhausted`。no_limit_noticeは現在の6 Pro選択と制限通知がないことの観測で、実残量や次の回答の保証ではありません。上限不明・選択不明・通知未確認はunknownとして必要な確認を行います。未来または2分超の観測は再確認が必要です。回答後にもsurface/model/上限通知を確認します。

`--live`なしは過去の分類呼出し互換用で、結果の`live_checked:false`を送信可能と扱いません。`true`も入力された観測の整合を検証しただけで、実UI・backendモデル・権限の機械的な証明ではありません。legacy `codex-app-tools`のsurfaceは`chat`であり、一時Chatを作成・操作しません。


## 初回操作と実行済みの区別（0.8.1）

初めてのPR作成などは、実行成功を先に要求すると、確認のためだけの書込みが必要になる循環が生じます。実際のChat側の操作スキーマと対象範囲の権限を確認できた場合、該当するcapabilityだけを`permission_checked`として記録できます。`permission_evidence`に確認方法と根拠への短い参照を必須で添えます。インストール済み、操作名の表示、別repoの成功、読み取り権限だけではこの状態にしません。秘密値や認証情報を根拠本文へ保存しません。

```json
{
  "tool": "GitHub", "operation": "create_pull_request", "scope": "example/project",
  "state": "permission_checked",
  "permission_evidence": "架空例：Chat側の操作スキーマと対象repoの書込み権限を確認。試験書込みは未実施",
  "observed_at": "2026-09-15T00:00:00Z", "expires_at": "2026-09-16T00:00:00Z"
}
```

この状態を含む候補は、新しい`--live`によるroot・opt-in・転送方法・今回の操作承認・surface/model・利用制限の確認が通る場合だけ返します。`--live`なしではpreflight_neededです。完全一致のoperation/scope、surface、有効期限も既存と同じく検査します。古いverified記録の意味は変更しません。

出力の`execution_verified:false`は、成功をまだ観測していないことを明示します。権限の自己申告やこの分類結果だけで実行成功とせず、本来の承認済み操作で結果と証拠を確認してからverifiedへ更新します。操作が拒否された場合はblocked等へ更新し、再送・権限拡張で押し切りません。`advertised`だけの必須操作は従来どおりpreflight_neededです。

## 準備した依頼のサイズ（0.8.1）

`prepare`は入力ファイルだけでなく、promptをJSON文字列に格納した最終bundleのUTF-8サイズ（CLIの末尾改行を含む）を256KiB以内に検査します。引用符・バックスラッシュ等の再エスケープで、入力より出力が大きくなるためです。超過時は終了コード2で安全なエラーだけを返し、使用できないbundle本文を出力しません。材料を短くするか、承認された接続先の対象版・範囲を絞った参照へ変更します。上限を超えた本文を切り捨てて送信しません。既に準備済みの旧形式の検証条件は変えません。
