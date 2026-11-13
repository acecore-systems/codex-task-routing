# Drex に定型判定を割り当てる

親は、公開・合成テキストの分類、明文化した条件の判定、基準付き採点を Drex Max v1.1 に割り当てられます。対象は、材料と設問がそろい、選択肢・確率・点数で回答できる新規作業です。適任の担当が継続中の場合は引き続きその担当が進めます。公開 Web の調査は Muse Ember の経路を使います。最終判断や実行操作は親が扱います。[Nace Decisions API](https://console.nace.ai/docs/drex/api-reference/decisions)

既定は無効です。Codex ホームの `codex-task-routing/drex-decisions.json` に `{"schema_version":1,"enabled":true}` を置くと、開始フックが親へ候補を案内します。別途、`local_auth_nace_decisions` を提供する Local Auth が必要です。設定済みコピーで `NACE_DECISIONS_PLAN` を有効にし、同じ Local Auth の `NACE_DREX_PLAN` と保存済みの Drex キーを使います。フックは設定を読み、案内だけを返します。

### 準備と受入

1. 現在のホストで専用ツールが利用でき、Nace への送信、入力資料、費用が承認済みであることを確認します。同じ範囲の既存承認を再利用します。本文と設問には公開・合成テキストだけを使います。
2. facts に `schema_version:1`、`actor:root`、`data_class:public_or_synthetic`、確認済みの `host_authorized`、`target_authorized`、`materials_authorized`、`cost_authorized`、および下記の `request` を指定し、`python <plugin-root>/scripts/drex_decisions.py prepare --facts <facts.json>` を実行します。helper が返した `status` 用の引数を専用ツールに渡し、結果を facts の `status` に追加して同じ prepare を実行します。
3. `ready:true` なら、返された `run` の引数をそのまま同じツールに一度渡します。送信前に経路が利用できない場合は有効方針に従って進めます。送信後に結果が不明な間は、同じハッシュの `status` で照合します。実行履歴がある依頼はその結果を使います。
4. 同じ `request` とツールから受け取った `result` を `drex_decisions.py verify --facts <facts.json>` に渡します。helper が完了状態、モデル、入力ハッシュ、設問と回答の一致を確認します。親は選択肢と数値の意味を元資料に照らして確認し、曖昧な判定を保留します。

### 入力例

```json
{
  "input": "合成された障害報告。更新後に保存を押すとアプリが終了する。",
  "questions": [
    {
      "type": "choice",
      "name": "category",
      "instructions": "報告内容を分類してください。",
      "choices": [
        {"value": "bug", "description": "既存の動作が壊れている"},
        {"value": "feature", "description": "新機能を求めている"},
        {"value": "unclear", "description": "判断できる情報がない"}
      ]
    },
    {
      "type": "predicate",
      "name": "has_reproduction_step",
      "instructions": "再現操作が一つ以上書かれていますか。"
    }
  ]
}
```

テキストは 32 KiB、依頼全体は UTF-8 で 64 KiB、設問は 1–16 件です。設問の `name` は一意にし、半角英数字とアンダースコアで 1–64 文字にします。`instructions` は 2 KiB までです。`choice` の `choices` は 2–16 件で、`value` に半角英数字・ハイフン・アンダースコアで 1–64 文字の一意な値を指定できます。任意の `description` は 1 KiB までです。`score` の `levels` は、段階順に並べた 2–16 件の `{"label":"…"} です。ラベルは一意で、各 64 UTF-8 バイトまでです。
