# 通常Chatへのルーティング

この経路は利用者が `chatgpt.json` で有効にした場合にだけ、ルート親が通常Chatへ独立した仕事を渡すための明示的な例外です。既定のCodexサブエージェント方針、親のモデル・effort、承認境界は維持します。フックは経路を知らせるだけで、AIやネットワークを呼び出しません。

既定の `browser-temporary` は、通常Chatの一時Chatを1依頼ごとに開く方式です。作業はChatの利用枠で行い、ChatGPT Work、モデルAPI、CLIモデル呼出しへ切り替えません。利用枠の上限、未ログイン、モデル不明、サポートされたBrowser操作の欠如は、この経路の停止理由です。別経路へ替える場合は理由と使用枠を利用者へ伝え、既存の承認と指示に従います。

## 選定

- 調査、資料比較、文案、設計案、限定レビューなど、渡した材料と接続済みツールで回答が完結するまとまった仕事を候補にします。ローカルの編集・ビルド・テスト、本番操作、秘密を含む資料、現物確認が主な仕事は、親または通常のCodexサブエージェントで進めます。
- 同時に通常Chatへ渡す仕事は原則1件です。親には独立した有用作業を残し、Chatの回答を信頼しない入力として検証して重要判断を引き取ります。Chatを子から再委譲しません。
- 既存の下書きや別タスクの会話を再利用しません。新しい一時Chatを使えない場合は、下書きを上書きせず停止します。

## `browser-temporary` の実行手順

1. 補助CLIの `status` で経路が有効で transport が `browser-temporary` であることを確認し、スコープ、材料、合格条件、許可された操作を整理します。非公開資料の送信は、データと送信先に対する利用者の承認を確認し、秘密や無関係な履歴を含めません。
2. サポートされたBrowser操作で `https://chatgpt.com/` を開き、通常Chatから新しい一時Chatを作成します。既存の下書きがある入力欄を使わず、ログイン入力は利用者に任せます。送信直前に、Chatが選択されWorkが未選択であること、一時Chatであること、UIのモデル表示が `6 Pro` であることを確認します。選択・確認できなければ送信しません。
3. 必要なら一時Chatのプラスメニューで、既存の承認済みプラグインまたはMCPを選択します。新しい接続・権限・独自MCPサーバーの追加はこの設定では承認されません。接続は材料の受け渡し方法の選択肢であり、一時Chatの起動やモデル指定の手段ではありません。
4. `python <plugin-root>/scripts/chatgpt_route.py prepare --input request.json` で依頼を1回だけ準備します。出力された `prompt` を初回の実依頼として送ります。接続確認だけのハンドシェイクは送りません。
5. `prompt` が複数行なら、`typeText` で打ち込まず、サポートされた `paste({format:'text'})` または対応した `fill` で入力します。送信前に入力欄の全文が `prompt` と一致し、既存の文面が混ざっていないことを確認してから、送信ボタンを1回だけ使います。確認できない場合は送信しません。
6. サポートされたBrowser操作で同じ一時Chatの最終回答を取得します。`send_message_to_thread` と `read_thread` は一時Chatに使いません。前者は内部で `isTemporaryChat: false` を固定するためです。30秒程度から間隔を広げて完了を待ち、経過を利用者へ伝えます。10分で回答がなければ未完了とし、必要ならサポートされたhandoff機能でタブを保持します。回答待ちや取得失敗で同じ依頼を再送したり、処理中のタブを勝手に閉じたりしません。
7. 最終回答だけを明示したローカル作業場所へ `reply.json` として保存し、`python <plugin-root>/scripts/chatgpt_route.py validate --request bundle.json --response reply.json` で `request_id` と `input_sha256` を照合します。`blocked` と `missing_input` は成功ではありません。親が合格条件と内容を確認し、必要な成果物だけを保存します。
8. UIの `6 Pro` 表示を回答取得後にも確認します。モデル表示が変わった場合は受入を止め、別モデルの結果として扱います。バックエンドのモデルIDは不明のままにし、自己申告で補完しません。
9. 修正が必要なら、閉じる前の同じ一時Chatで、新しい依頼IDと材料hashを使って修正を依頼できます。結果の受入と保存が完了したら、一時Chatを保存せず閉じます。会話URL、会話ID、認証情報、入力全文、実会話ログを公開repoに保存しません。閉じた後に追加作業が必要なら、必要最小限の材料を新しい一時Chatへ渡し、新しい `request_id` で依頼します。

## ローカル補助CLI

`<plugin-root>` は導入済みコピーのルートです。設定の診断は `python <plugin-root>/scripts/chatgpt_route.py status` で行います。正常な `enabled: true` は設定の検証結果であり、Browser機能、実行モデル、利用枠の証明ではありません。

依頼JSONは `task`（仕事と許可範囲）、`materials`（本文）、`acceptance_criteria`（非空の文字列配列）を持ちます。`request_id` は任意のUUIDで、省略時に生成します。機密データを公開repoへ保存しないよう、ローカルの明示した作業用ディレクトリを使います。

```json
{
  "task": "材料にある二案を比較し、条件を満たす案と理由を日本語で答える。外部操作はしない。",
  "materials": "条件は納期3日以内。案Aは2日、案Bは5日。",
  "acceptance_criteria": ["案Aを選ぶ", "判断根拠となる納期を示す"]
}
```

```text
python <plugin-root>/scripts/chatgpt_route.py prepare --input request.json
python <plugin-root>/scripts/chatgpt_route.py validate --request bundle.json --response reply.json
```

`prepare` のJSON出力をUTF-8の `bundle.json` として保持し、送信するのはそのオブジェクトの `prompt` 文字列です。返信は当該依頼の最終回答だけをUTF-8の `reply.json` に保存します。Markdownの囲いが付いた場合は単一のJSON部分だけを取り出せますが、IDや内容を修正して照合を通してはいけません。`validate` は現行形式と0.4系で生成済みの形式だけを厳密に照合するため、更新中の依頼回収を壊しません。`validate` の終了コード0は形式と対応関係の成功であり、品質、モデル、利用枠、外部ツールの実行を保証しません。

## 既存 `codex-app-tools` 設定

既に `"transport": "codex-app-tools"` を明示した設定は自動で書き換えず、legacyとして受理します。これは従来の直接スレッド送受信との互換性だけを保つ設定で、一時Chatを作成・操作する方式ではありません。一時Chatを使う仕事でこのtransportを選ばず、`send_message_to_thread` を代替手段にしません。

## MCPとの役割分担

既存の承認済みMCPは、通常Chatに材料を渡す別の手段として使えます。独自MCPサーバーの公開、ジョブキュー、結果書き戻し、添付の自動化はこの版に含めません。MCP接続だけで通常Chatを起動したり、Temporary Chatや `6 Pro` を指定できるとは扱いません。

## 確認済みの範囲と制約

この版の自動テストは設定の既定値・legacy互換・依頼IDと材料hashの照合を対象にします。実行ごとに、Browser上で通常Chat、一時Chat、`6 Pro` の表示を送信前後で確認します。Browserを使わずに通常Chatの一時Chatを新規作成し、モデルを指定する正規の外部入口は、この版では扱いません。今後の課題は、必要な正規外部入口の提供と、認証付きMCP常用接続の整備です。
