# Annotty HIL Protocol v1.0

> 標準プロトコル仕様書。Annotty HIL アノテーションシステムにおける
> **クライアント（iPad アプリ等）⇄ サーバー（HIL バックエンド）** 間の通信規約を定める。
> この文書が単一の真実源（Single Source of Truth）であり、
> リファレンス実装（`server/main.py`、`AnnottyHIL/Services/HIL/HILServerClient.swift`）は本仕様に従う。

---

## 0. 設計目標

| 目標 | 実現方法 |
|---|---|
| **クラス数・名前・色がプロジェクトごとに可変** | `num_classes / class_names / palette` を実行時に動的決定。クライアントもサーバーもハードコードしない |
| **クライアントとサーバーが疎結合** | `protocol_version` で互換性を明示。マイナー違いは前方互換 |
| **デバッグ視認性** | マスクは RGB PNG（カラー）。Preview などで開けば一目でクラス分布が分かる |
| **移植容易性** | エンドポイント・JSON スキーマ・エラー形式を厳密に定義。任意言語で再実装可能 |

---

## 1. 用語

| 用語 | 意味 |
|---|---|
| **Image** | アノテーション対象の入力画像（JPEG または PNG） |
| **Mask** | クラスIDをピクセル単位で表すアノテーション。本プロトコルでは RGB PNG として伝送 |
| **Class** | セグメンテーションの分類。クラス ID は `0..num_classes-1` の整数 |
| **Class 0 (background)** | 背景クラス。慣習的に「塗らない」領域に対応する。マスク PNG では `(0, 0, 0)` |
| **Palette** | クラス ID → RGB 色 のマッピング。`palette[i] = [R, G, B]`、長さ `num_classes` |
| **Pool** | 画像の状態カテゴリ。`unannotated`（未アノテーション）/ `completed`（提出済み）の 2 種 |
| **Seed** | サーバーが事前推論で生成した「種マスク」。クライアントがそれを編集してアノテーション完成形にする |

---

## 2. バージョニング

- 本プロトコルは **`protocol_version`** という文字列フィールドで識別する（例: `"1.0"`）。
- 形式は `"MAJOR.MINOR"`。
- **MAJOR が異なる**: 互換性なし。クライアントは接続を拒否し、ユーザーに「サーバー更新が必要」と通知する。
- **MAJOR 一致 / MINOR が異なる**: 前方互換。クライアントは警告のみで動作続行。新しい MINOR で追加された任意フィールドは未知でも無視。
- 仕様変更時は本ドキュメントの末尾「変更履歴」を更新し、`protocol_version` を上げる。

---

## 3. 認証

- 任意の **`X-API-Key`** ヘッダで認証する。
- サーバー側で API キーが設定されている場合のみ必須。
- 不一致／欠落時は **HTTP 401** を返す。
- すべてのエンドポイントに同じキーを要求する（エンドポイントごとの権限分離はしない）。

---

## 4. エラー形式

すべての 4xx / 5xx レスポンスは以下の単一形式に従う：

```json
{ "detail": "<エラーメッセージ>" }
```

- `Content-Type: application/json` 固定。
- `detail` は人間可読な短い文字列（英語または日本語）。
- 機械可読なコードが必要なら HTTP ステータスコードで判別する（追加コードは v1 では持たない）。

| 主な HTTP コード | 意味 |
|---|---|
| 400 | 不正なリクエスト（ファイル形式不正、ID バリデーション失敗等） |
| 401 | 認証失敗 |
| 404 | 画像／マスク／モデルが存在しない |
| 409 | 状態競合（既に訓練中、訓練していないのにキャンセル要求 等） |
| 422 | リクエスト形式エラー（FastAPI 自動生成） |
| 500 | サーバー内部エラー |
| 503 | サービス利用不可（モデル未訓練等） |

---

## 5. データフォーマット

### 5.1 マスク（Mask）

