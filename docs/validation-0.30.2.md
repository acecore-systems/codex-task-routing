# 0.30.2 のレビューと検証

2026-11-10、0.30.1 の固定コミット [62ef29b](https://github.com/acecore-systems/codex-task-routing/tree/62ef29b74fe0e51f52f798b0c8b31c6030c51f34) を通常Chatの一時チャットでレビューしました。Git追跡対象の136ファイルを4つの資料にまとめ、過去の検証記録1か所の個人ホームパスを伏せました。Proは全資料を読んだと報告し、コードと公開一次資料を比較しました。指摘の再現、編集、テストはローカル担当が行いました。

## コードレビューの反映

| 指摘 | 反映と確認 |
| --- | --- |
| JSONとして読める破損台帳で観測フックが型例外になる | 観測データを検証し、状態を unknown として方針の出力を継続。schema 1・2で元データの保持を確認。 |
| 導入テストが旧Sol既定と旧フック文言を要求する | インストール先の既定値を同じ配布元と照合。別の有効なeffortでoverrideの反映と再導入時の保持を確認。 |
| 親の low / ultra と、子の実行時に観測したeffortを記録できない | 観測用と子の要求用のeffort検証を分離。実行値には引き続き根拠を要求。 |
| 設定の単独サロゲートで制御された診断が失われる | UTF-8で保存できることを設定の入口で検証。既存ファイルを保持し、設定値を伏せた診断を返す。 |
| キャッシュ公開の競合・失敗で一時領域が残る | 失敗時に今回作った一時領域だけを整理し、競合先の有効なキャッシュを保持。 |

実作業で見つかったWindowsの回答保存も修正しました。完全に書いた一時ファイルを同一ディレクトリ内で上書き不可のリネームにより公開します。Windowsのサンドボックスでハードリンクを作成できない場合も、転送テスト15件と既存回答の保護を確認しました。[Python の os.rename](https://docs.python.org/3/library/os.html#os.rename)

## 委譲と助言の比較

| 仕組み | 2026-11-10時点の確認と採用判断 |
| --- | --- |
| Codex subagents と review | 子への委譲・続行・回収は組込み機能を使います。reviewはStableで、指定した差分を確認します。操作ごとに公開仕様・実ホストでの提供・今回の承認・実行証拠・受入を分ける手順を同梱しました。[Subagents](https://learn.chatgpt.com/docs/agent-configuration/subagents) · [Developer commands](https://learn.chatgpt.com/docs/developer-commands#codex-review) |
| Claude Code subagents と agent teams | サブエージェントは別コンテキストで作業を担当できます。別セッションが連携するteamsは試験的機能です。現行のCodex担当継続に対応する考え方として比較しました。[Subagents](https://code.claude.com/docs/en/sub-agents) · [Agent teams](https://code.claude.com/docs/en/agent-teams) |
| Claude Code Advisor | 試験的機能。実行担当が途中で助言を受け、作業を継続します。全会話とツール結果が助言側へ渡るため、このプラグインでは必要な資料だけを渡す既存の相談方式を使います。[Advisor](https://code.claude.com/docs/en/advisor) |
| GitHub Copilotの委譲 | CLIの /delegate はクラウド実行へ渡し、ブランチとドラフトPRでレビューに戻します。現行のCodex Cloudと同様に、実行・権限・PR受入を分けて確認します。[GitHub Docs](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/delegate-tasks-to-cca) |
| MCP sampling / elicitation | samplingは2026-07-28仕様で非推奨です。elicitationは利用者への追加情報の要求に使われます。既存のChat経路と認証分岐を維持します。[Sampling](https://modelcontextprotocol.io/specification/2026-07-28/client/sampling) · [Elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation) |
| Agents SDK と A2A | SDKの agents-as-tools は元担当が結果を統合し、handoff は担当を移します。A2Aは別のエージェントシステム間で状態と成果物を扱い、Send Messageの冪等化は任意です。相談と担当移管を区別する運用を採用しました。[Orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration) · [A2A specification](https://a2a-protocol.org/latest/specification/) |

中断時の通常Chatに対する永続claimは設計候補です。導入を判断する際は再送リスクが生じた実例を確認し、既存Cloud記録と分離した状態・所有者・依頼の照合を先に検証します。

## 検証範囲

- Windows / Python 3.13.16 で全447件の単体テストとパッケージ検査を実行し、成功しました。
- 隔離ホームで Codex CLI 0.167.0-alpha.3.2 の導入・再導入・解除、overrideと無関係な設定の保持、同梱フックの合成イベントでの出力を確認しました。
- 通常Chatでは一度送信し、回答前後のPro表示と、完了回答に含まれる依頼ID・入力ハッシュを照合しました。表示から取り出したJSONは単独サロゲートを含み、ローカル転送のUnicode検査で保存が拒否されました。採用した指摘と一次資料を上に記録しています。
- 0.30.2の配布物については、稼働中のホストへのフック配送、Cloud・Haikuでの実モデル起動、通常Chatの回答保存までの実環境での往復が未検証です。
