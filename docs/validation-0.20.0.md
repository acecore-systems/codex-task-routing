# 0.20.0 の検証範囲

2026-10-28、専用ブランチ`codex/terra-61-routing`、基点`8c5cb70cabb0762438d79a6fe9967ef8e3a16f3f`（0.19.0）と今回の未commit差分で検証しました。

## 方針の意味変更

- `models.terra61`を`gpt-6.1-terra/xhigh`の正式候補に追加しました。下限high・上限maxを保持し、自動選定は有効なdefault_effortを使います。Solと同列の実装・修正・テスト候補として、実際の利用可否・対象適合・既存担当の継続から選びます。比較実測がないため性能・品質・費用の優劣は断定しません。
- schema 1の旧`models.terra`は互換専用のまま選定対象外です。旧設定を`terra61`へ移したり、新候補として黙って有効にしたりしません。既存のLuna・Sol・Astraのモデル/effortと明示override、親の実設定を保持します。
- 標準孫は割当て役割としてAstra→Sol/Terra/Luna、Sol/Terra→Lunaまでです。Sol/Terra間の再委譲、孫から先、同モデル・上位役割は禁止します。通常Chatの所有者はrootまたはroot直下Sol子だけで、Terra子への権限追加はありません。
- 要約・詳細・分類表・引継ぎ・スキル・両フック・設定例を同期しました。`principles.summary`等の文章overrideが旧候補記述を保持する場合に備え、候補表とTerra/旧terra/Chat境界をsummaryの外に表示します。`status.override_review`と両フックは対象キーの確認案内を出し、上書き原文を自動変更しません。意味の矛盾を自動判定したとは扱わず、未解消ならrootへ返します。

これは候補と役割境界の意味変更なのでminor版を上げました。配布形式・フック定義・通常の起動方法・Cloud/Chat転送・認証方式は変更していません。

## 検証結果

Windows / Python 3.13.15。稼働中のCodexホームを使用せず、隔離ホームとfixtureで検証しました。

| 検証 | 結果 |
| --- | --- |
| `python -m unittest discover -s tests -v` | 305件成功、skipなし、終了0（11.600秒） |
| Terraの指定値 | 既定・明示override・effort範囲を検査し、有効方針・詳細・両フック・statusの一致を確認 |
| 旧設定の互換 | 旧terra overrideを変更せず受理し、選定表・子フック・configured_child_modelsへ旧IDを出さないことを確認 |
| 古いsummary | 旧3候補の独自summaryと別の認証原則を保持し、Terra候補・対象キーだけの注意・root返却条件が両フックに届くことを確認 |
| Chat境界 | terra61のdepth 1/2はinspect・dispatch・receiveすべて`actor_not_eligible`。root/root直下Solの既存条件を維持 |
| フック上限 | 200文字以上の導入先、両Chat transport、両フックの既存回帰テストが成功。8000文字上限を維持 |
| `python scripts/check_package.py` | 成功。manifest、参照、フック、defaults、配布物の個人パス・秘密値・未完成素材を検査 |
| `git diff --check` | 成功 |
| 生成fixture | 3詳細文書を隔離ホームで生成し、provenanceのSHA-256と一致 |

最初の制限トークンによる全件実行は、既存のprivate temp・atomic link/replace・Windows ACL制約で67エラーと、保存失敗に起因するHTTP 422の1失敗でした。製品の保存処理や権限を変更せず、同じソースを通常ユーザー権限で実行すると全305件が成功しました。テスト中のfixtureは各テストが後処理し、秘密を含まない検証ログはGit対象外です。

## 確認範囲と残対応

この記録は設定解決・生成文書・フック・ローカルhelperの検証です。モデルへの実委譲、実行backend、品質・速度・利用量比較、通常Chatの実送受信、0.20.0の実Cloud実行、他OSはこの検証から推定しません。今回の実モデル試用の結果は、実施した親担当の作業記録で別に確認します。

稼働プラグインの更新、利用者のoverride・AGENTS・config.toml・認証・フック信頼の変更は行っていません。配布後の利用環境への導入、実フックでの版/hashと候補・override注意の確認は別工程です。commit・push・PR・CIは親担当が実結果を記録します。今回の子担当はブラウザーを開いておらず、新規worktreeやbranchも作成していません。