- **MIME**: `image/png`
- **形式**: RGB PNG（3 チャンネル、α なし）
- **サイズ**: 入力画像と同じピクセル数
- **色の意味**: 各画素の `(R, G, B)` がいずれかの `palette[i]` と一致するクラス `i` を表す
- **背景の扱い**: クラス 0（background）は **塗らない**。マスク PNG 上では `(0, 0, 0)` のまま残る
- **未塗布領域**: `(0, 0, 0)` は「クラス 0 にアサイン済み」と「未編集」を区別しない。実用上はどちらも背景として扱う

> **実装注意（サーバー側）**: 受信した RGB PNG をクラス ID 配列に変換するときは、各画素を palette と完全一致比較する（誤差なし）。アンチエイリアスや補間で中間色が出ないよう、クライアントは **必ず最近傍補間でリサイズし、PNG 圧縮以外の変換を経由しないこと**。

### 5.2 Palette

- 形式: `[[R, G, B], [R, G, B], ...]`（外側の長さ = `num_classes`）
- 値: 各成分 0–255 の整数
- **所有権**: クライアント側が管理する。サーバーはクライアントから受け取って保持するだけで、勝手に決めない。
- 同期手段: クライアントが `POST /config` で送信する（§7.10 参照）。

### 5.3 画像（Image）

- 入力画像は JPEG または PNG。サーバー実装は両方を受け付けること。
- ファイル名（`image_id`）は `[A-Za-z0-9_\-\.]+\.(png|jpg|jpeg)` のみ許可（パストラバーサル対策）。

### 5.4 命名規則

- JSON フィールドは **`snake_case`**（FastAPI のデフォルトに合わせる）。
- クライアントが Codable 等で `camelCase` を期待する場合、クライアント側で変換する（例: Swift の `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase`）。
- 日時フィールドは ISO 8601 文字列（例: `"2026-04-29T12:34:56"`）。

---

## 6. ステートモデル

### 6.1 画像のプール

```
            ┌────────────────────┐
            │   unannotated      │
            │  (未提出)           │
            └─────────┬──────────┘
                      │ PUT /submit/{id}
                      ▼
            ┌────────────────────┐
            │   completed        │
            │  (提出済み)         │
            └────────────────────┘
```

- 画像は最初 `unannotated` プールに存在する。
- `PUT /submit/{id}` で人手アノテーション（マスク）が登録されると `completed` プールへ遷移する。
- 一度 `completed` になった画像は通常戻らない（再アノテーションには別途 API が必要だが v1.0 範囲外）。

### 6.2 画像のメタフラグ

| フラグ | 意味 |
|---|---|
| `has_seed` | サーバーがその画像に対する「種マスク」（推論結果や事前準備マスク）を保持しているか |
| `has_annotation` | 人手アノテーションが完了しているか（= `completed` プールに存在するか） |

### 6.3 訓練ステートマシン

```
   idle ──POST /train──▶ running ──完了──▶ completed
                            │                    │
                            │ POST /train/cancel │
                            │                    │
                            └──キャンセル─────▶ cancelled
                            │
                            └──エラー──────▶ error
```

- 同時に複数の訓練ジョブを走らせない（`running` 中の `POST /train` は HTTP 409）。
- `POST /train/cancel` は `running` 中のみ受け付ける（それ以外は HTTP 409）。
- 訓練完了後の `state` は `completed`。次の `POST /train` で再び `running` に遷移し、各種フィールドはリセットされる。

---

## 7. エンドポイント

すべてベース URL `${BASE_URL}` 配下。プレフィックスなし（`/api/` などは付けない）。

### 7.1 `GET /info` — サーバー情報

レスポンス 200:
```json
{
  "name": "Annotty HIL Server",
  "protocol_version": "1.0",
  "num_classes": 7,
  "class_names": ["background", "brow", "sclera", "exposed_iris", "caruncle", "lid", "occluded_iris"],
  "input_size": 512,
  "counts": {
    "unannotated": 2824,
    "completed": 0,
    "total": 2824
  },
  "model": {
    "best_exists": false,
    "coreml_exists": false,
    "version": "0",
    "updated_at": 0.0,
    "md5": null
  }
}
```

