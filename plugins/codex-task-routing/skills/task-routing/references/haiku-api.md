# 任意の Haiku API 経路

`claude-haiku-5-5` / `xhigh` を、通常の実装・調査を含むまとまった工程の候補として検討できます。Lunaの定型工程に限定せず、品質・費用の優劣は推測しません。適任の既存担当とCloud/Proの優先は維持します。標準Codex子とは別の、root所有の経路です。通常Chatのfallbackではありません。既定では無効で、フックは実行しません。

実行は、現在のホストが外部プロセスへの委譲を許し、対象操作とAnthropicへの資料送信が認められた場合だけです。標準子に限定するホストではplanまで。JSONのbooleanは呼出し元が確認した事実の記録であり、権限を作りません。明示opt-in、予算、転送する資料と対象ファイル、操作範囲、Git変更、成果の受入を別々に確認し、既存の承認を同じ範囲で再利用します。秘密・不要な個人情報を依頼や対象に含めず、対象repoにCLIを信頼できることを確認します。既存Claude Code 2.1.286以降と実行時の `ANTHROPIC_API_KEY` が必要で、自動インストール・鍵保存はしません。

実行時の環境は最小限にし、API keyと公式 `https://api.anthropic.com` を指定し、OAuth・他provider・proxy等は引き継ぎません。ただし `--restricted` でも管理者のmanaged settingsは適用されます。管理設定が第三者providerや別endpointへ変更していないことを、秘密を閲覧せず呼出し元が別途確認し、確認不能なら `official_endpoint_verified` をfalseにして実行しません。環境変数の指定だけでは実際の接続先の証明にはなりません。CLIは `--bare --restricted --disable-slash-commands --strict-mcp-config`、セッション非永続化、`dontAsk` と制限されたツールで起動します。CLI permission はOS sandboxではありません。ホスト側のsandbox・権限を維持し、その境界を超える能力を与えません。

## 入力と実行

UTF-8の依頼ファイルとJSON requestを用意します。次は形式の例であり、値は対象・承認に応じて設定してください。`input_sha256` は依頼ファイルのUTF-8バイトのSHA-256、`cwd` は絶対パスのrepo root、`head` は完全なcommit IDです。既存のdirty作業と混ざらないよう、この版はcleanなbranchだけを開始できます。必要な指示や判断材料は依頼文に明示します（`CLAUDE.md`等は自動読込みしません）。

```json
{
  "schema_version": 1, "enabled": false, "actor": "root", "request_id": "example-task-1",
  "input_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
  "cwd": "/absolute/repository", "branch": "codex/example", "head": "0000000000000000000000000000000000000000",
  "tools": ["Read", "Glob", "Grep", "Edit"], "bash_rules": [],
  "max_turns": 10, "max_budget_usd": 2.0,
  "host_execution_allowed": false, "target_operations_allowed": false,
  "materials_transfer_allowed": false, "official_endpoint_verified": false
}
```

`tools` は `Read`, `Glob`, `Grep`, `Edit`, `Write`, `Bash` から明示します。テストを任せる場合だけ `Bash` と、例えば `"bash_rules": ["Bash(python -m unittest *)"]` のような承認済みコマンド範囲を指定します。無条件のBash、任意の構文やMCP・skill・子エージェントは指定できません。Bashルールのパターンは完全なコマンド封じ込めを保証せず、信頼できるコード・依存関係を対象にし、API keyを含む子環境がテストから見え得ることに注意してください。シェル操作や対象範囲はホストの実際の制限と承認に従います。

```text
python <plugin-root>/scripts/haiku_route.py inspect --request request.json
python <plugin-root>/scripts/haiku_route.py plan --request request.json
python <plugin-root>/scripts/haiku_route.py run --request request.json --input task.txt --state-dir <共通の既存ディレクトリ> --claude-executable <Claude-Code実行ファイルの絶対パス>
```

inspect/planは接続・認証確認・プロセス起動を行いません。runは実行直前にCLI版、入力hash、repo root・branch・HEAD・clean状態を確認し、状態ディレクトリに排他的なclaimを書いてから一度だけ起動します。全依頼は同じ保護された共通state rootを使い、別ディレクトリでのclaim重複は検知できないため切り替えて再送しません。途中失敗やtimeoutは結果不明として保持し、元のrequestと対象の状態を照合してrootが判断します。成功応答を保存しても自動受入せず、モデル利用情報が指定modelのみと確認できない場合や権限拒否があれば部分／未確認です。`xhigh`は要求値で実effortは未観測です。結果JSONは依頼の内容や機密を含み得るため、state rootへのアクセスを本人が制限します。鍵とstderrはアダプターが保存・表示しません。

失敗時は固定の分類と終了コードだけを別ファイルに残すよう試みます。記録失敗時もclaimは保持します。timeout時にPythonが停止するのはCLIプロセスで、Bashなどの子プロセスの停止・後処理までは保証しません。再開や後片付けの前に、対象のプロセスと作業状態を確認してください。

`max_turns` はCLIへの指定、`max_budget_usd` はCLIの推定額の上限であり、最終請求額の厳密な上限ではありません。結果・差分・テストと実際の請求をそれぞれ確認します。実API、各OS、managed settings、権限ルールの動作は別途実環境で検証が必要です。

参照: [Claude Code headless](https://code.claude.com/docs/en/headless)、[CLI reference](https://code.claude.com/docs/en/cli-reference)、[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview)。Agent SDKは将来のcallback拡張候補で、この版では使用しません。
