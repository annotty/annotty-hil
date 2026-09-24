# Knowledge Base

## Swift Concurrency

### SWIFT_DEFAULT_ACTOR_ISOLATION: MainActor
- プロジェクト設定で全型がデフォルト `@MainActor` に隔離される
- `actor` を独自隔離にするには `nonisolated actor` と宣言する必要がある
- `MLModel.compileModel(at:)` のような同期メソッドも `@MainActor` クラス内からは `await` が必要になる場合がある

## CoreML モデル管理

### ct.ImageType vs ct.TensorType (重要)
- **ImageType**: CoreML が内部で 1/255 スケーリング + CVPixelBuffer/CGImage 入力を要求
  - MLMultiArray を渡すとエラー: "expects input to be an image"
  - バンドルの .mlmodelc では型制約が緩和されるが、.mlpackage からコンパイルした場合は厳格
- **TensorType**: 前処理なし、MLMultiArray を直接受け付ける
  - サーバーからダウンロードするモデルは TensorType を使うべき
- **正規化の違い**:
  - Bundled (ImageType, 内部1/255): Swift は `(pixel - 255*mean) / std` を渡す
  - Downloaded (TensorType, 前処理なし): Swift は `(pixel/255 - mean) / std` を渡す

### CoreML 入出力名の一致
- バンドルモデル: 入力 `"image"`, 出力 `"logits"`
- サーバー変換時に `ct.TensorType(name="logits")` で出力名を明示指定する必要がある
- Swift 側はフォールバックとして最初の MultiArray 出力も探す

### モデルロードパターン
- `.mlmodelc` (コンパイル済み) があればそちらを優先
- `.mlpackage` しかなければ `MLModel.compileModel(at:)` でオンデマンドコンパイル
- ダウンロードモデルは Application Support/HILModels/ に永続保存

## API 設計の教訓

### シンプルなクライアントロジック
- サーバーが既にバリデーションしている場合、クライアントで二重チェックしない
- 例: `/models/latest` は 200(ZIP) or 404(なし) → クライアントは叩いて結果を受けるだけ
- `/status` の `coreml_converted` 事前チェックは不要だった

### "The data couldn't be read because it is missing." の正体
- これは Swift `JSONDecoder` が必須キーを見つけられなかったとき（`DecodingError.keyNotFound`）の
  デフォルトメッセージで、`HILError` のような `LocalizedError` から再投げると素のままユーザー画面に出る
- "サーバーが何も返さない" ではなく "サーバーは返したが Codable が要求するキーが欠落／型違い" のサイン
- 切り分けは curl で生レスポンスを見るのが確実。`unannotated_count` のような未知キーが入っていれば
  サーバーが新仕様、クライアントが旧 Codable のミスマッチ
- `HILError.decodingError(path:underlying:)` を持っておけば「どのエンドポイントの何のキーが NG か」が
  ユーザー側にも開発者側にも一発で分かる

### `keyDecodingStrategy = .convertFromSnakeCase` の限界
- これはキー名のキャメルケース化のみ。`unannotated_count → unannotatedCount` には変わるが
  `total_images → totalImages` のようなセマンティックなリネームには対応しない
- サーバーとクライアントでフィールド名そのものが違うときは `CodingKeys` で明示マッピングが必要

### プロトコル仕様書を単一真実源に置く
- iPad とサーバーのレスポンス形式が静かにドリフトする問題は、`protocol/protocol.md` のような
  仕様書を作って両側がそれに従う構造にすると防げる
- バージョニングは `protocol_version: "MAJOR.MINOR"` のような文字列が良い。整数だと「破壊変更しか
  できず実運用で詰む」、セマバ的な major.minor.patch まで分けると過剰になりがち
- 訓練ステータスのような optional 多用フィールドは「idle 時は state のみ、running 時は埋める」
  という運用にすると Codable と相性が良い（Swift は `Int? String?` でそのまま受けられる）

### マスク形式の選択肢
- **インデックス画像 PNG（1ch、画素値=クラスID）**: 軽量、クラス数増減に強い、Preview.app では真っ黒
- **RGB PNG（3ch、palette 色で着色）**: 視認性最強、デバッグしやすい、palette を共有する必要あり
- Annotty HIL では後者を採用（プロトコル v1.0、`protocol/protocol.md` §5.1）。
  palette は iPad 側が真、`POST /config` でサーバーに伝える方式
- アンチエイリアスや色補間が混入すると palette 逆引きが壊れるので、リサイズは必ず最近傍補間

### プール定義は「label の有無」でなく「ライフサイクル段階」で分ける
- 旧 2 プール設計（`unannotated` / `completed`）は「label の有無」を間接的に状態の代わりに使っていた
  → 「初期画像に既に label がある（参考用）」というケースを表現できず破綻
- 新 3 プール設計（`pending` / `submitted` / `fixed`）は **画像のライフサイクル段階** で物理ディレクトリを分離
  - `pending`: HITL 前。label を持っていても seed 扱い。学習に含めない
  - `submitted`: HITL 後。iPad の Submit で確定したもの。再 submit で上書き、学習対象
  - `fixed`: 完成済み learning set。read-only、HITL 不要、学習対象
