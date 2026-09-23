# Annotty HIL Protocol v1.1

> 標準プロトコル仕様書。Annotty HIL アノテーションシステムにおける
> **クライアント（iPad アプリ等）⇄ サーバー（HIL バックエンド）** 間の通信規約を定める。
> この文書が単一の真実源（Single Source of Truth）である。
> サーバーを新規に実装する場合は、本仕様書に従い、適合テスト [`conformance_test.py`](conformance_test.py) を全項目 PASS させること。参照実装は §11。

---

## 0. 設計目標

| 目標 | 実現方法 |
|---|---|
| **クラス構成がプロジェクトごとに可変** | クラス定義（`num_classes` / `class_names`）はサーバーが持ち、`GET /info` で返す。色（palette）はクライアントが持ち、クラス番号で対応させる（§5.2） |
| **クライアントとサーバーが疎結合** | `protocol_version` で互換性を明示。互換性ルール（§2.1）でスキーマの変更範囲を制限 |
| **デバッグ視認性** | マスクは RGB PNG（カラー）。Preview などで開けば一目でクラス分布が分かる |
| **移植容易性** | エンドポイント・JSON スキーマ・エラー形式を厳密に定義し、適合テストで検証できる |

---

## 1. 用語

| 用語 | 意味 |
|---|---|
| **Image** | アノテーション対象の入力画像（JPEG または PNG） |
| **Mask** | クラスIDをピクセル単位で表すアノテーション。本プロトコルでは RGB PNG として伝送 |
| **Class** | セグメンテーションの分類。クラス ID は `0..num_classes-1` の整数。ID 0 は背景 |
| **Palette** | クラス ID → RGB 色 のマッピング。`palette[i] = [R, G, B]`、長さ `num_classes` |
| **Pool** | 画像の状態カテゴリ。`pending`（HITL 前）/ `submitted`（HITL 後）/ `fixed`（固定データ）の 3 種 |
| **Seed** | `pending` プールに置かれている任意の参考 label。クライアントがそれを編集してアノテーション完成形にする。学習には使わない |
| **Training set** | 学習に使うデータ集合。`submitted ∪ fixed` の (image, label) ペア。`pending` は **学習に含めない** |

---

## 2. バージョニング

- 本プロトコルは **`protocol_version`** という文字列フィールドで識別する（例: `"1.1"`）。形式は `"MAJOR.MINOR"`。
- クライアントは **MAJOR だけ**を確認する。MAJOR が異なれば接続を拒否し、「サーバー更新が必要」と通知する。MINOR の違いは無視して動作を続ける。

### 2.1 互換性ルール（サーバー実装者は厳守）

クライアントはレスポンスを**厳密にデコード**する（Swift `Codable`）。必須フィールドが欠けている、または型が違うと、その時点で失敗する。

- 各エンドポイントの表で **必須 ✓** のフィールドは、名前・型・入れ子構造を**そのまま**返すこと。改名・入れ子の変更・配列要素の型の変更（文字列の配列をオブジェクトの配列にする等）は禁止。
- **フィールドの追加は自由**（クライアントは未知のフィールドを無視する）。独自の情報はこの方法で追加すること。
- 任意 ✗ のフィールドは、省略しても `null` でもよい。値を入れる場合は表の型に従うこと。
- クエリパラメータを独自に追加するのは自由。ただし、**そのパラメータがなくても**仕様どおりに動くこと。
- **完成の条件**: 適合テスト（§10）の全項目が PASS になること。

---

## 3. 認証

- 任意の **`X-API-Key`** ヘッダで認証する。サーバー側で API キーが設定されている場合のみ必須。
- すべてのエンドポイントに同じキーを要求する（エンドポイントごとの権限分離はしない）。
- 比較する前に、**サーバーの設定値とヘッダ値の両方の前後の空白・改行を除去する**。
- 欠落・不一致のときは **HTTP 401** を返す。原因が分かるよう `detail` を区別する（例: `"missing X-API-Key"` / `"invalid X-API-Key"`）。

---

## 4. エラー形式

すべての 4xx / 5xx レスポンスは以下の単一形式に従う：