| フィールド | 必須 | 型 | 意味 |
|---|---|---|---|
| `name` | ✓ | string | サーバー識別名（任意、UI 表示用） |
| `protocol_version` | ✓ | string | 本プロトコルのバージョン（§2） |
| `num_classes` | ✓ | int | クラス数（≥ 2） |
| `class_names` | ✓ | string[] | クラス名（長さ = `num_classes`、index 0 は `"background"` 推奨） |
| `input_size` | ✓ | int | モデル入力解像度（正方形 1 辺、ピクセル） |
| `counts.unannotated` | ✓ | int | 未提出画像数 |
| `counts.completed` | ✓ | int | 提出済み画像数 |
| `counts.total` | ✓ | int | 総数（= `unannotated + completed`） |
| `model.best_exists` | ✓ | bool | 最良モデルの重みファイルが存在するか |
| `model.coreml_exists` | ✓ | bool | CoreML 変換済みファイルが存在するか |
| `model.version` | ✓ | string | モデル世代識別子（学習回数や日時など、不透明文字列） |
| `model.updated_at` | ✓ | number | モデル更新時刻（UNIX タイムスタンプ秒、未訓練時は 0.0） |
| `model.md5` | ✓ | string \| null | CoreML ファイルの MD5。差分同期に使用 |

> **palette は含めない**: §5.2 のとおりクライアントが所有する。クライアントはローカル設定の palette を使う。

### 7.2 `POST /config` — クライアント設定の登録

クライアントが起動時に palette / class_names / num_classes をサーバーへ通知する。

リクエスト:
```json
{
  "palette": [[0,0,0],[0,230,0],[130,0,235],[255,230,0],[255,0,230],[0,230,230],[255,130,0]],
  "class_names": ["background","brow","sclera","exposed_iris","caruncle","lid","occluded_iris"],
  "num_classes": 7
}
```

- `palette` の長さは `num_classes` と一致しなければならない（不一致なら 400）。
- `class_names` の長さも同じ。
- サーバーは受け取った値を保持し、以降の訓練・推論・マスク変換に使う。

レスポンス 200:
```json
{ "status": "ok" }
```

> **整合性ポリシー**: 既に `completed` プールに画像がある状態で `palette` の **色の組** を変更するとマスクのクラス対応が壊れる。サーバーは `completed_count > 0` のとき palette 変更要求を **HTTP 409** で拒否してもよい（実装裁量）。

### 7.3 `GET /images?pool=<unannotated|completed>` — 画像一覧

クエリパラメータ:
- `pool`: 省略時 `unannotated`。

レスポンス 200:
```json
{
  "pool": "unannotated",
  "count": 2824,
  "items": ["0_celeb_crop_celeb.jpg", "1_celeb_crop_celeb.jpg", "..."]
}
```

- `items` は `image_id` の配列（順序に意味なし）。

### 7.4 `GET /images/{image_id}/meta` — 画像メタ情報

レスポンス 200:
```json
{
  "image_id": "0_celeb_crop_celeb.jpg",
  "pool": "unannotated",
  "has_seed": true,
  "has_annotation": false,
  "bytes": 10844,
  "width": 257,
  "height": 100
}
```

### 7.5 `GET /images/{image_id}/download` — 画像本体ダウンロード

レスポンス 200: `image/png` または `image/jpeg`（バイナリ）。
404: 画像が存在しない。

### 7.6 `GET /labels/{image_id}/download` — マスクダウンロード

人手アノテーション済みマスク（`completed` プール）または種マスク（seed）を返す。
レスポンス 200: `image/png`（§5.1 の RGB PNG）。
404: マスクが存在しない。

### 7.7 `POST /infer/{image_id}` — 推論

サーバーが現行モデルで推論を行い、マスクを返す。

レスポンス 200: `image/png`（§5.1 の RGB PNG）。
503: モデル未訓練。
404: 画像なし。

### 7.8 `PUT /submit/{image_id}` — マスク提出

リクエスト: `multipart/form-data`
- フィールド名 `file`、`Content-Type: image/png`、内容は §5.1 の RGB PNG。

レスポンス 200:
```json
{ "status": "saved", "image_id": "0_celeb_crop_celeb.jpg", "pool": "completed" }
```

