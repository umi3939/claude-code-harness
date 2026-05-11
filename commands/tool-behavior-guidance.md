---
description: behavior_guidance MCPツールの使い方ガイド
---
# behavior_guidance

## 用途
Generate behavioral guidance from emotion state and Gap Analysis documents.

## MCP function
mcp__self-observation__behavior_guidance

## 引数
- `memory_dir` (string, optional): Directory containing emotion_state.json. Default: auto-resolved memory dir
- `docs_dir` (string, optional): Directory containing gap_analysis_*.md files. Default: project docs/

## 使用場面
- UserPromptSubmit時のContext Injection（skill_executor経由で自動実行）
- 現在の感情状態に基づく行動指針を手動で確認したい時
- Gap Analysisの優先項目と感情状態の組み合わせから推奨アクションを得たい時

## 使用すべきでない場面
- 感情状態の変更（このツールはREAD-ONLY）
- Gap Analysis自体の更新

## 関連ツール
- emotion_get (感情状態の直接確認)
- activation_surface (注意すべき事項の確認)
- psyche_drive (自動状態更新)

## 制約・注意点
- READ-ONLY（感情状態やGap Analysisを変更しない）
- パスにはパストラバーサル防止が適用される
- 感情飽和時はガイダンス生成を抑制する（空文字列を返す）
- skill_executor経由の場合: Context Injectionの[Behavior Guidance]セクションとして出力