```json
{ "detail": "<エラーメッセージ>" }
```

- `Content-Type: application/json` 固定。
- `detail` は人間可読な短い文字列（英語または日本語）。クライアントはこれを UI に表示する。
- 機械可読なコードが必要なら HTTP ステータスコードで判別する（追加コードは v1 では持たない）。

| 主な HTTP コード | 意味 |
|---|---|
| 400 | 不正なリクエスト（ファイル形式不正、ID バリデーション失敗等） |
| 401 | 認証失敗（§3） |
| 404 | 画像／マスク／モデルが存在しない |
| 409 | 状態競合（既に訓練中、`/config` のクラス定義不一致 等） |
| 422 | リクエスト形式エラー（FastAPI 自動生成） |
| 500 | サーバー内部エラー |
| 501 | 任意機能を実装していない（§7.14） |
| 503 | サービス利用不可（モデル未訓練等） |

---

## 5. データフォーマット

### 5.1 マスク（Mask）

- **MIME**: `image/png`
- **形式**: RGB PNG、または RGBA PNG（α は無視する）
- **サイズ**:
  - クライアントが送るマスク（§7.8）: 入力画像と**同じ幅・高さ**。クライアントは最近傍補間でリサイズしてから送る。
  - サーバーが返すマスク（§7.6, §7.7）: 入力画像と同じ幅・高さ、または §9 の「表示解像度」。
- **色の意味**: 各画素の `(R, G, B)` が `palette[i]` と一致する画素はクラス `i`。
- **背景**: クラス 0 は塗らない。マスク上では白 `(255, 255, 255)` のまま残る（`palette[0]` = 白）。「クラス 0 に指定済み」と「未編集」は区別しない。
- **受信側の変換**:
  - サーバー: 受信した PNG を palette と**完全一致**で比較してクラス ID に変換する。どの色とも一致しない画素があれば **400** で拒否する（`detail` に色と座標を含める）。
  - クライアント: 各成分が 250 以上の画素（ニアホワイト）は背景として扱う。

### 5.2 クラス定義と Palette

**役割分担**:

| 項目 | 持ち主 | 伝達手段 |
|---|---|---|
| `num_classes` / `class_names`（何を塗るか） | **サーバー** | `GET /info`（§7.1）で常に返す（`POST /config` の前も） |
| `palette`（何色で塗るか） | **クライアント** | `POST /config`（§7.2）で送る |

- クラスと色の対応は**クラス番号**で決まる（`class_names[i]` ↔ `palette[i]`）。**クラス名で色の対応や、パレットを採用するかどうかを決めてはならない**。
- 形式: `[[R, G, B], ...]`（長さ = `num_classes`、各成分 0–255 の整数）。`palette[0]` は白 `[255, 255, 255]`。
- サーバーは `POST /config` を受けるまで、自前の既定 palette を使う。既定 palette は下の iPad パレットと同じにすることを推奨する。
- サーバーがクライアントに返すマスク（§7.6, §7.7）は、その時点で採用している palette で塗る。内部の保存形式（クラス ID 配列など）は実装の自由。

**iPad クライアントのパレット**（参照クライアント。前景は最大 8 クラスなので `num_classes ≤ 9`）:

| クラス ID | 色 | RGB |
|---|---|---|
| 0 | 白（背景） | `[255, 255, 255]` |
| 1 | 赤 | `[255, 0, 0]` |
| 2 | オレンジ | `[255, 128, 0]` |
| 3 | 黄 | `[255, 255, 0]` |
| 4 | 緑 | `[0, 255, 0]` |
| 5 | シアン | `[0, 255, 255]` |
| 6 | 青 | `[0, 0, 255]` |
| 7 | 紫 | `[128, 0, 255]` |
| 8 | ピンク | `[255, 102, 178]` |

### 5.3 画像（Image）

- 入力画像は JPEG または PNG。サーバー実装は両方を受け付けること。
- ファイル名（`image_id`）は `[A-Za-z0-9_\-\.]+\.(png|jpg|jpeg)` のみ許可（パストラバーサル対策）。

### 5.4 命名規則