- 提出後、画像は `unannotated` から `completed` プールへ移動する（§6.1）。

### 7.9 `GET /next?strategy=<random|uncertainty>` — 次のサンプル

クエリパラメータ:
- `strategy`: 省略時 `random`。サーバー実装は最低でも `random` を提供する。`uncertainty` 等は任意拡張。

レスポンス 200（プールが空でない場合）:
```json
{
  "image_id": "0_celeb_crop_celeb.jpg",
  "pool": "unannotated",
  "has_seed": true,
  "has_annotation": false,
  "bytes": 10844,
  "width": 257,
  "height": 100
}
```

レスポンス 200（プールが空の場合）:
```json
{ "image_id": null }
```

### 7.10 `POST /train?max_epochs=<int>` — 訓練開始

クエリパラメータ:
- `max_epochs`: 省略時はサーバー既定値。

レスポンス 200:
```json
{ "status": "started", "max_epochs": 100, "training_pairs": 50 }
```

レスポンス 409: 既に訓練中。
レスポンス 400: 訓練データ不足（`detail` に最小必要枚数を含める）。

### 7.11 `POST /train/cancel` — 訓練キャンセル

レスポンス 200:
```json
{ "status": "cancelling" }
```

レスポンス 409: 訓練中ではない。

### 7.12 `GET /status` — 訓練ステータス

レスポンス 200（idle 時）:
```json
{ "state": "idle" }
```

レスポンス 200（running 時、すべての optional フィールドが入る例）:
```json
{
  "state": "running",
  "epoch": 12,
  "max_epochs": 100,
  "best_metric": 0.7821,
  "metric_name": "dice",
  "current_fold": 0,
  "n_folds": 5,
  "started_at": "2026-04-29T12:34:56",
  "completed_at": null,
  "version": null,
  "error": null
}
```

| フィールド | 必須 | 型 | 意味 |
|---|---|---|---|
| `state` | ✓ | string | `idle` / `running` / `completed` / `cancelled` / `error` |
| `epoch` | ✗ | int | 現在のエポック（1 始まり） |
| `max_epochs` | ✗ | int | 最大エポック数 |
| `best_metric` | ✗ | number | これまでの最良評価指標値 |
| `metric_name` | ✗ | string | `best_metric` の名称（例: `"dice"`、`"iou"`、`"accuracy"`） |
| `current_fold` | ✗ | int | クロスバリデーション中の fold インデックス |
| `n_folds` | ✗ | int | fold 総数 |
| `started_at` | ✗ | string | 訓練開始時刻（ISO 8601） |
| `completed_at` | ✗ | string | 訓練終了時刻（ISO 8601） |
| `version` | ✗ | string | 訓練完了後のモデル世代識別子 |
| `error` | ✗ | string | `state == "error"` のときのメッセージ |

> **クライアント実装注意**: optional フィールドは「存在しないことがある」と「null」のどちらでも来る前提で実装する。Swift では `Int?` `String?` で受ければどちらも処理できる。

### 7.13 `GET /models/latest` — 最新 CoreML モデルダウンロード

レスポンス 200: `application/zip`（CoreML mlpackage を ZIP 化したもの）。

レスポンスヘッダ（必須）:
- `X-Model-Version: <string>`
- `X-Model-Md5: <string>`
- `X-Model-Updated-At: <UNIX timestamp seconds>`

レスポンス 404: CoreML モデル未生成。

> **クライアント実装注意**: ローカルキャッシュした MD5 と `X-Model-Md5` を比較して、差分があるときだけダウンロードを完了させる。HEAD リクエストでヘッダだけ取得する選択肢もある（サーバー実装は HEAD を許可してよい）。

### 7.14 `POST /models/convert` — CoreML 変換

サーバー側で最新の重みから CoreML mlpackage を生成する。

レスポンス 200:
```json
{ "status": "converted", "version": "20260429-123456", "md5": "abc123..." }
```

レスポンス 503: 変換可能な重みが存在しない。

---

## 8. クライアント実装ガイド

新規にクライアントを書く（または別言語に移植する）場合のチェックリスト：

