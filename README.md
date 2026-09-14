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

Gitから導入した場合、現在登録しているrefの更新を取得するコマンドは次のとおりです。

```text
codex plugin marketplace upgrade codex-task-routing
codex plugin add codex-task-routing@codex-task-routing
```

異なる版へ切り替えるときは、既存の登録元を確認し、このプラグインを解除してから対象marketplaceの登録を外し、Git refを明示して登録し直します。次の `COMMIT_SHA_OR_TAG` を取得したい版に置き換えます。

```text
codex plugin remove codex-task-routing@codex-task-routing
codex plugin marketplace remove codex-task-routing
codex plugin marketplace add acecore-systems/codex-task-routing --ref COMMIT_SHA_OR_TAG
codex plugin add codex-task-routing@codex-task-routing
```

ローカルの開発ではmanifestのcachebusterまたは版を更新して再導入します。実行中のタスクが自動で同じ版へ切り替わるとは扱わず、新しいタスクで確認します。

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

このプラグインは認証・会話履歴・メモリ・契約の利用率を読みません。ローカルの有効方針キャッシュと利用者が指定した成果物だけを書き出します。内部ログ解析や常時計測は含みません。

MIT License · Acecore