- JSON フィールドは **`snake_case`**。クライアントは必要に応じて自分で変換する（例: Swift の `JSONDecoder.keyDecodingStrategy = .convertFromSnakeCase`）。
- 日時フィールドは ISO 8601 文字列（例: `"2026-04-29T12:34:56"`）。
  - **例外**: モデル更新時刻 `model.updated_at`（§7.1）と `X-Model-Updated-At`（§7.13）は UNIX タイムスタンプ秒（数値）。

---

## 6. ステートモデル

### 6.1 画像のプール

```
                                     ┌────────────────────┐
                                     │   fixed            │
                                     │  (固定データ・read- │
                                     │   only・学習用)     │
                                     └────────────────────┘

  ┌────────────────────┐                ┌────────────────────┐
  │   pending          │ PUT /submit/   │   submitted        │
  │  (HITL 前)          │ ─────────────▶ │  (HITL 後・学習用) │
  │  seed label 任意   │  (物理移動)    │  再 submit で上書き │
  └────────────────────┘                └────────────────────┘
```

| プール | 役割 | label の有無 | 学習対象 | 書込 |
|---|---|---|---|---|
| **`pending`** | HITL 前。任意で参考 label（seed）を持てる | optional | × | サーバー設置者のみ |
| **`submitted`** | iPad の `PUT /submit` で確定したもの。再 DL → 編集 → 再 submit 可 | 必須 | ○ | iPad（`PUT /submit` 経由） |
| **`fixed`** | 既に完成済の learning set。HITL 不要 | 必須 | ○ | read-only（サーバー設置者のみ追加） |

**遷移ルール**:
- `pending` の画像に `PUT /submit/{id}` が来ると、画像とマスクを **物理的に `submitted` へ移動** する（コピーではない）。seed は破棄する。
- `submitted` の画像に `PUT /submit/{id}` が来ると、そのマスクを**上書き**する（再編集扱い）。画像は移動しない。
- `fixed` の画像に `PUT /submit/{id}` が来ると **HTTP 409** で拒否する。
- `fixed` への画像追加・削除はサーバー設置者の責務（API では行わない）。

### 6.2 画像のメタフラグ

| フラグ | 意味 |
|---|---|
| `has_seed` | `pool == "pending"` かつ seed label が存在するとき true。pending 以外のプールでは常に false |
| `has_annotation` | 確定 label が存在するとき true。すなわち `pool ∈ {submitted, fixed}` のとき true |

### 6.3 学習データの取得

- 学習ペアは **`submitted` の全 (image, label) ペア + `fixed` の全 (image, label) ペア**。
- `pending` の seed label は **学習に使わない**。

### 6.4 訓練ステートマシン

```
   idle ──POST /train──▶ running ──完了──▶ completed
                            │
                            ├──POST /train/cancel──▶ cancelled
                            │
                            └──エラー──────▶ error
```

- 同時に複数の訓練ジョブを走らせない（`running` 中の `POST /train` は HTTP 409）。
- `POST /train/cancel` は `running` 中のみ受け付ける（それ以外は HTTP 409）。
- 次の `POST /train` で再び `running` に遷移し、各種フィールドはリセットされる。

---

## 7. エンドポイント

すべてベース URL `${BASE_URL}` 配下。プレフィックスなし（`/api/` などは付けない）。
表の「必須」の意味は §2.1 のとおり。

### 7.1 `GET /info` — サーバー情報

