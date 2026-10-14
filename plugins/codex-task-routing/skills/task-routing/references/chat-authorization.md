# 通常Chat Proの許可根拠と確認範囲（0.11.0）

## まず、何を許可判定しているか

通常Chat内で利用者の依頼を受け、接続済みGitHubや実行環境を使う作業と、Codex親から**別の通常Chatへ依頼を送信し、その回答を自動回収する転送**は別の操作です。`chat_plan.py --live`の`transport_authorized`は後者だけの確認です。現在のChat内のネイティブツール利用に、別Chatへの転送許可やCodexのroot・opt-in・独立した親作業を要求しません。これは新しい転送方式を追加したり、Codexからの未許可転送を直接依頼扱いにする変更ではありません。

通常Chatで利用可能な操作仕様、対象repoの権限、利用者が今回承認した範囲を確認できれば、調査・編集・テスト・PR・レビュー・CI確認をまとめて担当できます。マージまで明示依頼されていれば同じ承認を参照し、段階ごとに同じ質問を繰り返しません。PRだけの依頼からマージ・配備の承認は推測せず、ホストが実際に要求する確認やブランチ保護は省略しません。接続済みGitHubに書込み機能があっても、コンテナのネットワーク、テスト環境、利用者PCへのアクセスは別に確認します。

## 制約の三層

| 層 | 根拠と確認方法 | 混同しないこと |
| --- | --- | --- |
| OpenAI・接続先の公式条件 | 適用対象の利用規約、公式の機能仕様・許可条件を対象操作に照合する。個人用と業務用等の適用範囲も区別する | ツールの存在やユーザーのopt-inだけでは、実際に禁止された抽出・制限迂回を許可できない |
| 実行ホスト | このセッションに提供された操作スキーマ・利用指示・接続先権限・管理者制御・実際の承認応答を確認する | 一般的なBrowser機能の存在は、別Chatへのモデル委譲・自動回収まで認めた証拠ではない。読取権限は書込権限ではない |
| このrepoの設計 | root限定、明示opt-in、Temporary Chatの既定、Work/APIへの暗黙切替禁止、モデル表示確認、ID/hash、2分のlive観測、最大7日の接続台帳、転送証拠の入力形式 | これらの独自条件・期限・フィールド名を「OpenAI公式が要求する条件」と説明しない。分類結果は送信や実行の許可証ではない |