1. **接続時に `GET /info` を叩き、`protocol_version` の MAJOR を確認**。MAJOR 不一致なら接続を拒否すること。
2. **起動時に `POST /config` で palette / class_names / num_classes を送る**。サーバー側にローカル設定を反映させる。
3. **マスク I/O は §5.1 を厳守**。
   - 送信時: ローカル palette でクラス ID 配列を RGB PNG に着色して PUT。
   - 受信時: サーバーから来る RGB PNG を、ローカル palette で逆引きしてクラス ID 配列に変換。
4. **エラーは `detail` を抽出**して UI に表示。
5. **`/status` の optional フィールドは欠落・null 双方を許容**。
6. **モデル同期は `model.md5` で差分判定**。同一 MD5 ならダウンロードしない。
7. **`/images/{id}/download` の Content-Type が `image/jpeg` のことがある**（PNG 限定にしない）。

---

## 9. サーバー実装ガイド

新規にサーバーを書く（または別言語に移植する）場合のチェックリスト：

1. **`GET /info` の `protocol_version` を必ず返す**。
2. **`POST /config` でクライアントから palette を受け取り、内部状態として保持する**。
   - 起動直後は palette 未設定状態でよいが、その間は提出・推論を 503 で拒否する選択肢あり。
3. **マスクは RGB PNG として保存・配信する**。内部的にクラス ID 配列で持つ場合、palette 逆引きで変換すること。
4. **エラーは `{"detail": "..."}` 形式に統一**。FastAPI なら `HTTPException(status_code=..., detail="...")` を投げれば自動でこの形式になる。
5. **画像 ID のバリデーション**: パストラバーサル対策として `[A-Za-z0-9_\-\.]+\.(png|jpg|jpeg)` のみ許可する。
6. **`/status` は idle 時に `{"state": "idle"}` のみ返してよい**。訓練中は §7.12 の optional フィールドを詰める。
7. **`/models/latest` レスポンスヘッダ `X-Model-Md5` を必ず付ける**。クライアントの差分同期はこれに依存する。
8. **CORS ヘッダを付ける**（iPad アプリ以外のクライアント想定なら）。

---

## 10. 動作確認用 curl 例

```bash
BASE="https://example.trycloudflare.com"

# サーバー情報
curl -s "$BASE/info" | jq

# クライアント設定（palette 登録）
curl -s -X POST "$BASE/config" \
  -H "Content-Type: application/json" \
  -d '{
    "palette": [[0,0,0],[0,230,0],[130,0,235],[255,230,0],[255,0,230],[0,230,230],[255,130,0]],
    "class_names": ["background","brow","sclera","exposed_iris","caruncle","lid","occluded_iris"],
    "num_classes": 7
  }' | jq

# 未提出画像の一覧
curl -s "$BASE/images?pool=unannotated" | jq '.count'

# 次の推奨サンプル
curl -s "$BASE/next" | jq

# マスク提出
curl -s -X PUT "$BASE/submit/0_celeb_crop_celeb.jpg" \
  -F "file=@mask.png;type=image/png" | jq

# 訓練開始 / ステータス / キャンセル
curl -s -X POST "$BASE/train?max_epochs=50" | jq
curl -s "$BASE/status" | jq
curl -s -X POST "$BASE/train/cancel" | jq

# モデルダウンロード（ヘッダ確認）
curl -sI "$BASE/models/latest"
curl -s -o latest_model.zip "$BASE/models/latest"
```

---

## 11. 変更履歴

| バージョン | 日付 | 変更点 |
|---|---|---|
| 1.0 | 2026-04-29 | 初版。多クラス対応、palette クライアント所有、エラー形式統一、`protocol_version` 導入、`/config` 追加、`/status` の metric 抽象化、`/models/latest` のヘッダベース MD5 同期。 |

---

## 12. 参照実装

- **サーバー**: [`server/main.py`](../server/main.py)
- **クライアント (iPad / Swift)**: [`AnnottyHIL/Services/HIL/HILServerClient.swift`](../AnnottyHIL/Services/HIL/HILServerClient.swift)

これらの実装と本仕様書の記述に齟齬がある場合、**本仕様書を優先**する。
