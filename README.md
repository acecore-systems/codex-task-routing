# Codex Task Routing

調査・文書・画像・データ処理・運用・開発の仕事を、必要な判断能力に合わせてCodexの標準サブエージェントへ振り分けるプラグインです。

Acecoreが日常利用で改善している分担原則を既定値として同梱します。親のモデル・effortを維持し、引継ぎ、同じ担当の継続、重要なレビューまで含めて、品質と総利用負担を考慮します。次回送信のモデル選択を勧める機能はありません。節約率は未実証です。

| 子の役割 | 下限 | 標準 | 上限 |
| --- | --- | --- | --- |
| Luna | xhigh | max | max |
| Terra | high | xhigh | max |
| Sol | medium | high | xhigh |
| Astra | high | high | max |

モデル別の具体的な条件を標準値より優先します。原則として直接の子は1体で、親にも独立して進める有用な仕事がある時だけ委譲します。Astra親が方針・重要レビュー・最終受入を担当します。別のモデルで開始した選択も維持します。

## 必要な環境

- Codexのプラグイン、ライフサイクルフック、標準サブエージェントが使えるローカル環境。
- `python --version` がPython 3.11以上を返すこと。Python標準ライブラリだけを使います。
- 起動前同期にはCodex CLIとGitが必要です。Windowsの起動ショートカットには同じPythonの`pythonw.exe`とWindows版Codexアプリを使います。
- 選択したモデル・effortが、そのアカウントと実行環境で利用できること。

最初の検証対象はWindowsとCodex CLIです。設定の要求値を確認しても、実際の子がそのモデルで実行した証明にはなりません。起動時のツールと実行結果を別に確認します。

## 導入

次のコマンドで `main` の配布版を導入します。複数PCで同じ版を固定する場合は、`main` の代わりに同じcommit SHAまたは公開タグを指定してください。

```text
codex plugin marketplace add acecore-systems/codex-task-routing --ref main
codex plugin add codex-task-routing@codex-task-routing
```

ローカルのcheckoutを使う場合は、リポジトリ直下で実行します。

```text
codex plugin marketplace add .
codex plugin add codex-task-routing@codex-task-routing
```

