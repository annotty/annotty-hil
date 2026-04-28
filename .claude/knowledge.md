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
- iPad とサーバーのレスポンス形式が静かにドリフトする問題は、`docs/protocol.md` のような
  仕様書を作って両側がそれに従う構造にすると防げる
- バージョニングは `protocol_version: "MAJOR.MINOR"` のような文字列が良い。整数だと「破壊変更しか
  できず実運用で詰む」、セマバ的な major.minor.patch まで分けると過剰になりがち
- 訓練ステータスのような optional 多用フィールドは「idle 時は state のみ、running 時は埋める」
  という運用にすると Codable と相性が良い（Swift は `Int? String?` でそのまま受けられる）

### マスク形式の選択肢
- **インデックス画像 PNG（1ch、画素値=クラスID）**: 軽量、クラス数増減に強い、Preview.app では真っ黒
- **RGB PNG（3ch、palette 色で着色）**: 視認性最強、デバッグしやすい、palette を共有する必要あり
- Annotty HIL では後者を採用（プロトコル v1.0、`docs/protocol.md` §5.1）。
  palette は iPad 側が真、`POST /config` でサーバーに伝える方式
- アンチエイリアスや色補間が混入すると palette 逆引きが壊れるので、リサイズは必ず最近傍補間

## ZIPFoundation
- SPM: `https://github.com/weichsel/ZIPFoundation` from "0.9.19"
- `FileManager.unzipItem(at:to:)` で ZIP 展開