- 教訓: **「フラグの組み合わせで状態を表現する」より「物理ディレクトリで分離する」**方が、
  運用時に `ls` で状態が見え、誤操作のリスクも減る
- `PUT /submit/{id}` は元プールに応じて挙動分岐（pending→移動、submitted→上書き、fixed→409）。
  「同じエンドポイントが文脈で違う動きをする」のは API として一見複雑だが、クライアント実装は単純（呼ぶだけ）

### `protocol_version` の運用ポリシー
- 仕様策定中（≒最初の完全実装が動き出す前）の破壊変更は、minor up より **同じバージョンの内容を上書き** が望ましい
- 「この仕様は v1.0 です」と公表してから別実装が現れた後の変更は、必ず minor/major up が必要
- 変更履歴セクションに `1.0 / 1.0 (rev) / 1.1` のように内訳を残せば、移植側は「自分は rev 適用済か」を見て判断できる

## ZIPFoundation
- SPM: `https://github.com/weichsel/ZIPFoundation` from "0.9.19"
- `FileManager.unzipItem(at:to:)` で ZIP 展開

## 座標変換: 位置 vs 長さ
- iPad Retina の `contentScaleFactor`（typically 2.0）は **タッチ位置（point）→ pixel** の変換に使う
  - 例: `MetalRenderer.convertTouchToScreen` で `point * contentScaleFactor`
- ブラシ半径のような **長さ・サイズ量** は元画像 pixel 単位で扱う場合、`contentScaleFactor` を掛けてはいけない
  - マスク座標系への変換は `radius * maskScaleFactor` のみ
  - 「タッチ位置を pixel に変換するから半径も pixel に変換すべき」という直感は誤り
- SwiftUI のオーバーレイ（プレビュー円・SmoothStrokeOverlay）は UIKit point 単位
  - 元画像 pixel → drawable pixel: `* currentScale`（matrix の scale）
  - drawable pixel → UIKit point: `/ contentScaleFactor`
  - したがって元画像 pixel → UIKit point は `* currentScale / contentScaleFactor`

## HIL サーバー接続トラブルの切り分け
- 401 "invalid or missing X-API-Key": クライアントはキーが空でなければ全リクエストに付ける。
  原因はたいてい **値の空白・改行の混入**（iPad での貼り付け、サーバー側の `.env`/`$(cat key)` の末尾改行）。
  → クライアントは trim 済み（2026-09-23）。サーバー側も strip 推奨
- 「応答のデコードに失敗」: `/info` が通っても、次の `/images?pool=` で落ちることが多い。
  `/info` の型一致だけで「互換あり」と判断しない。メッセージに keyNotFound/typeMismatch とキーのパスが出るので、それを見る
- パレット: iPad のパレットは class1 = 赤 (255,0,0) で固定。接続時に `POST /config` で送る（2026-09-23 から）。
  class_names / num_classes は `/info` の値をそのまま返し、色だけ iPad のものを番号で対応させる。
  → 利用者にクラス名の設定をさせない（「名前を合わせないと動かない」設計は煩雑なので避ける）

## LLM にサーバーを作らせるときの教訓
- LLM は仕様書より、**リポジトリ内のコード・テスト・README を真似る**ことがある
  （2026-09: 古い `server/scripts/test_api.py` の `{"images":[{"id":..}]}` が新サーバーにそのまま使われた）
  → 仕様と食い違う古い参照コードは削除するか、「非準拠」と明記する
- 文面だけだと LLM は形を「改善」してしまう。必須/型の表・禁止事項（§2.1）・**適合テスト**（`protocol/conformance_test.py`）で合否を判定させる
- 仕様を変えたら、クライアント（`HILServerClient.swift` の Codable）と適合テストのスキーマを同時に更新する（3つを一致させる）

## マスク送受信の落とし穴（参照サーバーで実際に起きたもの）
- iPad の提出マスクは **RGBA**（`createColoredPNG` → `UIImage.pngData()`）。サーバーが RGBA を拒否すると提出できない → α は捨てて RGB として扱う
- 「R=G=B なら旧形式のクラスID画像」とみなす分岐は危険。**真っ白（全部背景）のマスクがクラスID 255 と誤解釈**されて 400 になる
- 保存形式をクラスIDにしておけば、通信用のパレットはいつ差し替えてもよい（パレット変更を 409 で禁止する必要がない）
- `git push` の前に `git fetch` で、リモートに別の作業（別マシン・別セッション）が入っていないか確認する。今回は `server/` が丸ごと新しい実装に置き換わっていた

## カメラでの文字読み取り（VisionKit DataScanner）
- `DataScannerViewController` で `.barcode(symbologies: [.qr])` を使うには `import Vision` が必要（ないと「missing import of defining module 'Vision'」）。`.text()` なら VisionKit だけでよい
- OCR は `I`/`l`/`1`、`O`/`0` を取り違える。ランダムなキーの読み取りでは誤読が起こりうるので、読み取り直後に接続テストして結果を見せ、貼り付けの手段も残す
- 画面上の長い URL は複数行に分かれて認識される → 各行で探したあと、全行を連結した文字列でも探す
- 実機で試した結果、カメラ読み取りは使いにくく削除した（2026-09-25）。キー付き URL の貼り付け＋自動振り分けで十分だった
