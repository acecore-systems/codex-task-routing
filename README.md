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
- 自動更新にはCodex CLIとGitが必要です。定期実行の登録はWindowsに対応し、Windowsタスクスケジューラと同じPythonの`pythonw.exe`を使います。
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

### 普段の起動方法のまま自動更新する

0.2.0以降では、Windowsで自動更新を一度登録すると、ログオン中に15分ごとに登録したGit refを確認します。`--ref main`で導入した場合はmainを追従し、固定したcommit SHAは維持します。**専用ショートカットは不要です。普段どおりCodexを開いてください。**

プラグイン導入後、PowerShellで次を一度実行します。リポジトリのクローンは不要です。

```powershell
$routingPlugin = (codex plugin list --marketplace codex-task-routing --json | ConvertFrom-Json).installed |
    Where-Object pluginId -eq 'codex-task-routing@codex-task-routing'
python (Join-Path $routingPlugin.source.path 'scripts/install_updater.py') install
```

リポジトリのcheckoutがある場合は`python scripts/install_updater.py install`でも登録できます。登録内容を先に確認するには`install --dry-run`を使います。管理者権限やパスワードの保存は不要です。インストーラーは自動更新用ファイルと本人用の定期タスクを作り、Codexの起動・終了や、その場でのプラグイン更新は行いません。

自動更新の動作は次のとおりです。

- CodexとChatGPTのアプリ・CLIがすべて終了していれば、対象marketplaceだけを標準CLIで同期します。モデルを呼ばず、AIのトークンを消費しません。
- 使用中、稼働状態が判別できない場合、別の更新処理が実行中の場合は、更新を見送って次の確認を待ちます。アプリを長時間開いたままの場合は、閉じた後の確認まで更新されません。
- 通信・認証・CLIエラーでは、削除や再インストールを試みません。更新失敗は短い診断として残り、次の定期確認で再度確認します。利用者のアプリを停止・再起動しません。
- 個別の`overrides.json`、親設定、指示、フックの信頼設定を編集しません。フック定義が変わった版では、Codex標準の`/hooks`による再確認が必要な場合があります。

登録状態と直近の結果の確認、定期実行の解除は次のコマンドで行えます。

```text
python scripts/install_updater.py status
python scripts/install_updater.py uninstall
```

checkoutがない場合は、上のPowerShell例と同じ`install_updater.py`のパスに`status`または`uninstall`を渡します。解除後も個別の上書きやCodex設定は残ります。自動更新用のファイルはプラグインの版別キャッシュの外へ保存し、プラグイン更新後は新しい更新処理を読み込みます。固定された起動インターフェースを変更する将来の版では再登録が必要になる場合があります。

診断先は`<CODEX_HOME>/codex-task-routing/updater/state/last-sync.json`です。認証情報やCLI出力全文は保存しません。PCの休止中・ログオフ中には実行せず、次に実行できるタイミングで確認します。通常起動と更新の開始を完全には排他できないため、**毎回の起動直前に必ず最新版になる保証はありません**。

Windows以外では定期実行の登録に未対応です。Codexが終了している時に`python plugins/codex-task-routing/scripts/updater.py`で同じ一回分の更新処理を実行できます。

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

自動更新を登録せず手動更新する場合や0.1.0から移行する場合は、Codexアプリ・CLIをすべて終了してから次を実行します。`upgrade`だけで導入済みプラグインも更新します。

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

プラグインを解除する場合は、先に`install_updater.py uninstall`で定期実行を解除し、次を実行します。通常のCodex設定や上書きファイルは残ります。

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

このプラグインは認証・会話履歴・メモリ・契約の利用率を読みません。方針フックはローカルの有効方針キャッシュと利用者が指定した成果物を書き出します。任意の自動更新機能は専用ファイル・設定・同期診断とWindowsの定期タスクを作成し、標準CLIによるmarketplace更新を行います。内部ログ解析や常時計測は含みません。

MIT License · Acecore
