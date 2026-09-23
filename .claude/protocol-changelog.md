# Annotty HIL Protocol 変更履歴

`protocol/protocol.md` の改訂履歴。実装者向けの仕様書からはノイズになるため、ここで管理する。
仕様を改訂したら、この表に追記し、`protocol_version` を上げる（方針は knowledge.md「`protocol_version` の運用ポリシー」）。

| バージョン | 日付 | 変更点 |
|---|---|---|
| 1.0 | 2026-04-29 | 初版。多クラス対応、palette クライアント所有、エラー形式統一、`protocol_version` 導入、`/config` 追加、`/status` の metric 抽象化、`/models/latest` のヘッダベース MD5 同期。 |
| 1.0 (rev) | 2026-04-29 | プール定義を 2 段（`unannotated/completed`）から 3 段（`pending/submitted/fixed`）に再編。`pending` は HITL 前で seed label を任意で持てるが学習には使わない。`submitted` は HITL 後・再編集可・学習対象。`fixed` は read-only の固定データセット・学習対象。`PUT /submit/{id}` は元プールに応じて物理移動 / 上書き / 拒否を分岐。`/info.counts` を 3 フィールドに、`/labels/{id}/download` の検索順を `submitted → fixed → pending` に、`/next` を `pending` プールのみから候補抽出。**PeriorbitAI 側へ移植前のため protocol_version は 1.0 のまま破壊改訂**。 |
| 1.0 (rev2) | 2026-04-30 | 実装の事実に合わせて clarification。背景色を `(0, 0, 0)` 黒から `(255, 255, 255)` 白に変更（iPad の `ColorMaskParser` および `PNGExporter` がいずれも白を背景として扱う実装になっていたため）。`palette[0]` の慣習を白に明記。ニアホワイト許容（成分 ≥ 250）を §5.1 に追加。`POST /config` を呼ばないクライアントの存在を §5.2 / §8 / §9 に明記。サーバーは legacy 単一チャンネル grayscale PNG の submit を受け付けてよいと §5.1 / §9 に追記。`/labels/{id}/download` は pool に依存せず必ず試行し 404 を握りつぶすことをクライアント実装ガイド §8 に追加（pending seed の取りこぼし防止）。**互換破壊なし — 振る舞いは既存 iPad 実装の追認**。 |
| 1.0 (rev3) | 2026-05-01 | class-id ラベルの事前 upscale ガイドラインを追加。§5.1 の「サイズ」を「画像と同じ or 整数倍 OK」に拡張。§9 に項目 10 を新設し、iPad の `InternalMask.calculateDimensions` と同じ式でターゲット解像度を算出して per-class Gaussian + argmax で事前 upscale する手順を明記。class-id データには bilinear/bicubic が使えない（中間値が生まれる）制約と、NEAREST だけでは段数が増えないため iPad 表示が blocky になる根本原因を文書化。参考実装として `server/scripts/smooth_labels.py` を追加。**互換破壊なし — 既存サーバーはそのままで動作するが、エッジの滑らかさが向上する**。 |
| 1.1 | 2026-09-24 | 通信形式は変えず、曖昧さと矛盾を解消。**クラス定義はサーバー、palette はクライアント**と役割を分け、クラス番号で対応させる（名前で判定しない）。サーバーは `/config` の前からクラス定義を返す。`/config` はクラス定義が一致すれば palette を無条件で採用（不一致は 409、palette 変更時の 409 は廃止）。互換性ルール（§2.1）と適合テスト（`protocol/conformance_test.py`）を新設。全レスポンスに必須/型の表を追加。マスクのサイズ: クライアント→サーバーは画像と同じ、サーバー→クライアントは画像と同じか表示解像度（rev3 の事前拡大ガイドラインを §9 に要約）。パレットにない色を含む提出は 400。RGBA は α を無視して受け付ける。legacy グレースケールの規定を削除。日時の例外（モデル更新時刻は UNIX 秒）を明記。`/infer` はクエリなしで動作すること。CoreML 配信を任意機能に（404 / 501）。認証キーは前後の空白を除いて比較。仕様書を `docs/` から `protocol/` に移動し、変更履歴を仕様書から分離。 |
