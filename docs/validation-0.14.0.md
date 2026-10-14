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

## 初回実装時点の未検証と後処理

初回実装時の開発セッションにはroot-onlyの上位制約が残っていたため、この新方針を実行許可と扱わず、子からChatを実送受信していません。新しい標準孫も起動していません。実ホストでのrole/depth照合、子→ProのBrowser往復・内容受入、実モデル/effort、実認証、節約・品質改善は未検証です。従来rootのBrowser往復は0.13.0の記録にある範囲で、今回の子経路成功へ一般化しません。

初回実装時は、開発repoのAGENTS.mdにある旧root-only説明の同期を明示承認待ちで保留しました。AGENTS.md/config.toml・利用者設定・稼働プラグイン・フック信頼は変更していません。配布のdefaults・SKILL・テンプレート・フック・plannerは更新済みです。

検証用プラグインは一時homeから解除済みで、テスト用fixtureを後処理しました。必要な証拠は無視対象outputsへ保持し、この担当ではBrowserタブを開いていません。Draft PR対応のため専用branch/worktreeを保持します。マージと利用環境への導入は未実施です。
## 導入後の実送受信受入（2026-10-14）

PR #20で0.14.0を導入した後、本人の明示承認を受けてPR #21でAGENTS.mdの旧root-only記載を同期し、mainとローカルへ反映しました。AGENTS.md同期時の198テスト、4環境CI、マージ後CIは成功しています。プラグインのモデル設定、個人設定、フック信頼は変更していません。

既存の子には古い開始方針が残っていたため送信せず終了しました。新しい標準Sol子には0.14.0の条件付きChat経路が実際に届き、root直下・role=sol・depth=1を確認しました。これは保存ファイルの版だけから推定した結果ではありません。要求はGPT-6.1 Sol/maxで、backendのモデル・effortの独立した観測はありません。

本人が公開済みの方針・actor判定・合成テスト抜粋をProへ送る内容と宛先を明示承認した後、同じ準備済み依頼で検証を再開しました。アプリ内Browserが再開時に利用不能だったため、提供されていた公式Edge Browser pluginを使用しました。新しい通常ChatのTemporary Chatで、選択表示Proを送信前後に確認しています。別CLIやモデルAPIを使用した結果ではありません。

- 承認済み11,050文字の依頼文と入力欄の一致、inspect/dispatch判定を確認して1回送信。再送は0回。
- 同じ担当が同じタブの最終回答を回収。receiveはcollect_reply、dispatch_authorized:falseで、新規送信を要求しませんでした。
- JSON解析、request IDと入力hash、completed状態、転送helperの保存とexit 0、別途CLIのvalidate成功を確認。
- 提供資料に基づく6件の境界ケースと運用チェックをSol子が受入。標準孫とSol→Pro、役割・深さ・所有者・ホスト許可、送信と回収の区別が含まれることを確認。
- 今回のChatとhelperの2タブを閉じ、再一覧で閉鎖を確認。helperも終了。親は保存された回答のhashを完了記録と照合して一致を確認。

実行時の自動承認審査は、当初helper起動を具体的な素材・宛先の承認不足として拒否しました。本人の具体的承認後に同じ操作の再審査が通ったため継続したもので、拒否を別経路で迂回していません。これは今回のホストで観測した条件であり、毎回同じ追加承認を必要とするプラグイン規則は追加していません。

依頼全文・回答全文・会話URL・実行ログ・個人パスは公開記録へ含めず、必要な検証証拠をGit対象外のローカル出力へ保存しました。実送信・回収・照合・意味の受入・タブ/helperの後片付けは、この1件で確認済みです。backend、実残量、IABでの実送受信、他OS/Browser、今回の標準孫起動、実認証、利用量や品質の改善率までは確認していません。