[OpenAI利用規約](https://openai.com/policies/row-terms-of-use/)は自動・プログラム的なデータ/Output抽出や保護措置の迂回を制限しています。一方、[Connected apps](https://help.openai.com/en/articles/11487775-connected-apps-in-chatgpt)は対応する接続先で作成・更新を含む操作を提供し、実際のアカウント・ワークスペース権限と承認を要求しています。ネイティブ接続操作と別Chatからの自動抽出を一括して禁止・許可とは判断しません。公式仕様とホスト契約が具体的な方法を支えているなら、追加の「例外許可書」を一律に要求しません。ただし、適用される上位禁止に反する場合や根拠が曖昧な場合は送信を止め、対象操作を明示して確認します。下位のツール説明は上位禁止を上書きできません。

2026-10-14に確認した[GitHub Help](https://help.openai.com/en/articles/11145903-connecting-github-to-chatgpt)には読取専用との説明が残っていますが、本作業の通常Chat接続では書込み操作も提供されています。一般記事の能力説明と実セッションの仕様・結果は別に記録します。機能が異なることだけで公式禁止と判断せず、今回の成功を全利用者・全repoへの保証にもしません。

## `transport_authorized`の根拠

`route_enabled`は別経路を使う意思、`materials_approved`は共有する材料の承認、`actions_authorized`は今回の対象操作の承認です。一つの依頼が複数を明確に含むなら同じ依頼を根拠として再利用できます。これらだけから転送方法の適法性・ホスト許可は導きません。

`transport_authorized`は、親が実際の方法を確認した結果を渡すbooleanです。falseは未確認または不許可のまま維持します。0.11.0ではtrueによる候補化に、次の`transport_evidence`も必要です。根拠を確認せずtrueだけ入力することを避ける**repoの検証形式**であり、新しいOpenAI承認手続ではありません。0.10.1の設定ファイルや保存済みliveを自動更新しません。

| フィールド | 必要な証拠と確認方法 |
| --- | --- |
| `basis` | `host_tool_contract`: 現在のホストが対象の別Chat送信・回収をサポートすると明示した契約。`official_documentation`: 対象方法・適用条件を明示した公式資料。`provider_permission`: 当該方法に対する提供者の具体的な許可。いずれも適用される上位禁止がないことを確認 |
| `reference` | 上記を確認できる短い参照と条件。操作名・文書節・対象版など。秘密、会話URL、認証情報は含めない。一般的なツール一覧・有料プラン・残量だけは不可 |
| `transport` | 今回のliveと同じ`browser-temporary`または`codex-app-tools`。一時Chat非対応のツールを一時Chatに転用しない |
| `operations` | 証拠が対象に含める操作。候補化には`send`と`receive`の両方が必要。送信許可だけで回答回収まで許可済みにしない |

現在の対象フローを支えるホスト契約が確認できた場合の**架空の形式例**です。実際に根拠を確認する前にコピーして許可済みにしないでください。

```json
{
  "transport_evidence": {
    "basis": "host_tool_contract",
    "reference": "架空例: 対象版のホスト指示と操作仕様がこの別Chat送信・回収を明示的に許可。上位条件との不整合なし",
    "transport": "browser-temporary",
    "operations": ["send", "receive"]
  }
}
```

これはliveへの追加フィールドで、単独の入力ではありません。`transport_authorized:false`のままでは、証拠オブジェクトがあっても候補になりません。trueでも根拠なしは`transport_evidence_missing`、transport不一致は`transport_evidence_scope_mismatch`、片方向のみは`transport_evidence_incomplete`です。`constraint_layer:repository_preflight`はrepoが証拠不足として停止したという意味で、OpenAIが当該操作を禁止したとの判定ではありません。

CLIは参照先を取得せず、内容の真偽・適用性を検証しません。親は根拠を確認したうえで入力します。`observation_source:caller_supplied`と`authorization_attested:false`は、候補結果が権限の実証を行っていないという意味です。候補を一律不許可とする値ではなく、送信の最終判断は確認済みの根拠を持つ親が行います。既存の有効な証拠は再利用し、ホスト・方法・範囲・適用条件が変わった部分だけ確認し直します。別経路の許可が未確認なら、既存承認内のCodex作業や利用者自身の通常Chat依頼を案内しますが、停止した自動転送を別手段で押し通しません。

## ProのUI確認とバックエンド

必要なのは**モデル選択欄の選択値**です。現在の`Pro`と旧`6 Pro`、`GPT-6 Pro`を同じ要件の表示別名として扱い、大小文字と前後・連続空白だけ正規化します。`Sol Pro`、`Thinking`、`GPT-6`だけの表示、APIモデルID、アカウントのPro契約バッジやChatの自己申告は代用できません。

`config.required_model`は希望条件、inventory/liveの`model`は選択欄から取得した観測です。両者を混同しません。`status`は`model_selection_observed:false`を返し、UIを見たとは主張しません。`Pro`だけから特定のbackend IDを推測せず、実行側の信頼できる情報がなければ`backend_model:unknown`を保持します。

[2026-10-07の公式リリースノート](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)はGPT-6のChatGPT展開とPro reasoning optionに言及しています。ただし公開情報は個別セッションの実行モデルの証明ではありません。本修正の調査時には利用者端末のライブ画面を直接取得できておらず、`Pro`表示は利用者報告と公開仕様に基づく互換対象です。実際の転送時には親が許可された画面/DOMで送信前後に選択値を確認します。旧別名からProへの表記変更だけをフォールバックとせず、Thinking等への実際の選択変更や確認不能は未確認として停止します。
