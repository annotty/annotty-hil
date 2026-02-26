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

## ZIPFoundation
- SPM: `https://github.com/weichsel/ZIPFoundation` from "0.9.19"
- `FileManager.unzipItem(at:to:)` で ZIP 展開