導入後、CLIの `/hooks` でこのプラグインのフックを確認して信頼し、新しいタスクを開始してください。フックの定義を変更した版は、再確認が必要な場合があります。スキルの検出だけでは常時適用されません。[公式フック仕様](https://learn.chatgpt.com/docs/hooks)

開始・再開・コンパクション時に有効方針を読み込みます。子には短い引継ぎ用のコンテキストと参照先を渡します。フック自体はモデル呼出し・ネットワークアクセス・子の起動を行いません。

### 起動前にmainへ自動同期する

0.2.0以降では、最初に起動入口を一度追加すれば、その入口からCodexを開くたびに登録したGit refへ同期します。`--ref main`で導入した場合はmainを追従し、固定したcommit SHAは維持します。固定版を選ぶ場合は変更されないcommit SHAを推奨します。

Windowsでは、プラグイン導入後にPowerShellで次を実行します。リポジトリを別途クローンする必要はありません。

```powershell
$routingPlugin = (codex plugin list --marketplace codex-task-routing --json | ConvertFrom-Json).installed |
    Where-Object pluginId -eq 'codex-task-routing@codex-task-routing'
python (Join-Path $routingPlugin.source.path 'scripts/install_launcher.py')
```

スタートメニューに追加される **Codex Task Routing** から起動してください。必要ならこのショートカットをタスクバーにピン留めできます。インストーラーは起動入口だけを作成し、Codexの起動・終了やプラグインの更新は行いません。既存の通常ショートカットから起動した場合は自動同期されません。

リポジトリのcheckoutがある場合は`python scripts/install_launcher.py`でも同じ入口を作れます。Windows以外では`python scripts/install_launcher.py --mode cli`を使い、生成された`bootstrap.py`をPythonで起動します。Windowsでも`--mode cli --no-shortcut`でCLI用に導入できます。

```text
python <CODEX_HOME>/codex-task-routing/launcher/bootstrap.py --cli -- <Codex CLIの引数>
```

起動時の動作は次のとおりです。

- Codexが完全に終了していれば、対象marketplaceだけを標準CLIで同期してから起動します。更新確認にモデルを呼びません。
- Codex・ChatGPTのアプリやCLIがすでに動いている場合、または稼働状態が判別できない場合は、更新を見送って現在の版で起動します。独自に改名された実行ファイルまでは検出しません。
- 通信・認証・CLIの更新エラーでは、削除や再インストールを試みず起動を続けます。タイムアウト時はランチャーが起動した更新プロセスだけを停止します。
- 同時に別のランチャーが同期している場合、排他を確保できない場合、タイムアウト後の更新プロセスの停止を確認できない場合は、追加の起動を見送ります。同期中に通常ショートカットや別の端末からCodexを起動することまでは排他できないため、普段の起動入口をこのランチャーにそろえてください。
- 個別の`overrides.json`、親設定、指示、フックの信頼設定を編集しません。変更されたフックの再確認はCodex標準の`/hooks`で行います。

診断は`<CODEX_HOME>/codex-task-routing/launcher/state/last-sync.json`に短い結果だけを残します。認証情報やCLI出力全文は保存しません。アプリを長時間開いたままの場合、途中の自動更新は行わず、完全終了後の次回起動で追従します。

起動入口はプラグインの版別キャッシュの外に置き、毎回Codexの取得元から現在の同期処理を読みます。薄い起動用bootstrapと予備の同期処理は初回導入時のコピーです。通常の方針・プラグイン・同期処理の更新は追従しますが、この起動インターフェース自体の変更時はインストーラーを再実行してください。解除する場合はショートカットを削除して通常の起動入口へ戻します。

## 適用確認と変更

リポジトリ直下で実行します。

```text
python plugins/codex-task-routing/scripts/routing.py status --json
python plugins/codex-task-routing/scripts/routing.py render --output-dir outputs/effective
```

`status` は設定・版・上書き・競合の診断です。ホストのフック信頼や実行モデルを推測して「有効」とは判定しません。実際の開始時に方針が渡されたかも確認してください。

上書きは `$CODEX_HOME/codex-task-routing/overrides.json` へ保存します。`CODEX_HOME` が未設定なら `~/.codex` です。上書きがなければ作者の既定値を使います。プラグインの更新・解除でこの上書きファイルを編集・削除しません。

例: Terraの標準effortだけ変更する場合。

```json
{
  "schema_version": 1,
  "models": {
    "terra": { "default_effort": "high" }
  }
}
```

これは使い方の例で、既定値を下げる推奨ではありません。原則自体も `principles.summary` や対応する詳細節を置き換えて変更できます。[設定の詳細](plugins/codex-task-routing/skills/task-routing/references/configuration.md)

## 更新・同じ版の再現・解除

同じ版を再現する場合、両PCの取得元のrefを同じcommit SHAまたは公開タグにそろえ、同じ上書きを用意します。診断のpolicy hashも比較します。モデルの生成結果まで同一になるという意味ではありません。

起動ランチャーを使わず手動更新する場合や0.1.0から移行する場合は、Codexアプリ・CLIをすべて終了してから次を実行します。`upgrade`だけで導入済みプラグインも更新します。

```text
codex plugin marketplace upgrade codex-task-routing
```

異なる版へ切り替えるときは、既存の登録元を確認し、このプラグインを解除してから対象marketplaceの登録を外し、Git refを明示して登録し直します。次の `COMMIT_SHA_OR_TAG` を取得したい版に置き換えます。

```text
codex plugin remove codex-task-routing@codex-task-routing
codex plugin marketplace remove codex-task-routing
codex plugin marketplace add acecore-systems/codex-task-routing --ref COMMIT_SHA_OR_TAG
codex plugin add codex-task-routing@codex-task-routing
```

配布ファイルを変更するPRではmanifestの版も更新します。CIは同じ版のまま配布内容が変わることを拒否します。ローカルの開発ではmanifestのcachebusterまたは版を更新して再導入します。更新は旧導入コピーを削除するため、作業中のフックから自己更新しません。起動後の新しいタスクで適用を確認します。

解除は次のとおりです。通常のCodex設定や上書きファイルは残ります。

```text
codex plugin remove codex-task-routing@codex-task-routing
```

## 既存の分担指示がある場合

Codexホームや作業先の `AGENTS.md` / `AGENTS.override.md` に既知の分担指示があると、二重適用を防ぐためフックは新方針を追加せず競合を知らせます。自動で旧指示を削除しません。旧指示をバックアップして対象の分担節だけ移行し、新しいタスクで適用を確認してください。異なる表現の類似方針をすべて機械検出できるわけではありません。

## 開発と検証

```text
python -m unittest discover -s tests -v
python scripts/check_package.py
```

テストでは既定本文の再現、上書き・無効設定、開始イベント、既存指示との競合、ファイル保護を検証します。実際のフック信頼操作・通常会話の分担判断・他OSは別の確認対象です。検証実績は [docs/validation.md](docs/validation.md) を参照してください。

詳細は必要な節だけ読みます。方針の正本は `defaults/config.json`、分類と引継ぎのテンプレートは `defaults/templates/` にあります。既定の元文書は個人パスと適用経路を調整し、意味を保った正本スナップショットと照合しています。

このプラグインは認証・会話履歴・メモリ・契約の利用率を読みません。方針フックはローカルの有効方針キャッシュと利用者が指定した成果物を書き出します。任意の起動ランチャーは専用の入口・設定・同期診断を保存し、標準CLIによるmarketplace更新を行います。内部ログ解析や常時計測は含みません。

MIT License · Acecore
