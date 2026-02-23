# Retinal HIL Server

眼底血管セグメンテーションのための Human-In-the-Loop (HIL) アノテーションシステム。
iPad アプリ (Annotty) からアノテーション → サーバーで再学習 → モデル配信の HIL ループを実現する。

## システム概要

```
iPad (Annotty)                    Server (このディレクトリ)
  |                                 |
  |-- GET /images ---------------->| 未アノテーション画像一覧
  |-- GET /images/{id}/download -->| 画像ダウンロード
  |-- POST /infer/{id} ---------->| 推論（5-fold アンサンブル）
  |-- PUT /submit/{id} ---------->| アノテーション保存
  |-- POST /train --------------->| バックグラウンド学習開始
  |-- GET /status --------------->| 学習進捗ポーリング
  |-- GET /models/latest -------->| CoreML モデルダウンロード
```

## セットアップ

```bash
cd server/

# 依存パッケージのインストール
pip install -r requirements.txt

# サーバー起動 (http://0.0.0.0:8000)
python main.py
```

Tailscale 経由で iPad からアクセスする場合は、Tailscale の IP アドレスを使用する。

## ディレクトリ構成

```
server/
├── main.py                # FastAPI サーバー（エントリーポイント）
├── config.py              # パス・ハイパーパラメータ一元管理
├── model.py               # U-Net モデル定義 (smp + ResNet34)
├── data_manager.py        # データアクセス層
├── trainer.py             # 5-fold CV 学習ワーカー
├── inference.py           # 5-fold アンサンブル推論
├── convert_coreml.py      # PyTorch → CoreML 変換
├── version_manager.py     # モデルバージョン管理
├── requirements.txt       # 依存パッケージ
├── scripts/
│   ├── import_images.py       # 画像インポートスクリプト
│   ├── generate_dummy_data.py # テスト用ダミーデータ生成
│   ├── migrate_data.py        # データ移行スクリプト
│   └── test_api.py            # API テストスクリプト
└── data/
    ├── images_completed/      # アノテーション完了済み（read-only）
    │   ├── images/
    │   └── annotations/
    ├── images_unannotated/    # アノテーション対象（iPad から書き込み）
    │   ├── images/
    │   └── annotations/
    └── models/
        ├── pytorch/
        │   ├── pretrained.pt  # 事前学習モデル
        │   ├── current_pt/    # 推論で使うモデル一式
        │   │   ├── fold_0..4.pt  # 5-fold モデル（アンサンブル推論用）
        │   │   └── best.pt       # ベスト fold（CoreML 変換・フォールバック用）
        │   └── versions/      # バージョン管理
        │       ├── v001/
        │       ├── v002/
        │       └── ...
        └── coreml/
            └── SegmentationModel.mlpackage
```

### データ分離設計

| ディレクトリ | 用途 | 書き込み |
|---|---|---|
| `images_completed/` | アノテーション完了済みデータ。学習に使用 | read-only |
| `images_unannotated/` | iPad からアノテーション対象の画像群 | iPad が `PUT /submit` で書き込み |

サーバーコードは `images_completed/` に一切書き込まないため、完了済みアノテーションの事故上書きを防止する。

## API エンドポイント一覧

### 画像・アノテーション

| Method | Path | 説明 |
|--------|------|------|
| `GET` | `/info` | サーバー情報（画像数・ラベル数・学習状態） |
| `GET` | `/images` | 未アノテーション画像の一覧 |
| `GET` | `/images/{image_id}/download` | 画像ダウンロード |
| `GET` | `/labels/{image_id}/download` | アノテーションマスクダウンロード |
| `GET` | `/next?strategy=random` | 次の未ラベル画像 ID を返す |
| `POST` | `/infer/{image_id}` | 推論実行（赤色マスク PNG を返す） |
| `PUT` | `/submit/{image_id}` | アノテーションマスクアップロード |

### 学習

| Method | Path | 説明 |
|--------|------|------|
| `POST` | `/train?max_epochs=50` | バックグラウンド学習開始（5-fold CV） |
| `POST` | `/train/cancel` | 学習キャンセル |
| `GET` | `/status` | 学習ステータス（epoch, dice, fold, version） |

### モデル管理

| Method | Path | 説明 |
|--------|------|------|
| `GET` | `/models/latest` | CoreML モデルダウンロード (ZIP) |
| `POST` | `/models/convert` | PyTorch → CoreML 変換実行 |
| `GET` | `/models/versions` | 全バージョンのサマリーリスト |
| `POST` | `/models/versions/{version}/restore` | 指定バージョンに復元（ロールバック） |

## モデルバージョン管理

### 概要

学習を実行するたびに `versions/v{NNN}/` にモデルファイルと学習記録が自動保存される。
推論に使われるのは常に `current_pt/` 内のモデルであり、バージョンディレクトリはアーカイブ。

### 学習時の自動バージョニング

1. `POST /train` → 学習開始時に `get_next_version()` で次のバージョン番号を決定
2. 各 epoch で `train_loss` と `val_dice` を記録
3. 学習完了後に `current_pt/` のモデルをバージョンディレクトリにコピー
4. `training_log.json` を書き出し
5. キャンセル・エラー時も部分バージョンを保存（status: `"cancelled"` / `"error"`）

### バージョン一覧の確認

```bash
curl http://localhost:8000/models/versions
```

### ロールバック（過去バージョンへの復元）

```bash
curl -X POST http://localhost:8000/models/versions/v001/restore
```

## モデルアーキテクチャ

- **ベースモデル**: U-Net (segmentation_models_pytorch)
- **エンコーダ**: ResNet34 (ImageNet pretrained)
- **入力**: 512x512 RGB
- **出力**: 512x512 1ch (血管セグメンテーションマスク)
- **損失関数**: DiceBCELoss (Dice Loss + Binary Cross Entropy)
- **学習**: 5-fold Cross Validation, AdamW + CosineAnnealing
- **推論**: 5-fold アンサンブル（各 fold の予測を平均）

## 主要パラメータ (config.py)

| パラメータ | デフォルト値 | 説明 |
|---|---|---|
| `IMAGE_SIZE` | 512 | 入力画像サイズ |
| `BATCH_SIZE` | 4 | バッチサイズ |
| `DEFAULT_MAX_EPOCHS` | 50 | fold あたりの最大エポック数 |
| `LEARNING_RATE` | 1e-4 | 学習率 |
| `WEIGHT_DECAY` | 1e-5 | L2 正則化 |
| `N_FOLDS` | 5 | Cross Validation の fold 数 |
| `MIN_IMAGES_FOR_TRAINING` | 2 | 学習に必要な最低画像枚数 |
