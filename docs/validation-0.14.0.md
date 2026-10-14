# 0.14.0の検証と未検証範囲

2026-10-14、main `3f35ffdd8659a1dc8d6b20d133fa9296e4dc6a40`を基準に専用branch `codex/bounded-descendants`で実装しました。モデルexecutorや新しい認証処理は追加していません。

## 変更した境界

標準Codex孫は割当て役割としてAstra子→Sol/Luna、Sol子→Lunaへ限定し、Luna子・孫から先と同モデル/上位は不可としました。これは一般的なモデル性能順位ではありません。root=depth 0、直接の子=1、孫=2を実コンテキストと引継ぎで照合し、role/depth不明はrootへ返します。有効default_effortと既存override、親の実設定を維持します。

明示opt-inのbrowser-temporaryではroot直下Sol子も依頼の準備・一度の送信・回収・内容受入・承認済み修正を所有できます。Astra/Luna子・孫はChat不可、legacyはroot専用です。Sol→Proは標準孫と別経路です。実行担当/所有者・割当て役割・深さ・同じタブ/依頼ID/input hash・送信済み状態を短く引き継ぎ、rootへの回収handoffでも再送しません。ID/hashやactorは送信者・権限の本人証明ではありません。

plannerはinspect/dispatch/receiveで同一のactor検査を使います。旧is_root:trueはactorなしでも互換、is_root:falseだけでは不可です。role=sol/depth=1、実行担当=所有者、現在のhost許可、browser transportを照合し、boolをdepth整数として受理しません。host確認は上位指示・現ホストの観測で、本人に追加承認を必ず求める欄ではありません。既存の有効な承認を再利用し、未確認をtrueへ補完しません。

SubagentStartは役割・深さの証明にならないため、全子をSol扱いせず条件付き案内だけを渡します。disabled/invalid/legacyでは子Chat経路を案内しません。並列の独立作業は委譲する担当自身に必要で、Sol子のPro/Luna委譲をrootの別作業だけで認めません。秘密工程は子からrootへ返し、登録済みlocal-auth・本人同意・MFA・生体認証等の条件を維持します。

## 確認済み

Windows / Python 3.13.15 / Codex CLI 0.162.0-alpha.17.2で実行しました。

```text
python -m unittest discover -s tests -v
# Ran 198 tests; OK (skipなし)
python scripts/check_package.py
# Package checks passed
python scripts/smoke_native.py --output-dir outputs/native-smoke-0.14.0
# 7 checks passed; ok=true
```

actor11件と条件付きhook1件の回帰を追加しました。root互換、全3phaseのSol正例、Astra/Luna子と孫の拒否、role/depth/root矛盾、bool・不正型・欠測、所有者不一致、host false、legacy子拒否、既存の操作・Pro・鮮度ゲート、枠切れ/send拒否時の許可済み回収とrootへの非再送引継ぎを確認しています。既存private-auth先行分岐とID/hash・no-overwrite検証も維持しています。

一時Codexホームのnativeスモークは導入、認証分類、Chat既定無効、両フック、browser条件付き案内、再導入/override保持、解除を確認しました。legacyの文脈長はroot 7574字/子3121字、browserはroot 7612字/子5785字で、既存8000字上限内です。modelsとschemaは基準と一致、生成fixturesとprovenanceを原本から同期しました。release検査とCIは対象commitに対応させて確認します。

## 未検証と後処理

現在の開発セッションにはroot-onlyの上位制約が残るため、この新方針を実行許可と扱わず、子からChatを実送受信していません。新しい標準孫も起動していません。実ホストでのrole/depth照合、子→ProのBrowser往復・内容受入、実モデル/effort、実認証、節約・品質改善は未検証です。従来rootのBrowser往復は0.13.0の記録にある範囲で、今回の子経路成功へ一般化しません。

開発repoのAGENTS.mdにある旧root-only説明の同期は明示承認待ちで保留しています。AGENTS.md/config.toml・利用者設定・稼働プラグイン・フック信頼は変更していません。配布のdefaults・SKILL・テンプレート・フック・plannerは更新済みです。

検証用プラグインは一時homeから解除済みで、テスト用fixtureを後処理しました。必要な証拠は無視対象outputsへ保持し、この担当ではBrowserタブを開いていません。Draft PR対応のため専用branch/worktreeを保持します。マージと利用環境への導入は未実施です。
