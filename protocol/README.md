# Annotty HIL サーバー実装ガイド

Annotty HIL iPad アプリと通信するサーバーを実装するためのフォルダです。

1. [`protocol.md`](protocol.md) を読む。これが唯一の仕様（Single Source of Truth）。
2. 仕様どおりにサーバーを実装する。参照実装 [`server/`](../server/) を参考にしてよいが、食い違いがあれば仕様を優先する。
3. 適合テストを実行し、**ALL PASS になるまで直す**（Python 標準ライブラリのみで動く）。

```bash
python conformance_test.py <BASE_URL> [--api-key KEY]
```
