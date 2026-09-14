# 設定と適用

プラグインの `defaults/config.json` が作者の既定値です。個別設定はCodexホーム内の `codex-task-routing/overrides.json` に分けて保存します。CLIの `--config` で別ファイル、`--codex-home` で別のホームを明示できます。これらのオプションはコマンドより前に置きます。

```text
python plugins/codex-task-routing/scripts/routing.py --config examples/overrides.example.json status --json
```

## モデルとeffort

上書きに含めたフィールドだけを変更します。`schema_version` は1。役割キーは `luna`、`terra`、`sol`、`astra`。各役割は `id`、`min_effort`、`default_effort`、`max_effort` を持ちます。別のモデルを設定しても役割ラベルは変えません。

effortの設定値は `medium`、`high`、`xhigh`、`max` で、下限≤標準≤上限が必要です。これはこの版の子の選定設定であり、親のeffortには適用しません。値が妥当でも、実際のモデルがそのeffortを受け付けるかは実行時の機能で確認します。

未定義キー、重複したJSONキー、型違い、空文字、無効範囲、解決できないテンプレートはエラーとして扱います。設定内容の誤りを既定値への暗黙の切戻しで隠しません。

## 分担原則

`principles` は同名キーの文章を置き換えます。要約のキーは `summary`。詳細のキーは次のとおりです。

| キー | 内容 |
| --- | --- |
| policy_intro | 方針と親の選択 |
| policy_effort | effortの基本条件 |
| policy_timing | 選定のタイミング |
| policy_reading | 読む範囲 |
| policy_classification | 作業分類 |
| policy_test_image | テストと画像 |
| policy_originals | 原本と必須項目 |
| policy_luna_timing | Lunaを検討する条件 |
| policy_retrieval | 定型取得 |
| policy_escalation | 返却と再配分 |
| policy_handoff | 引継ぎ |
| policy_reasoning | effortの具体的な対応表 |
| policy_parent | 親と子の選択 |
| policy_hierarchy | 階層委譲 |
| policy_economy | 総利用負担 |
| policy_observation | 限定的な観測 |
| policy_scope | 保存先と適用範囲 |

文章中では `{{models.terra.default_effort}}` 等の参照を使えます。未知参照や循環は拒否されます。モデル・effortの変更を本文へ反映するため、関連する参照を残してください。

**構文検証は原則の意味の矛盾まで保証しません。** 要約を変える場合は関連する詳細節も確認します。通常はAIに変更したい原則を伝え、関連節の差分をまとめて作り、renderした結果を確認します。分類表に影響する大きな運用変更は、パッケージ側の変更として版を分ける方が管理しやすくなります。

## 状態の区別

- `status` の解決成功: ファイルと設定が正しい。
- Codexのプラグイン有効状態: ホストのプラグイン管理で確認する。
- フックの信頼: `/hooks` で利用者が確認する。
- 方針の読み込み: 新規・再開時の実際のフック出力で確認する。
- 指定model/effortでの実行: 子の実行結果又は利用可能な実行メタデータで確認する。

前の段階の成功だけで、後の段階まで成功したと報告しません。不正設定・旧分担指示との競合がある場合、フックは診断を出し、新方針を適用しません。通常のCodex作業自体は止めません。

上書きとキャッシュには利用者が記述した文章が含まれます。秘密を記述しないでください。初版はキャッシュの自動削除を行いません。不要になった場合は有効な参照がないことを確認し、対象ディレクトリだけを整理します。