レスポンス 200:
```json
{
  "name": "Annotty HIL Server",
  "protocol_version": "1.1",
  "num_classes": 3,
  "class_names": ["background", "brow", "lid"],
  "input_size": 512,
  "counts": { "pending": 2824, "submitted": 0, "fixed": 0, "total": 2824 },
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
| `name` | ✓ | string | サーバー識別名（UI 表示用） |
| `protocol_version` | ✓ | string | 本プロトコルのバージョン（§2） |
| `num_classes` | ✓ | int | クラス数（背景を含む、≥ 2。§5.2） |
| `class_names` | ✓ | string[] | クラス名（長さ = `num_classes`、index 0 は `"background"`） |
| `input_size` | ✓ | int | モデル入力解像度（正方形 1 辺、ピクセル） |
| `counts.pending` | ✓ | int | HITL 前画像数 |
| `counts.submitted` | ✓ | int | HITL 後画像数 |
| `counts.fixed` | ✓ | int | 固定データ画像数 |
| `counts.total` | ✓ | int | 総数（= `pending + submitted + fixed`） |
| `model.best_exists` | ✓ | bool | 推論に使える重みが存在するか |
| `model.coreml_exists` | ✓ | bool | CoreML モデルを配信できるか（§7.13） |
| `model.version` | ✓ | string | モデル世代識別子（不透明文字列。未訓練時は `"0"`） |
| `model.updated_at` | ✓ | number | モデル更新時刻（UNIX 秒。未訓練時は `0.0`） |
| `model.md5` | ✓ | string \| null | CoreML ファイルの MD5。CoreML がなければ `null` |

### 7.2 `POST /config` — palette の登録

クライアントが接続時に、`/info` のクラス定義に自分の palette を付けて送る。

リクエスト（上の `/info` 例に iPad パレットを付けた場合）:
```json
{
  "palette": [[255,255,255],[255,0,0],[255,128,0]],
  "class_names": ["background","brow","lid"],
  "num_classes": 3
}
```

- `class_names` と `num_classes` は、クライアントが同じクラス定義を見ていることの確認に使う。`/info` の値と一致しなければ **409**。
- `palette` の長さが `num_classes` と違う、または成分が 0–255 の範囲外なら **400**。
- 上の検査に通れば、サーバーは palette を**無条件で採用**し、以降のマスク変換（§5.1、§7.6〜§7.8）に使う。

レスポンス 200:
```json
{ "status": "ok" }
```

| フィールド | 必須 | 型 | 意味 |
|---|---|---|---|
| `status` | ✓ | string | `"ok"` |
| `warning` | ✗ | string | 利用者に伝えたい注意。クライアントは UI に表示する |

### 7.3 `GET /images?pool=<pending|submitted|fixed>` — 画像一覧

クエリパラメータ:
- `pool`: 省略時 `pending`。

レスポンス 200:
```json
{
  "pool": "pending",
  "count": 2824,
  "items": ["0_celeb_crop_celeb.jpg", "1_celeb_crop_celeb.jpg", "..."]
}
```

| フィールド | 必須 | 型 | 意味 |
|---|---|---|---|
| `pool` | ✓ | string | 要求されたプール名 |
| `count` | ✓ | int | `items` の長さ |
| `items` | ✓ | string[] | `image_id` の配列（**文字列の配列**。順序に意味なし） |

### 7.4 `GET /images/{image_id}/meta` — 画像メタ情報

レスポンス 200:
```json
{
  "image_id": "0_celeb_crop_celeb.jpg",
  "pool": "pending",
  "has_seed": true,
  "has_annotation": false,
  "bytes": 10844,
  "width": 257,
  "height": 100
}
```

| フィールド | 必須 | 型 | 意味 |
|---|---|---|---|
| `image_id` | ✓ | string | 画像 ID |
| `pool` | ✓ | string | `"pending"` / `"submitted"` / `"fixed"` |
| `has_seed` | ✓ | bool | §6.2 |
| `has_annotation` | ✓ | bool | §6.2 |
| `bytes` | ✓ | int | 画像ファイルのバイト数 |
| `width` | ✓ | int | 画像の幅（ピクセル） |
| `height` | ✓ | int | 画像の高さ（ピクセル） |

404: 画像が存在しない。

### 7.5 `GET /images/{image_id}/download` — 画像本体ダウンロード

レスポンス 200: `image/png` または `image/jpeg`（バイナリ）。
404: 画像が存在しない。

### 7.6 `GET /labels/{image_id}/download` — マスクダウンロード

該当画像のマスクを返す。**`submitted` → `fixed` → `pending`（seed）** の優先順で検索し、最初に見つかったものを返す。

レスポンス 200: `image/png`（§5.1）。
404: マスクがどのプールにも存在しない（正常系。クライアントは無視する）。

### 7.7 `POST /infer/{image_id}` — 推論

サーバーが現行モデルで推論し、マスクを返す。**クエリパラメータなしで動作すること**（§2.1）。

レスポンス 200: `image/png`（§5.1）。
503: モデル未訓練。
404: 画像なし。

### 7.8 `PUT /submit/{image_id}` — マスク提出

リクエスト: `multipart/form-data`。フィールド名 `file`、`Content-Type: image/png`、内容は §5.1 のマスク。
挙動は画像のプールで分岐する（§6.1）。

| 元の `pool` | 挙動 | レスポンス |
|---|---|---|
| `pending` | 画像とマスクを `submitted` へ移動 | 200 `{ "status": "saved", "image_id": ..., "pool": "submitted" }` |
| `submitted` | マスクを上書き | 200 `{ "status": "updated", "image_id": ..., "pool": "submitted" }` |
| `fixed` | 拒否 | 409 `{ "detail": "fixed pool is read-only" }` |
| 不在 | 拒否 | 404 `{ "detail": "image not found" }` |

| フィールド（200） | 必須 | 型 | 意味 |
|---|---|---|---|
| `status` | ✓ | string | `"saved"` / `"updated"` |
| `image_id` | ✗ | string | 画像 ID |
| `pool` | ✗ | string | 提出後のプール |

### 7.9 `GET /next?strategy=<random|uncertainty>` — 次のサンプル

`pending` プールから次の HITL 対象を返す。

クエリパラメータ:
- `strategy`: 省略時 `random`。サーバーは最低でも `random` を提供する。

レスポンス 200（pending が空でない場合）: §7.4 と同じ形。
```json
{
  "image_id": "0_celeb_crop_celeb.jpg",
  "pool": "pending",
  "has_seed": true,
  "has_annotation": false,
  "bytes": 10844,
  "width": 257,
  "height": 100
}
```

レスポンス 200（pending が空の場合）:
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

| フィールド | 必須 | 型 | 意味 |
|---|---|---|---|
| `status` | ✓ | string | `"started"` |
| `max_epochs` | ✗ | int | 実際に使う最大エポック数 |
| `training_pairs` | ✗ | int | 学習ペア数 |
| `message` | ✗ | string | 補足メッセージ |

409: 既に訓練中。
400: 訓練データ不足（`detail` に最小必要枚数を含める）。

### 7.11 `POST /train/cancel` — 訓練キャンセル

レスポンス 200: `{ "status": "cancelling" }`（フィールドの定義は §7.10 と同じ。`status` のみ必須）。
409: 訓練中ではない。

### 7.12 `GET /status` — 訓練ステータス

レスポンス 200（idle 時）:
```json
{ "state": "idle" }
```

レスポンス 200（running 時の例）:
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
| `metric_name` | ✗ | string | `best_metric` の名称（例: `"dice"`） |
| `current_fold` | ✗ | int | クロスバリデーション中の fold インデックス |
| `n_folds` | ✗ | int | fold 総数 |
| `started_at` | ✗ | string | 訓練開始時刻（**ISO 8601 文字列**。数値は不可） |
| `completed_at` | ✗ | string | 訓練終了時刻（**ISO 8601 文字列**。数値は不可） |
| `version` | ✗ | string | 訓練完了後のモデル世代識別子 |
| `error` | ✗ | string | `state == "error"` のときのメッセージ |

### 7.13 `GET /models/latest` — 最新 CoreML モデルダウンロード（任意機能）

CoreML 配信は任意。配信しないサーバーは常に **404** を返し、`/info` の `model.coreml_exists` を `false`、`model.md5` を `null` にする。

レスポンス 200: `application/zip`（CoreML mlpackage を ZIP 化したもの）。
レスポンスヘッダ（200 のとき必須）:
- `X-Model-Version: <string>`
- `X-Model-Md5: <string>`
- `X-Model-Updated-At: <UNIX timestamp seconds>`

404: CoreML モデルがない。

### 7.14 `POST /models/convert` — CoreML 変換（任意機能）

サーバー側で最新の重みから CoreML mlpackage を生成する。CoreML を配信しないサーバーは **501** を返す。

レスポンス 200:
```json
{ "status": "converted", "version": "20260429-123456", "md5": "abc123..." }
```

503: 変換可能な重みが存在しない。

---

## 8. クライアント実装ガイド

1. 接続時に `GET /info` を呼び、MAJOR を確認する（§2）。
2. 続けて `POST /config` で、`/info` のクラス定義に自分の palette を付けて送る（§7.2）。失敗しても接続は続けてよい（サーバーは既定 palette を使う）。
3. マスクの送受信は §5.1 に従う。
4. エラーは `detail` を UI に表示する（§4）。
5. 画像を取り込むときは、プールに関係なく `GET /labels/{id}/download` も呼ぶ。404 は無視する（pending の seed を取りこぼさないため）。

## 9. サーバー実装ガイド

1. §2.1 の互換性ルールを守り、適合テスト（§10）を全項目 PASS させる。
2. クラス定義と palette の扱いは §5.2・§7.2 に従う。
3. マスクの変換は §5.1 に従う。
4. エラーは `{"detail": "..."}` 形式に統一する（FastAPI なら `HTTPException(status_code=..., detail="...")`）。
5. 画像 ID を検証する（§5.3）。
6. 3 プールを物理的に分離し（例: `pending/{images,labels}/` 等）、遷移ルール（§6.1）と学習データの範囲（§6.3）を守る。
7. iPad 以外のクライアントも想定するなら CORS ヘッダを付ける。
8. （推奨）返すマスクを **表示解像度** で送る。iPad はマスクを内部で拡大して表示するが、クラス ID は最近傍でしか拡大できず、境界がギザギザになる。サーバーが事前に滑らかに拡大しておくと改善する。
   - 表示解像度: `scale = min(2.0, 4096 / max(W, H))`、`(int(W * scale), int(H * scale))`（W, H は入力画像のサイズ）
   - 手順: 最近傍で表示解像度に拡大 → クラスごとの 2 値マスクに Gaussian（σ ≈ 1.5）→ クラス間で argmax。クラス ID 配列に bilinear / bicubic を直接かけてはいけない（存在しないクラス ID が生まれる）。
   - 実装例: [`server/scripts/smooth_labels.py`](../server/scripts/smooth_labels.py)

---

## 10. 動作確認

### 適合テスト

```bash
python protocol/conformance_test.py "$BASE" --api-key "$KEY"
# 提出も検査する場合（指定した画像は submitted へ移動する）
python protocol/conformance_test.py "$BASE" --api-key "$KEY" --submit <image_id>
```

### curl 例

```bash
BASE="https://example.trycloudflare.com"
H="X-API-Key: $KEY"

