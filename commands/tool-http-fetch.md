---
description: http_fetch MCPツールの使い方ガイド
---
# http_fetch

## 用途
Fetch a URL and return raw response (no AI processing).

## 引数
- `url` (str): The URL to fetch (required). [必須]
- `method` (str): HTTP method — GET or POST (default: GET). [省略可, default=GET]
- `headers` (str): Optional headers as "Key: Value" lines (newline-separated). [省略可, default=]
- `body` (str): Optional request body (for POST). [省略可, default=]

## 使用場面
- JSON APIのレスポンスを取得する時
- ステータスチェックやraw HTMLの取得
- AI処理なしの生HTTPレスポンスが必要な時

## 使用すべきでない場面
- AI処理が必要な場合（WebFetchを使用）
- URLが空文字の状態
- 内部ネットワークアドレスへのアクセス

## 制約・注意点
- 必須引数: url
- GET/POSTのみ対応
- AI処理なし（生レスポンスを返す）
- Hook guard: guard-http-fetch（required_args）