curl -s -H "$H" "$BASE/info" | jq

# palette 登録（class_names / num_classes は /info の値と同じにする）
curl -s -H "$H" -X POST "$BASE/config" \
  -H "Content-Type: application/json" \
  -d '{"palette": [[255,255,255],[255,0,0],[255,128,0]],
       "class_names": ["background","brow","lid"], "num_classes": 3}' | jq

curl -s -H "$H" "$BASE/images?pool=pending" | jq '.count'
curl -s -H "$H" "$BASE/next" | jq
curl -s -H "$H" -X PUT "$BASE/submit/0_celeb_crop_celeb.jpg" -F "file=@mask.png;type=image/png" | jq
curl -s -H "$H" -X POST "$BASE/train?max_epochs=50" | jq
curl -s -H "$H" "$BASE/status" | jq
curl -sI -H "$H" "$BASE/models/latest"
```

---

## 11. 参照実装

- **クライアント (iPad / Swift)**: [`AnnottyHIL/Services/HIL/HILServerClient.swift`](../AnnottyHIL/Services/HIL/HILServerClient.swift)。レスポンスの型は §7 の表と一致している。
- **サーバー (Python / FastAPI)**: [`server/`](../server/)。適合テストを全項目 PASS する。
- **適合テスト**: [`conformance_test.py`](conformance_test.py)

本仕様書と実装の間に食い違いがある場合は、**本仕様書を優先**する。
